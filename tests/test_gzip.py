"""Tests for ``Accept-Encoding: gzip`` (P8).

The bodies these ping/statistic sites return are HTML pages; gzip turns the
164 kB page used in the P5 benchmarks into ~2 kB on the wire.  What has to be
proved is that the *classifier sees the same text* afterwards, that a body cut
at ``max_body_bytes`` still decompresses, and that a server claiming gzip but
sending plain text does not break a run.

Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import gzip
import sys
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer import httpclient  # noqa: E402
from gsa_seo_indexer.engine import Indexer, PingTarget, Settings  # noqa: E402

TARGET = "http://t.example/"


def big_page(kind: str = "anchor") -> bytes:
    """A 164 kB page shaped like the result pages in the P5 benchmark."""
    filler = "<p>" + ("statistik situs ini menyebut banyak hal. " * 40) + "</p>"
    if kind == "anchor":
        link = f'<a href="{TARGET}">t.example</a>'
    elif kind == "listed":
        link = f"<div>hasil pencarian: {TARGET}</div>"
    elif kind == "noindex":
        link = '<meta name="robots" content="noindex">'
    else:
        link = "<div>bukan apa-apa</div>"
    body = "<html><body>" + link + filler * 60 + "</body></html>"
    return body.encode("utf-8")


class _GzipHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server API
        page = big_page(self.server.kind)
        accepts = "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
        self.server.requests.append(
            {
                "accept_encoding": self.headers.get("Accept-Encoding", ""),
                "gzip": accepts,
            }
        )
        if accepts and self.server.honour:
            payload = gzip.compress(page, 6)
            encoding = "gzip"
        elif accepts and self.server.lie:
            # claims gzip, sends plain text - happens in the wild
            payload = page
            encoding = "gzip"
        else:
            payload = page
            encoding = ""
        self.server.wire_bytes.append(len(payload))
        self.server.plain_bytes = len(page)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def gzip_server(kind: str = "anchor", honour: bool = True, lie: bool = False):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GzipHandler)
    server.daemon_threads = True
    server.kind = kind
    server.honour = honour
    server.lie = lie
    server.requests = []
    server.wire_bytes = []
    server.plain_bytes = 0
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def make_indexer(**kwargs) -> Indexer:
    settings = Settings(
        threads=2,
        persist_host_cache=False,
        random_delay=False,
        skip_dead_hosts=False,
        dns_cache=False,
        **kwargs,
    )
    return Indexer(settings)


class GzipSubmissionTest(unittest.TestCase):
    def test_gzip_keeps_the_decision_and_shrinks_the_wire(self) -> None:
        with gzip_server(kind="anchor") as (port, server):
            indexer = make_indexer(accept_gzip=True)
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/", mode="query"), TARGET
                )
            finally:
                indexer.close()

        self.assertEqual(result.status, "submitted", result.error)
        self.assertTrue(result.indexable, "the classifier must see the real page")
        self.assertTrue(server.requests[0]["gzip"], "the header must be sent")
        self.assertLess(server.wire_bytes[0], server.plain_bytes / 10)

    def test_off_by_default(self, ) -> None:
        with gzip_server(kind="anchor") as (port, server):
            indexer = make_indexer()
            try:
                result = indexer.submit_one(PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET)
            finally:
                indexer.close()

        self.assertTrue(result.indexable)
        self.assertFalse(server.requests[0]["gzip"])
        self.assertEqual(server.wire_bytes[0], server.plain_bytes)

    def test_a_listed_page_still_reads_as_listed(self) -> None:
        """The P5 fast path must not be fooled by a compressed transfer."""
        with gzip_server(kind="listed") as (port, _server):
            indexer = make_indexer(accept_gzip=True)
            try:
                result = indexer.submit_one(PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET)
            finally:
                indexer.close()

        self.assertTrue(result.listed)
        self.assertFalse(result.indexable)

    def test_pooled_transport_decodes_too(self) -> None:
        with gzip_server(kind="anchor") as (port, server):
            indexer = make_indexer(accept_gzip=True, reuse_connections=True)
            try:
                result = indexer.submit_one(PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET)
            finally:
                indexer.close()

        self.assertTrue(result.indexable, result.error)
        self.assertTrue(server.requests[0]["gzip"])

    def test_a_server_that_lies_about_gzip_still_works(self) -> None:
        with gzip_server(kind="anchor", honour=False, lie=True) as (port, _server):
            indexer = make_indexer(accept_gzip=True)
            try:
                result = indexer.submit_one(PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET)
            finally:
                indexer.close()

        self.assertEqual(result.status, "submitted", result.error)
        self.assertTrue(result.indexable, "plain bytes must be used as they are")

    def test_check_url_still_sees_noindex_inside_a_gzip_body(self) -> None:
        # The noindex test runs on the decompressed text; with the header sent
        # and the body still compressed it would silently never match.
        with gzip_server(kind="noindex") as (port, server):
            indexer = make_indexer(accept_gzip=True)
            try:
                ok, reason = indexer.check_url(f"http://127.0.0.1:{port}/")
            finally:
                indexer.close()

        self.assertFalse(ok)
        self.assertEqual(reason, "page contains noindex")
        self.assertTrue(server.requests[0]["gzip"])


class DecodeContentTest(unittest.TestCase):
    def test_gzip_round_trip(self) -> None:
        page = big_page()
        self.assertEqual(httpclient.decode_content(gzip.compress(page), "gzip"), page)

    def test_truncated_gzip_returns_what_it_can(self) -> None:
        page = big_page()
        blob = gzip.compress(page, 6)[:-20]
        out = httpclient.decode_content(blob, "gzip")
        self.assertTrue(out)
        self.assertTrue(page.startswith(out), "a cut stream must still decode in order")

    def test_the_decompressed_size_is_capped(self) -> None:
        """A 1 MB of zeros is ~1 kB compressed: never expand past the limit."""
        blob = gzip.compress(b"\0" * 1_000_000, 9)
        out = httpclient.decode_content(blob, "gzip", limit=4096)
        self.assertEqual(len(out), 4096)

    def test_plain_bytes_behind_a_gzip_header(self) -> None:
        page = b"<html>plain</html>"
        self.assertEqual(httpclient.decode_content(page, "gzip"), page)

    def test_deflate_in_both_dialects(self) -> None:
        page = b"<html>deflate</html>"
        zlib_wrapped = zlib.compress(page)
        raw = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
        raw_bytes = raw.compress(page) + raw.flush()
        self.assertEqual(httpclient.decode_content(zlib_wrapped, "deflate"), page)
        self.assertEqual(httpclient.decode_content(raw_bytes, "deflate"), page)

    def test_identity_and_unknown_encodings_pass_through(self) -> None:
        page = b"<html>identity</html>"
        self.assertEqual(httpclient.decode_content(page, ""), page)
        self.assertEqual(httpclient.decode_content(page, "identity"), page)
        self.assertEqual(httpclient.decode_content(page, "br"), page)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
