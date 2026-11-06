"""Tests for ``mode="post"`` targets (P7).

A ``post`` target used to be a lie: ``PingTarget.with_payload`` returned the
bare base URL and every transport opened it with a hard-coded GET, so the
submission carried no URL at all and the site answered a page about nothing.
These tests pin the real behaviour - the body is a form, both transports send
it, and the GET paths stay byte-for-byte what they were.

Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer import engine  # noqa: E402
from gsa_seo_indexer.engine import Indexer, PingTarget, Settings  # noqa: E402

PAGE = b'<html><a href="http://t.example/">t.example</a></html>'


class _Recorder(BaseHTTPRequestHandler):
    """Records every request and answers with one page that links t.example."""

    protocol_version = "HTTP/1.1"

    def _record(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.seen.append(
            {
                "method": self.command,
                "path": self.path,
                "body": body,
                "content_type": self.headers.get("Content-Type", ""),
                "content_length": self.headers.get("Content-Length", ""),
            }
        )
        return body

    def _answer(self, code: int, body: bytes, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server API
        self._record()
        if self.path.startswith("/r303"):
            self._answer(303, b"", {"Location": "/any"})
            return
        if self.path.startswith("/r307"):
            self._answer(307, b"", {"Location": "/any"})
            return
        self._answer(200, PAGE)

    def do_POST(self):  # noqa: N802 - http.server API
        self._record()
        if self.path.startswith("/r303"):
            self._answer(303, b"", {"Location": "/any"})
            return
        if self.path.startswith("/r307"):
            self._answer(307, b"", {"Location": "/any"})
            return
        self._answer(200, PAGE)

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def recording_server():
    """Yield ``(port, seen)`` for a local server that records every request."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    server.daemon_threads = True
    server.request_queue_size = 64
    server.seen = []
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, server.seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def make_indexer(reuse_connections: bool = False, **kwargs) -> Indexer:
    settings = Settings(
        threads=2,
        persist_host_cache=False,
        random_delay=False,
        skip_dead_hosts=False,
        dns_cache=False,
        reuse_connections=reuse_connections,
        **kwargs,
    )
    return Indexer(settings)


# --------------------------------------------------------------------------
# The target itself
# --------------------------------------------------------------------------
class PingTargetShapeTest(unittest.TestCase):
    def test_default_body_mirrors_the_query_shape(self) -> None:
        target = PingTarget(url="http://s.example/", mode="post")
        self.assertEqual(target.body, engine.DEFAULT_POST_BODY)
        self.assertEqual(target.http_method, "POST")

    def test_the_payload_is_form_encoded_in_the_body(self) -> None:
        target = PingTarget(url="http://s.example/", mode="post")
        self.assertEqual(
            target.post_body("http://t.example/a b&c"),
            "url=http%3A%2F%2Ft.example%2Fa%20b%26c",
        )

    def test_the_url_is_not_touched_for_a_post_target(self) -> None:
        """with_payload must not glue the target onto a POST endpoint."""
        target = PingTarget(url="http://s.example/ping", mode="post")
        self.assertEqual(target.with_payload("http://t.example/"), "http://s.example/ping")

    def test_custom_template_and_raw_placeholder(self) -> None:
        target = PingTarget(url="http://s.example/", mode="post", body='links[]={enc}&raw={url}')
        self.assertEqual(
            target.post_body("http://t.example/"),
            "links[]=http%3A%2F%2Ft.example%2F&raw=http://t.example/",
        )

    def test_an_empty_body_sends_no_payload(self) -> None:
        target = PingTarget(url="http://s.example/", mode="post", body="")
        self.assertEqual(target.post_body("http://t.example/"), "")

    def test_get_targets_keep_their_method_and_never_render_a_body(self) -> None:
        for mode in ("append", "query"):
            target = PingTarget(url="http://s.example/?url=", mode=mode)
            self.assertEqual(target.http_method, "GET", mode)


# --------------------------------------------------------------------------
# The urllib transport
# --------------------------------------------------------------------------
class UrllibPostTest(unittest.TestCase):
    def test_post_target_sends_a_form_body(self) -> None:
        with recording_server() as (port, seen):
            indexer = make_indexer()
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/ping", mode="post"),
                    "http://t.example/",
                )
            finally:
                indexer.close()

        self.assertEqual(result.status, "submitted", result.error)
        self.assertTrue(result.indexable)
        self.assertEqual(len(seen), 1)
        request = seen[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/ping")
        self.assertEqual(request["body"], b"url=http%3A%2F%2Ft.example%2F")
        self.assertEqual(
            request["content_type"], "application/x-www-form-urlencoded"
        )
        self.assertEqual(request["content_length"], str(len(request["body"])))

    def test_get_target_is_unchanged(self) -> None:
        """The regression guard: no body, no Content-Type, still a GET."""
        with recording_server() as (port, seen):
            indexer = make_indexer()
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/", mode="query"),
                    "http://t.example/",
                )
            finally:
                indexer.close()

        self.assertEqual(result.status, "submitted", result.error)
        self.assertEqual(seen[0]["method"], "GET")
        self.assertEqual(seen[0]["body"], b"")
        self.assertEqual(seen[0]["content_type"], "")

    def test_303_turns_the_follow_up_into_a_get(self) -> None:
        with recording_server() as (port, seen):
            indexer = make_indexer()
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/r303", mode="post"),
                    "http://t.example/",
                )
            finally:
                indexer.close()

        self.assertEqual(result.status, "submitted", result.error)
        self.assertEqual([r["method"] for r in seen], ["POST", "GET"])
        self.assertEqual(seen[0]["body"], b"url=http%3A%2F%2Ft.example%2F")
        self.assertEqual(seen[1]["body"], b"")


# --------------------------------------------------------------------------
# The pooled transport
# --------------------------------------------------------------------------
class PooledPostTest(unittest.TestCase):
    def test_pooled_post_sends_the_same_body(self) -> None:
        with recording_server() as (port, seen):
            indexer = make_indexer(reuse_connections=True)
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/ping", mode="post"),
                    "http://t.example/",
                )
            finally:
                indexer.close()

        self.assertEqual(result.status, "submitted", result.error)
        self.assertTrue(result.indexable)
        self.assertEqual(seen[0]["method"], "POST")
        self.assertEqual(seen[0]["body"], b"url=http%3A%2F%2Ft.example%2F")
        self.assertEqual(seen[0]["content_type"], "application/x-www-form-urlencoded")

    def test_pooled_get_is_unchanged(self) -> None:
        with recording_server() as (port, seen):
            indexer = make_indexer(reuse_connections=True)
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/", mode="query"),
                    "http://t.example/",
                )
            finally:
                indexer.close()

        self.assertEqual(result.status, "submitted", result.error)
        self.assertEqual(seen[0]["method"], "GET")
        self.assertEqual(seen[0]["body"], b"")

    def test_pooled_303_becomes_get_and_307_keeps_the_body(self) -> None:
        with recording_server() as (port, seen):
            indexer = make_indexer(reuse_connections=True)
            try:
                indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/r303", mode="post"),
                    "http://t.example/",
                )
                indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/r307", mode="post"),
                    "http://t.example/",
                )
            finally:
                indexer.close()

        methods = [r["method"] for r in seen]
        self.assertEqual(methods, ["POST", "GET", "POST", "POST"])
        # 303: the follow-up carries no body; 307: the body is replayed.
        self.assertEqual(seen[1]["body"], b"")
        self.assertEqual(seen[3]["body"], b"url=http%3A%2F%2Ft.example%2F")


# --------------------------------------------------------------------------
# Batch submissions and the JSON loader
# --------------------------------------------------------------------------
class PostBatchTest(unittest.TestCase):
    def test_batch_posts_one_joined_body(self) -> None:
        with recording_server() as (port, seen):
            indexer = make_indexer()
            try:
                results = indexer.submit_batch(
                    PingTarget(url=f"http://127.0.0.1:{port}/ping", mode="post"),
                    ["http://t.example/", "http://u.example/"],
                )
            finally:
                indexer.close()

        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.status == "submitted" for r in results))
        self.assertEqual(len(seen), 1, "a batch is one request")
        self.assertEqual(seen[0]["method"], "POST")
        self.assertEqual(
            seen[0]["body"],
            b"url=http%3A%2F%2Ft.example%2F%2Chttp%3A%2F%2Fu.example%2F",
        )


class ModernTargetLoaderTest(unittest.TestCase):
    def _load(self, targets: list[dict]) -> list[PingTarget]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "modern_targets.json"
            path.write_text(json.dumps({"targets": targets}), encoding="utf-8")
            return engine.load_modern_targets(path)

    def test_mode_is_normalised_and_the_body_is_honoured(self) -> None:
        loaded = self._load(
            [
                {"url": "http://a.example/", "mode": " POST "},
                {"url": "http://b.example/", "mode": "post", "body": "links[]={enc}"},
            ]
        )
        self.assertEqual([t.mode for t in loaded], ["post", "post"])
        self.assertEqual(loaded[0].body, engine.DEFAULT_POST_BODY)
        self.assertEqual(loaded[1].body, "links[]={enc}")

    def test_unknown_mode_falls_back_to_append(self) -> None:
        loaded = self._load([{"url": "http://a.example/", "mode": "telepathy"}])
        self.assertEqual(loaded[0].mode, "append")
        self.assertEqual(loaded[0].http_method, "GET")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
