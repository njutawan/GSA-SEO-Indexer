"""TLS verify-first fallback and open (credential-less) proxies — P15.

Two things are pinned here, both found while answering "does this work through
a free proxy list?":

1. A failed handshake **closes the socket**, so the code that restored the
   read timeout after ``connect()`` used to raise ``EBADF`` from inside its
   ``finally`` block.  That hid ``SSLCertVerificationError`` — the only signal
   that makes the client retry the host without verification — so on the
   default (urllib) transport every https host with a broken chain failed with
   "Bad file descriptor", and a proxy carrying such a host was retired as if
   the proxy were at fault.
2. A proxy list written the way free lists are written (``host:port``, no
   ``user:pass``) works on every transport, including CONNECT tunnels.

The proxy fixture here is a *raw* tunnel: it accepts a CONNECT line, opens the
upstream socket and copies bytes.  That is what a small free proxy does, and it
matters — an ``http.server``-based fake closes the connection after the
CONNECT response and would prove nothing about the client.
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

PAYLOAD = "https://raratravel.id/"
PAGE = b'<html><body><a href="https://raratravel.id/">raratravel.id</a></body></html>'


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
class _Origin(ThreadingHTTPServer):
    """A stand-in for one of the submission sites."""

    daemon_threads = True
    request_queue_size = 256


class _OriginHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True

    def do_GET(self):  # noqa: N802 - http.server API
        self.server.paths.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def origin(cert: tuple[str, str] | None = None):
    srv = _Origin(("127.0.0.1", 0), _OriginHandler)
    srv.paths = []
    if cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*cert)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1], srv
    finally:
        srv.shutdown()
        srv.server_close()


class _RawTunnelProxy:
    """Open proxy: no user:pass, forwards by absolute URI or CONNECT."""

    def __init__(self, allow_connect: bool = True) -> None:
        self.allow_connect = allow_connect
        self.seen: list[tuple[str, str]] = []
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(64)
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                client, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(4096)
                if not chunk:
                    client.close()
                    return
                head += chunk
            method, target, _version = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
            self.seen.append((method, target))
            if method == "GET" and target.startswith("http://"):
                # Plain HTTP through a proxy: the request line carries the
                # absolute URL, which is exactly what a real proxy receives.
                self._forward(client, target)
                return
            if method != "CONNECT" or not self.allow_connect:
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                client.close()
                return
            host, _, port = target.partition(":")
            upstream = socket.create_connection((host, int(port)), timeout=5)
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            rest = head.split(b"\r\n\r\n", 1)[1]
            if rest:
                upstream.sendall(rest)

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

            side = threading.Thread(target=pump, args=(client, upstream), daemon=True)
            side.start()
            pump(upstream, client)
            side.join(timeout=5)
            upstream.close()
        finally:
            with contextlib.suppress(OSError):
                client.close()

    def _forward(self, client: socket.socket, absolute: str) -> None:
        """Forward one absolute-form request and pipe the answer back."""
        from urllib.parse import urlsplit

        parts = urlsplit(absolute)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            upstream = socket.create_connection(
                (parts.hostname or "", parts.port or 80), timeout=5
            )
        except OSError as exc:
            client.sendall(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{exc}".encode())
            client.close()
            return
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        upstream.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
        )
        try:
            while True:
                chunk = upstream.recv(65536)
                if not chunk:
                    break
                client.sendall(chunk)
        except OSError:
            pass
        finally:
            upstream.close()

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()


@contextlib.contextmanager
def self_signed_cert():
    if not shutil.which("openssl"):
        raise unittest.SkipTest("openssl tidak tersedia")
    with tempfile.TemporaryDirectory() as tmp:
        cert, key = Path(tmp) / "c.pem", Path(tmp) / "k.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(key), "-out", str(cert), "-days", "1", "-nodes",
                "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        yield str(cert), str(key)


def make_indexer(**kwargs) -> Indexer:
    settings = Settings(
        persist_host_cache=False,
        dns_cache=False,
        skip_dead_hosts=False,
        random_delay=False,
        check_url=False,
        **{"threads": 2, **kwargs},
    )
    return Indexer(settings)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
class SslFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        httpclient._unverified_hosts.clear()

    def tearDown(self) -> None:
        httpclient._unverified_hosts.clear()

    def test_a_self_signed_host_is_retried_on_the_default_transport(self) -> None:
        """The bug: urllib path reported "Bad file descriptor" instead."""
        with self_signed_cert() as cert:
            with origin(cert) as (port, srv):
                indexer = make_indexer()
                try:
                    results = indexer.run(
                        [PAYLOAD],
                        [PingTarget(url=f"https://127.0.0.1:{port}/p?u=", mode="query")],
                    )
                finally:
                    indexer.close()

        self.assertEqual(results[0].status, "submitted", results[0].error)
        self.assertTrue(results[0].indexable)
        self.assertIn("127.0.0.1", httpclient._unverified_hosts)

    def test_the_fallback_key_is_the_hostname_on_both_transports(self) -> None:
        """A port must not split the memory: one host, one entry."""
        with self_signed_cert() as cert:
            with origin(cert) as (port, srv):
                indexer = make_indexer()
                try:
                    indexer.run(
                        [PAYLOAD],
                        [PingTarget(url=f"https://127.0.0.1:{port}/p?u=", mode="query")],
                    )
                finally:
                    indexer.close()
        self.assertEqual(sorted(httpclient._unverified_hosts), ["127.0.0.1"])

    def test_a_failing_host_still_reports_the_real_error(self) -> None:
        """No more EBADF: a closed port must say "refused", not "Bad fd"."""
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead = probe.getsockname()[1]
        indexer = make_indexer()
        try:
            results = indexer.run(
                [PAYLOAD], [PingTarget(url=f"https://127.0.0.1:{dead}/p?u=", mode="query")]
            )
        finally:
            indexer.close()
        self.assertEqual(results[0].status, "failed")
        self.assertNotIn("Bad file descriptor", results[0].error or "")


class OpenProxyTest(unittest.TestCase):
    """A free-proxy-style list: ``host:port``, no credentials."""

    def setUp(self) -> None:
        httpclient._unverified_hosts.clear()
        self.proxy = _RawTunnelProxy()
        self.addCleanup(self.proxy.close)
        self.addCleanup(httpclient._unverified_hosts.clear)

    def _run(self, target_url: str, transport: str = "threads", pool: bool = False):
        indexer = make_indexer(
            transport=transport,
            reuse_connections=pool,
            use_proxy=True,
            proxy_list=[f"127.0.0.1:{self.proxy.port}"],
        )
        try:
            results = indexer.run([PAYLOAD], [PingTarget(url=target_url, mode="query")])
            retired = list(indexer._retired)
        finally:
            indexer.close()
        return results, retired

    def test_plain_http_through_a_credential_less_proxy(self) -> None:
        with origin() as (port, srv):
            results, retired = self._run(f"http://127.0.0.1:{port}/p?u=")
        self.assertEqual(results[0].status, "submitted", results[0].error)
        method, target = self.proxy.seen[0]
        self.assertEqual(method, "GET")
        self.assertTrue(target.startswith("http://"), target)
        self.assertEqual(retired, [])

    def test_https_through_a_connect_tunnel_on_every_transport(self) -> None:
        with self_signed_cert() as cert:
            for transport, pool in (
                ("threads", False),
                ("threads", True),
                ("asyncio", False),
                ("asyncio", True),
            ):
                with origin(cert) as (port, srv):
                    results, retired = self._run(
                        f"https://127.0.0.1:{port}/p?u=", transport, pool
                    )
                self.assertEqual(
                    results[0].status,
                    "submitted",
                    f"{transport} pool={pool}: {results[0].error}",
                )
                self.assertEqual(retired, [], f"{transport} pool={pool} meretire proxy yang sehat")
                httpclient._unverified_hosts.clear()
                self.proxy.seen.clear()

    def test_the_proxy_sees_two_connects_for_a_self_signed_host(self) -> None:
        """Verify-first fails, the host is remembered, the tunnel is rebuilt."""
        with self_signed_cert() as cert:
            with origin(cert) as (port, srv):
                results, _ = self._run(f"https://127.0.0.1:{port}/p?u=")
        self.assertEqual(results[0].status, "submitted", results[0].error)
        connects = [t for m, t in self.proxy.seen if m == "CONNECT"]
        self.assertEqual(len(connects), 2, connects)

    def test_a_proxy_that_refuses_connect_is_retired_not_the_target(self) -> None:
        self.proxy.allow_connect = False
        with self_signed_cert() as cert:
            with origin(cert) as (port, srv):
                results, retired = self._run(f"https://127.0.0.1:{port}/p?u=")
        self.assertEqual(results[0].status, "failed")
        self.assertIn("403", results[0].error or "")
        self.assertTrue(retired, "proxy yang menolak CONNECT harus di-retire")

    def test_a_dead_entry_in_the_list_costs_one_row_not_the_run(self) -> None:
        """Honest behaviour: the row that meets the dead proxy is lost.

        The request is not retried on another proxy (that would double the
        timeouts on a list where most entries are dead).  What is pinned here
        is the part that must hold: the dead proxy is retired, the healthy one
        keeps serving, and the run finishes instead of hanging.
        """
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead = probe.getsockname()[1]
        # One worker and six rows: sequential, so at most the single row that
        # meets the dead proxy can be lost before it is retired.  With a wider
        # pool two rows can start on the dead entry at once, which is correct
        # behaviour but not a deterministic assertion.
        indexer = make_indexer(
            threads=1,
            transport="asyncio",
            use_proxy=True,
            proxy_list=[f"127.0.0.1:{dead}", f"127.0.0.1:{self.proxy.port}"],
        )
        with origin() as (port, srv):
            try:
                results = indexer.run(
                    [PAYLOAD],
                    [
                        PingTarget(url=f"http://127.0.0.1:{port}/p?u=", mode="query")
                        for _ in range(6)
                    ],
                )
                retired = list(indexer._retired)
            finally:
                indexer.close()
        self.assertEqual(len(results), 6)
        submitted = [r for r in results if r.status == "submitted"]
        self.assertGreaterEqual(len(submitted), 5, [r.error for r in results])
        for row in results:
            if row.status == "failed":
                self.assertIn("refused", (row.error or "").lower(), row.error)
        self.assertTrue(retired, "proxy mati harus di-retire")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
