"""Tests for the asyncio transport (P10).

The event loop must not be a second implementation of the rules: it has to
produce the same rows, the same stats and the same decisions as the thread
transport, with the same gzip, POST, redirect, dead-host and retry behaviour.
These tests compare the two transports against the same local server rather
than trusting the code to look similar.

Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import gzip
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer import httpclient  # noqa: E402
from gsa_seo_indexer.engine import Indexer, PingTarget, Settings  # noqa: E402

TARGET = "http://t.example/"
PAGE = f'<html><a href="{TARGET}">t.example</a></html>'.encode()


class _UniversalHandler(BaseHTTPRequestHandler):
    """One page, several shapes: redirect, chunked, slow, gzip, POST."""

    protocol_version = "HTTP/1.1"

    def _record(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        with self.server.lock:
            self.server.seen.append(
                {"method": self.command, "path": self.path, "body": body}
            )
            self.server.in_flight += 1
            self.server.peak = max(self.server.peak, self.server.in_flight)
        return body

    def _plain(self, code: int = 200, body: bytes = PAGE) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self) -> None:
        self._record()
        path = self.path
        try:
            if self.server.delay:
                time.sleep(self.server.delay)
            if path.startswith("/slow"):
                self._plain()
            elif path.startswith("/301"):
                self.send_response(301)
                self.send_header("Location", "/page")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path.startswith("/303"):
                self.send_response(303)
                self.send_header("Location", "/page")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path.startswith("/chunked"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for piece in (PAGE[:20], PAGE[20:40], PAGE[40:]):
                    self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            elif path.startswith("/eof"):
                # no Content-Length, no chunking: the body ends at EOF
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.close_connection = True
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(PAGE)
            elif path.startswith("/gzip"):
                blob = gzip.compress(PAGE)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
            elif path.startswith("/500"):
                self._plain(500, b"<html>error</html>")
            elif path.startswith("/404"):
                self._plain(404, b"<html>missing</html>")
            else:
                self._plain()
        finally:
            with self.server.lock:
                self.server.in_flight -= 1

    def do_GET(self):  # noqa: N802 - http.server API
        self._dispatch()

    def do_POST(self):  # noqa: N802 - http.server API
        self._dispatch()

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def universal_server(delay: float = 0.0, tls: tuple[str, str] | None = None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UniversalHandler)
    server.daemon_threads = True
    server.request_queue_size = 128
    server.seen = []
    server.lock = threading.Lock()
    server.in_flight = 0
    server.peak = 0
    server.delay = delay
    if tls:
        cert, key = tls
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def make_indexer(threads: int = 8, **kwargs) -> Indexer:
    settings = Settings(
        threads=threads,
        persist_host_cache=False,
        random_delay=False,
        skip_dead_hosts=False,
        dns_cache=False,
        check_url=False,
        **kwargs,
    )
    return Indexer(settings)


def rows(results) -> list[tuple]:
    """The comparable part of a result list (elapsed time is not stable)."""
    return sorted(
        (r.target, r.url, r.status, r.code, r.indexable, r.listed) for r in results
    )


class ParityTest(unittest.TestCase):
    def test_both_transports_produce_the_same_report(self) -> None:
        urls = ["http://a.example/", "http://b.example/"]

        with universal_server() as (port, _server):
            threads = make_indexer()
            try:
                custom = [
                    PingTarget(url=f"http://127.0.0.1:{port}/", mode="query"),
                ]
                thread_rows = threads.run(urls, custom)
                thread_stats = (threads.stats.submitted, threads.stats.failed, threads.stats.indexable)
            finally:
                threads.close()

            loop = make_indexer(transport="asyncio")
            try:
                async_rows = loop.run(urls, custom)
                async_stats = (loop.stats.submitted, loop.stats.failed, loop.stats.indexable)
            finally:
                loop.close()

        self.assertEqual(rows(thread_rows), rows(async_rows))
        self.assertEqual(thread_stats, async_stats)
        self.assertEqual(len(rows(async_rows)), 2)
        self.assertTrue(all(r.status == "submitted" for r in async_rows))

    def test_post_targets_send_the_body_over_the_loop(self) -> None:
        with universal_server() as (port, server):
            indexer = make_indexer(transport="asyncio")
            try:
                results = indexer.run(
                    [TARGET], [PingTarget(url=f"http://127.0.0.1:{port}/ping", mode="post")]
                )
            finally:
                indexer.close()

        self.assertTrue(all(r.status == "submitted" for r in results))
        self.assertEqual(server.seen[0]["method"], "POST")
        self.assertEqual(server.seen[0]["body"], b"url=http%3A%2F%2Ft.example%2F")

    def test_gzip_is_decoded(self) -> None:
        with universal_server() as (port, _server):
            indexer = make_indexer(transport="asyncio", accept_gzip=True)
            try:
                results = indexer.run([TARGET], [PingTarget(url=f"http://127.0.0.1:{port}/gzip")])
            finally:
                indexer.close()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].indexable, results[0].error)

    def test_redirects_are_followed_and_303_becomes_a_get(self) -> None:
        with universal_server() as (port, server):
            indexer = make_indexer(transport="asyncio")
            try:
                results = indexer.run(
                    [TARGET],
                    [
                        PingTarget(url=f"http://127.0.0.1:{port}/301"),
                        PingTarget(url=f"http://127.0.0.1:{port}/303", mode="post"),
                    ],
                )
            finally:
                indexer.close()

        self.assertTrue(all(r.status == "submitted" for r in results), [r.error for r in results])
        posts = [s for s in server.seen if s["method"] == "POST"]
        followed = [s for s in server.seen if s["path"].startswith("/page")]
        self.assertEqual(len(posts), 1, "only the /303 request itself is a POST")
        self.assertEqual(posts[0]["path"], "/303")
        # both follow-ups (301 and 303) land on /page, and both must be GET:
        # a 303 means "take the result with GET", the body is not replayed.
        self.assertEqual(len(followed), 2)
        self.assertTrue(all(s["method"] == "GET" for s in followed))
        self.assertTrue(all(s["body"] == b"" for s in followed))

    def test_chunked_and_eof_bodies(self) -> None:
        with universal_server() as (port, _server):
            indexer = make_indexer(transport="asyncio")
            try:
                results = indexer.run(
                    [TARGET],
                    [
                        PingTarget(url=f"http://127.0.0.1:{port}/chunked"),
                        PingTarget(url=f"http://127.0.0.1:{port}/eof"),
                    ],
                )
            finally:
                indexer.close()

        self.assertTrue(all(r.indexable for r in results), [r.error for r in results])


class AsyncBehaviourTest(unittest.TestCase):
    def test_error_statuses_are_failures(self) -> None:
        with universal_server() as (port, _server):
            indexer = make_indexer(transport="asyncio")
            try:
                results = indexer.run(
                    [TARGET],
                    [
                        PingTarget(url=f"http://127.0.0.1:{port}/500"),
                        PingTarget(url=f"http://127.0.0.1:{port}/404"),
                    ],
                )
            finally:
                indexer.close()

        self.assertEqual({r.code for r in results}, {500, 404})
        self.assertTrue(all(r.status == "failed" for r in results))

    def test_a_dead_host_is_skipped_without_a_request(self) -> None:
        with universal_server() as (port, server):
            indexer = make_indexer(transport="asyncio")
            indexer.host_cache.record_failure("127.0.0.1", "boom")
            indexer.settings.skip_dead_hosts = True
            try:
                results = indexer.run([TARGET], [PingTarget(url=f"http://127.0.0.1:{port}/")])
            finally:
                indexer.close()

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(server.seen, [], "the request must never happen")

    def test_the_connection_cap_is_respected(self) -> None:
        with universal_server(delay=0.05) as (port, server):
            indexer = make_indexer(transport="asyncio", threads=4)
            try:
                targets = [
                    PingTarget(url=f"http://127.0.0.1:{port}/slow?i={i}") for i in range(20)
                ]
                indexer.run([TARGET], targets)
            finally:
                indexer.close()

        self.assertLessEqual(server.peak, 4, "the semaphore must cap in-flight requests")
        self.assertGreaterEqual(server.peak, 2, "the loop should actually run in parallel")

    def test_stop_is_responsive(self) -> None:
        with universal_server(delay=0.05) as (port, _server):
            indexer = make_indexer(transport="asyncio", threads=4)
            targets = [
                PingTarget(url=f"http://127.0.0.1:{port}/slow?i={i}") for i in range(60)
            ]
            threading.Timer(0.15, indexer.stop).start()
            started = time.perf_counter()
            try:
                results = indexer.run([TARGET], targets)
            finally:
                indexer.close()
            elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 1.5, "Stop must not wait for the whole list")
        self.assertLess(len(results), 60, "the run stopped early")

    def test_a_worker_that_raises_is_recorded(self) -> None:
        with universal_server() as (port, _server):
            indexer = make_indexer(transport="asyncio")
            target = PingTarget(url=f"http://127.0.0.1:{port}/")
            original = indexer._headers

            def boom():
                raise RuntimeError("header bug")

            indexer._headers = boom  # type: ignore[assignment]
            try:
                results = indexer.run([TARGET], [target])
            finally:
                indexer._headers = original  # type: ignore[assignment]
                indexer.close()

        self.assertEqual(results[0].status, "failed")
        self.assertIn("RuntimeError", results[0].error)


class AsyncTlsTest(unittest.TestCase):
    """HTTPS with a self-signed certificate: verify first, then fall back."""

    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("openssl"):
            raise unittest.SkipTest("openssl not available")
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        cls.cert = folder / "cert.pem"
        cls.key = folder / "key.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(cls.key), "-out", str(cls.cert),
                "-days", "1", "-nodes", "-subj", "/CN=127.0.0.1",
                "-addext", "subjectAltName=IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def setUp(self) -> None:
        httpclient._unverified_hosts.clear()

    def tearDown(self) -> None:
        httpclient._unverified_hosts.clear()

    def test_https_falls_back_to_an_unverified_context(self) -> None:
        with universal_server(tls=(str(self.cert), str(self.key))) as (port, _server):
            indexer = make_indexer(transport="asyncio")
            try:
                results = indexer.run(
                    [TARGET], [PingTarget(url=f"https://127.0.0.1:{port}/")]
                )
            finally:
                indexer.close()

        self.assertEqual(results[0].status, "submitted", results[0].error)
        self.assertTrue(results[0].indexable)
        self.assertTrue(
            httpclient.needs_ssl_fallback("127.0.0.1"),
            "the failed verification must be remembered for the next run",
        )


class AsyncBatchTest(unittest.TestCase):
    def test_batching_falls_back_to_threads(self) -> None:
        with universal_server() as (port, _server):
            indexer = make_indexer(transport="asyncio")
            try:
                results = indexer.run(
                    [TARGET, "http://u.example/"],
                    [PingTarget(url=f"http://127.0.0.1:{port}/", mode="query")],
                    batch_size=2,
                )
            finally:
                indexer.close()

        self.assertEqual(indexer.transport_fallback, "batch -> threads")
        self.assertEqual(len(results), 2)

    def test_proxy_runs_stay_on_the_loop(self) -> None:
        """A proxy must not silently push the run back to the thread pool."""
        indexer = make_indexer(transport="asyncio", use_proxy=True, proxy_list=["127.0.0.1:9"])
        try:
            results = indexer.run(
                [TARGET], [PingTarget(url="http://127.0.0.1:1/")], reset=True
            )
        finally:
            indexer.close()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "failed")
        self.assertEqual(indexer.transport_fallback, "", "proxy is supported on the loop now")


class AsyncSocketSanityTest(unittest.TestCase):
    def test_a_closed_port_is_a_failure_not_a_crash(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        indexer = make_indexer(transport="asyncio")
        try:
            results = indexer.run([TARGET], [PingTarget(url=f"http://127.0.0.1:{port}/")])
        finally:
            indexer.close()
        self.assertEqual(results[0].status, "failed")
        self.assertTrue(results[0].error)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
