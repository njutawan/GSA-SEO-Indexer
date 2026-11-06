"""Tests for the 429/503 retry policy (P9).

``429`` and ``503`` mean "later", not "no": the site is rate limiting or
briefly overloaded, and a run that records them as failures loses the row.
The policy is opt-in (``Settings.retries``) and has a hard ceiling
(``retry_max_wait``), because the one thing worse than a lost row is a worker
parked for 60 seconds on a server's ``Retry-After``.

Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import email.utils
import random
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer.engine import (  # noqa: E402
    Indexer,
    PingTarget,
    RETRY_STATUSES,
    Settings,
    retry_after_seconds,
    retry_delay,
)

TARGET = "http://t.example/"
PAGE = f'<html><a href="{TARGET}">t.example</a></html>'.encode()


class _FlakyHandler(BaseHTTPRequestHandler):
    """Answers ``codes`` in order, then 200 for every later request."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server API
        server = self.server
        with server.lock:
            index = len(server.seen)
            server.seen.append(self.headers.get("User-Agent", ""))
        code = server.codes[index] if index < len(server.codes) else 200
        header = server.retry_after
        body = PAGE if code == 200 else b"<html>later</html>"
        self.send_response(code)
        if header is not None and code in (429, 503):
            self.send_header("Retry-After", header)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def flaky_server(codes, retry_after=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FlakyHandler)
    server.daemon_threads = True
    server.codes = list(codes)
    server.retry_after = retry_after
    server.seen = []
    server.lock = threading.Lock()
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


class RetryAfterParsingTest(unittest.TestCase):
    def test_delta_seconds(self) -> None:
        self.assertEqual(retry_after_seconds({"Retry-After": "7"}), 7.0)

    def test_lowercase_key(self) -> None:
        self.assertEqual(retry_after_seconds({"retry-after": "3"}), 3.0)

    def test_http_date_in_the_future(self) -> None:
        when = email.utils.formatdate(time.time() + 30, usegmt=True)
        parsed = retry_after_seconds({"Retry-After": when})
        self.assertIsNotNone(parsed)
        self.assertGreater(parsed, 20)
        self.assertLessEqual(parsed, 31)

    def test_a_date_in_the_past_means_now(self) -> None:
        when = email.utils.formatdate(time.time() - 300, usegmt=True)
        self.assertEqual(retry_after_seconds({"Retry-After": when}), 0.0)

    def test_garbage_and_missing(self) -> None:
        self.assertIsNone(retry_after_seconds({"Retry-After": "soon"}))
        self.assertIsNone(retry_after_seconds({"Retry-After": ""}))
        self.assertIsNone(retry_after_seconds({}))
        self.assertIsNone(retry_after_seconds(None))


class RetryDelayTest(unittest.TestCase):
    def test_the_server_value_wins(self) -> None:
        self.assertEqual(retry_delay(0, {"Retry-After": "2"}, cap=10), 2.0)

    def test_a_longer_wait_than_the_cap_is_refused(self) -> None:
        self.assertIsNone(retry_delay(0, {"Retry-After": "60"}, cap=10))

    def test_backoff_doubles_without_a_header(self) -> None:
        rng = random.Random(1)
        first = retry_delay(0, {}, cap=10, rng=rng)
        second = retry_delay(1, {}, cap=10, rng=rng)
        third = retry_delay(2, {}, cap=10, rng=rng)
        self.assertLess(first, second)
        self.assertLess(second, third)
        self.assertGreaterEqual(first, 0.5)
        self.assertLessEqual(third, 2.5)

    def test_the_cap_still_applies_to_backoff(self) -> None:
        self.assertIsNone(retry_delay(5, {}, cap=1))

    def test_statuses_are_the_documented_pair(self) -> None:
        self.assertEqual(set(RETRY_STATUSES), {429, 503})


class RetryBehaviourTest(unittest.TestCase):
    def test_off_by_default_means_one_attempt(self) -> None:
        with flaky_server([503, 200], retry_after="0") as (port, server):
            indexer = make_indexer()
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET
                )
            finally:
                indexer.close()

        self.assertEqual(len(server.seen), 1, "no retry without --retries")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, 503)

    def test_a_503_then_200_is_a_success(self) -> None:
        with flaky_server([503, 200], retry_after="0") as (port, server):
            indexer = make_indexer(retries=1)
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET
                )
            finally:
                indexer.close()

        self.assertEqual(len(server.seen), 2)
        self.assertEqual(result.status, "submitted", result.error)
        self.assertTrue(result.indexable)

    def test_a_429_is_retried_too(self) -> None:
        with flaky_server([429, 429, 200], retry_after="0") as (port, server):
            indexer = make_indexer(retries=2)
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET
                )
            finally:
                indexer.close()

        self.assertEqual(len(server.seen), 3)
        self.assertEqual(result.status, "submitted", result.error)

    def test_retries_run_out(self) -> None:
        with flaky_server([503, 503, 503], retry_after="0") as (port, server):
            indexer = make_indexer(retries=1)
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET
                )
            finally:
                indexer.close()

        self.assertEqual(len(server.seen), 2, "1 attempt + 1 retry")
        self.assertEqual(result.status, "failed")
        self.assertIn("retry habis", result.error)

    def test_a_retry_after_beyond_the_cap_is_not_obeyed(self) -> None:
        with flaky_server([503, 200], retry_after="60") as (port, server):
            indexer = make_indexer(retries=3, retry_max_wait=1.0)
            started = time.perf_counter()
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET
                )
            finally:
                indexer.close()
            elapsed = time.perf_counter() - started

        self.assertEqual(len(server.seen), 1, "a 60 s wait is refused")
        self.assertLess(elapsed, 1.0)
        self.assertEqual(result.status, "failed")
        self.assertIn("retry habis", result.error)

    def test_the_pooled_transport_retries_as_well(self) -> None:
        with flaky_server([503, 200], retry_after="0") as (port, server):
            indexer = make_indexer(retries=1, reuse_connections=True)
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET
                )
            finally:
                indexer.close()

        self.assertEqual(len(server.seen), 2)
        self.assertEqual(result.status, "submitted", result.error)

    def test_stop_interrupts_a_backoff(self) -> None:
        """A run that is stopping must not sit through its remaining waits."""
        with flaky_server([503, 503], retry_after="5") as (port, _server):
            indexer = make_indexer(retries=3, retry_max_wait=10.0)
            threading.Timer(0.2, indexer.stop).start()
            started = time.perf_counter()
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET
                )
            finally:
                indexer.close()
            elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 3.0, "the 5 s backoff must be interruptible")
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.error, "stopped during retry")

    def test_a_normal_page_is_never_retried(self) -> None:
        with flaky_server([200]) as (port, server):
            indexer = make_indexer(retries=3)
            try:
                result = indexer.submit_one(
                    PingTarget(url=f"http://127.0.0.1:{port}/"), TARGET
                )
            finally:
                indexer.close()

        self.assertEqual(len(server.seen), 1)
        self.assertEqual(result.status, "submitted")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
