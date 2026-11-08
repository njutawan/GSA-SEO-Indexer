"""Connection pooling and proxy support for the asyncio transport (P12/P13).

Two features, one file, because they share the fixture: a keep-alive HTTP
server, a forwarding proxy that speaks the absolute request line, and a proxy
that can tunnel ``CONNECT`` so an HTTPS request can go through it.

The properties that matter:

* with ``reuse_connections`` on, N requests to one host use **one** TCP
  connection, and a connection the server already closed is retried once;
* a plain-HTTP request through a proxy carries the absolute URL in the request
  line, an HTTPS request goes through a ``CONNECT`` tunnel that then carries
  the TLS handshake (verify-first, with the same per-host fallback);
* a proxy that fails is retired and does not mark the target host dead.

Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer import httpclient  # noqa: E402
from gsa_seo_indexer.engine import Indexer, PingTarget, Settings  # noqa: E402

TARGET = "http://t.example/"
PAGE = f'<html><a href="{TARGET}">t.example</a></html>'.encode()


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
class _KeepAliveHandler(BaseHTTPRequestHandler):
    """Answers every request, and records which connection carried it."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server API
        server = self.server
        with server.lock:
            server.ports.append(self.client_address[1])
            server.requests += 1
            hit = server.requests
        if server.close_after and hit <= server.close_after:
            # Keep-alive header, but the socket is closed anyway: this is what
            # a server with a short idle timeout does to a pooled connection.
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(PAGE)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(PAGE)
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.do_GET()

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def keepalive_server(close_after: int = 0, tls: tuple[str, str] | None = None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _KeepAliveHandler)
    server.daemon_threads = True
    server.request_queue_size = 128
    server.ports = []
    server.requests = 0
    server.lock = threading.Lock()
    server.close_after = close_after
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


class _ProxyHandler(BaseHTTPRequestHandler):
    """A tiny forwarding proxy.

    ``do_GET`` sees the absolute request line a proxy is supposed to receive;
    ``do_CONNECT`` opens a tunnel and pipes bytes both ways.
    """

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server API
        with self.server.lock:
            self.server.seen.append({"method": "GET", "path": self.path})
            self.server.connections.add(self.client_address[1])
        # Answer locally: the test is about what the proxy *receives*, not
        # about real forwarding.
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(self.server.body)))
        self.end_headers()
        self.wfile.write(self.server.body)

    def do_CONNECT(self):  # noqa: N802 - http.server API
        with self.server.lock:
            self.server.seen.append({"method": "CONNECT", "path": self.path})
        try:
            host, _, port = self.path.partition(":")
            upstream = socket.create_connection((host, int(port)), timeout=5)
        except OSError as exc:
            self.send_error(502, f"tidak bisa menyambung: {exc}")
            return
        self.send_response(200, "Connection established")
        self.end_headers()
        self.wfile.flush()

        def pump(src, dst) -> None:
            try:
                while True:
                    chunk = src.recv(65536)
                    if not chunk:
                        break
                    dst.sendall(chunk)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    dst.shutdown(socket.SHUT_WR)

        upstream.settimeout(10)
        self.connection.settimeout(10)
        thread = threading.Thread(target=pump, args=(self.connection, upstream), daemon=True)
        thread.start()
        pump(upstream, self.connection)
        thread.join(timeout=5)
        upstream.close()
        self.close_connection = True

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def proxy_server(body: bytes = PAGE):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
    server.daemon_threads = True
    server.seen = []
    server.connections = set()
    server.lock = threading.Lock()
    server.body = body
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def make_indexer(threads: int = 4, **kwargs) -> Indexer:
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


# --------------------------------------------------------------------------
# Connection pooling
# --------------------------------------------------------------------------
class AsyncPoolTest(unittest.TestCase):
    def test_pooling_reuses_one_connection(self) -> None:
        with keepalive_server() as (port, server):
            indexer = make_indexer(transport="asyncio", reuse_connections=True)
            try:
                results = indexer.run(
                    [TARGET], [PingTarget(url=f"http://127.0.0.1:{port}/")]
                )
                # run() owns the transport, so ask the server, not the pool
            finally:
                indexer.close()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "submitted")

    def test_sequential_requests_share_a_connection(self) -> None:
        """Three targets on one host, one worker: one connection must do."""
        with keepalive_server() as (port, server):
            indexer = make_indexer(threads=1, transport="asyncio", reuse_connections=True)
            try:
                targets = [
                    PingTarget(url=f"http://127.0.0.1:{port}/p{i}") for i in range(3)
                ]
                results = indexer.run([TARGET], targets)
            finally:
                indexer.close()

        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.status == "submitted" for r in results))
        self.assertEqual(len(set(server.ports)), 1, f"ports: {server.ports}")

    def test_without_pooling_every_request_opens_its_own(self) -> None:
        with keepalive_server() as (port, server):
            indexer = make_indexer(threads=1, transport="asyncio")
            try:
                targets = [
                    PingTarget(url=f"http://127.0.0.1:{port}/p{i}") for i in range(3)
                ]
                indexer.run([TARGET], targets)
            finally:
                indexer.close()

        self.assertEqual(len(set(server.ports)), 3)

    def test_a_connection_closed_by_the_server_is_retried_once(self) -> None:
        """A stale keep-alive socket must cost one retry, not one lost row."""
        with keepalive_server(close_after=1) as (port, server):
            indexer = make_indexer(threads=1, transport="asyncio", reuse_connections=True)
            try:
                targets = [
                    PingTarget(url=f"http://127.0.0.1:{port}/p{i}") for i in range(3)
                ]
                results = indexer.run([TARGET], targets)
            finally:
                indexer.close()

        self.assertEqual(len(results), 3)
        self.assertTrue(
            all(r.status == "submitted" for r in results),
            [r.error for r in results],
        )
        self.assertGreaterEqual(len(set(server.ports)), 2, "the retry opened a new socket")

    def test_the_pool_is_bounded_per_host(self) -> None:
        from gsa_seo_indexer.asyncio_transport import _Pool

        pool = _Pool(max_per_host=2, max_total=10)

        class _Writer:
            def close(self):
                pass

            def is_closing(self):
                return False

        class _Conn:
            pass

        conns = []
        for _ in range(5):
            conn = _Conn()
            conn.writer = _Writer()
            conn.close = lambda c=conn: None
            conn.usable = True  # type: ignore[attr-defined]
            conns.append(conn)
            pool.give(("http", "h", 80, ""), conn)
        self.assertEqual(pool.count(), 2, "max_per_host must cap the free list")
        pool.close()

    def test_post_requests_reuse_the_connection_too(self) -> None:
        with keepalive_server() as (port, server):
            indexer = make_indexer(threads=1, transport="asyncio", reuse_connections=True)
            try:
                targets = [
                    PingTarget(url=f"http://127.0.0.1:{port}/post{i}", mode="post")
                    for i in range(2)
                ]
                results = indexer.run([TARGET], targets)
            finally:
                indexer.close()

        self.assertTrue(all(r.status == "submitted" for r in results), [r.error for r in results])
        self.assertEqual(len(set(server.ports)), 1)


# --------------------------------------------------------------------------
# Proxies
# --------------------------------------------------------------------------
class AsyncProxyTest(unittest.TestCase):
    def test_plain_http_goes_through_the_proxy_in_absolute_form(self) -> None:
        with proxy_server() as (port, proxy):
            indexer = make_indexer(
                transport="asyncio", use_proxy=True, proxy_list=[f"127.0.0.1:{port}"]
            )
            try:
                results = indexer.run(
                    [TARGET], [PingTarget(url="http://site.example/page?u=", mode="query")]
                )
            finally:
                indexer.close()

        self.assertEqual(results[0].status, "submitted", results[0].error)
        self.assertEqual(len(proxy.seen), 1)
        self.assertEqual(proxy.seen[0]["method"], "GET")
        # the proxy must receive the absolute URL, not the origin form
        self.assertTrue(proxy.seen[0]["path"].startswith("http://site.example/page"))

    def test_https_goes_through_a_connect_tunnel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            if not shutil.which("openssl"):
                self.skipTest("openssl not available")
            folder = Path(tmp)
            cert, key = folder / "c.pem", folder / "k.pem"
            subprocess.run(
                [
                    "openssl", "req", "-x509", "-newkey", "rsa:2048",
                    "-keyout", str(key), "-out", str(cert),
                    "-days", "1", "-nodes", "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1",
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            httpclient._unverified_hosts.clear()
            with keepalive_server(tls=(str(cert), str(key))) as (target_port, _server):
                with proxy_server() as (proxy_port, proxy):
                    indexer = make_indexer(
                        transport="asyncio",
                        use_proxy=True,
                        proxy_list=[f"http://127.0.0.1:{proxy_port}"],
                    )
                    try:
                        results = indexer.run(
                            [TARGET],
                            [PingTarget(url=f"https://127.0.0.1:{target_port}/")],
                        )
                    finally:
                        indexer.close()
            httpclient._unverified_hosts.clear()

        self.assertEqual(results[0].status, "submitted", results[0].error)
        self.assertTrue(results[0].indexable)
        self.assertEqual(proxy.seen[0]["method"], "CONNECT")
        self.assertEqual(proxy.seen[0]["path"], f"127.0.0.1:{target_port}")

    def test_a_dead_proxy_fails_the_row_but_not_the_host_cache(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        indexer = make_indexer(
            transport="asyncio", use_proxy=True, proxy_list=[f"127.0.0.1:{dead_port}"]
        )
        try:
            results = indexer.run([TARGET], [PingTarget(url="http://site.example/")])
            dead = indexer.host_cache.is_dead("site.example")
            retired = list(indexer._retired)
        finally:
            indexer.close()

        self.assertEqual(results[0].status, "failed")
        self.assertFalse(dead, "a dead proxy says nothing about the target host")
        self.assertTrue(retired, "the failing proxy must be retired")

    def test_proxy_addresses_are_validated(self) -> None:
        from gsa_seo_indexer.asyncio_transport import AsyncTransport

        self.assertEqual(AsyncTransport.parse_proxy("127.0.0.1:8080"), ("127.0.0.1", 8080))
        self.assertEqual(
            AsyncTransport.parse_proxy("http://proxy.example:3128"), ("proxy.example", 3128)
        )
        with self.assertRaises(ValueError):
            AsyncTransport.parse_proxy("socks5://127.0.0.1:1080")
        with self.assertRaises(ValueError):
            AsyncTransport.parse_proxy("")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
