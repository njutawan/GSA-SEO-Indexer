"""Regression tests for the submission hot path (P0 optimisations).

Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import json
import ssl
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer import engine, httpclient  # noqa: E402
from gsa_seo_indexer.engine import (  # noqa: E402
    Indexer,
    PingTarget,
    Settings,
    _classify_link,
    cap_targets,
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    """Answers every GET with a page that links back to ``smoke.local``."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server API
        body = b'<html><a href="http://smoke.local/">smoke.local</a></html>'
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def local_server():
    """Yield the port of a zero-latency local HTTP server."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.request_queue_size = 128
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------
# P0 #1 — the CA bundle is loaded once, not once per request
# --------------------------------------------------------------------------
class TlsContextReuseTest(unittest.TestCase):
    def setUp(self) -> None:
        httpclient._contexts.clear()

    def tearDown(self) -> None:
        httpclient._contexts.clear()

    def test_contexts_are_built_once_per_kind(self) -> None:
        real = ssl.create_default_context
        calls: list[int] = []

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        with mock.patch.object(httpclient.ssl, "create_default_context", counting):
            indexer = Indexer(Settings(persist_host_cache=False))
            for _ in range(25):
                indexer._opener(None)
                indexer._opener(None, verify=False)

        self.assertEqual(len(calls), 2, "each context kind must be built exactly once")

    def test_real_requests_never_rebuild_a_context(self) -> None:
        """The guard that matters: 30 real submissions must not rebuild one."""
        real = ssl.create_default_context
        calls: list[int] = []

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        with local_server() as port:
            settings = Settings(
                threads=4,
                persist_host_cache=False,
                random_delay=False,
                skip_dead_hosts=False,
                dns_cache=False,
            )
            with mock.patch.object(httpclient.ssl, "create_default_context", counting):
                indexer = Indexer(settings)
                target = PingTarget(url=f"http://127.0.0.1:{port}/", mode="query")
                results = [indexer.submit_one(target, "http://smoke.local/") for _ in range(30)]
                indexer.close()

        self.assertTrue(all(r.status == "submitted" for r in results))
        self.assertLessEqual(len(calls), 2)

    def test_unverified_fallback_is_unverified(self) -> None:
        ctx = engine._ssl_context(False)
        self.assertFalse(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)

    def test_verified_context_verifies(self) -> None:
        ctx = engine._ssl_context(True)
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_opener_uses_the_shared_context(self) -> None:
        indexer = Indexer(Settings(persist_host_cache=False))
        opener = indexer._opener(None)
        contexts = [
            h._context
            for h in opener.handlers
            if isinstance(h, engine.urllib.request.HTTPSHandler)
        ]
        self.assertEqual(len(contexts), 1)
        self.assertIs(contexts[0], engine._ssl_context(True))

    def test_pooled_client_shares_the_same_contexts(self) -> None:
        """A pool per proxy must not reload the CA bundle either."""
        client = httpclient.PooledHttpClient()
        try:
            self.assertIs(client.pool._ctx, engine._ssl_context(False))
            self.assertIs(client.pool._verify_ctx, engine._ssl_context(True))
        finally:
            client.close()


# --------------------------------------------------------------------------
# P0 #2 — classification keeps its meaning with the fast reject
# --------------------------------------------------------------------------
class ClassifyLinkTest(unittest.TestCase):
    def test_real_anchor_is_indexed(self) -> None:
        body = "<html><a href='https://example.com/'>x</a></html>"
        self.assertEqual(_classify_link(body, "https://example.com/"), "indexed")

    def test_uppercase_host_and_scheme_mismatch_still_indexed(self) -> None:
        self.assertEqual(
            _classify_link("<a href='http://EXAMPLE.com/'>x</a>", "https://example.com/"),
            "indexed",
        )

    def test_deep_link_path_must_match(self) -> None:
        body = "<a href='https://example.com/other'>x</a>"
        self.assertEqual(_classify_link(body, "https://example.com/blog/post"), "")

    def test_deep_link_is_matched(self) -> None:
        body = "<a class='x' href='https://example.com/blog/post'>post</a>"
        self.assertEqual(_classify_link(body, "https://example.com/blog/post"), "indexed")

    def test_plain_mention_is_listed(self) -> None:
        body = "<p>see https://example.com/ for details</p>"
        self.assertEqual(_classify_link(body, "https://example.com/"), "listed")

    def test_query_echo_is_not_a_listing(self) -> None:
        body = "<a href='?url=https://example.com/'>search</a>"
        self.assertEqual(_classify_link(body, "https://example.com/"), "")

    def test_query_param_on_the_target_still_matches(self) -> None:
        body = "<a href='https://example.com/page?a=1'>x</a>"
        self.assertEqual(_classify_link(body, "https://example.com/page?a=1"), "indexed")

    def test_relative_link_is_never_indexed(self) -> None:
        body = "<a href='/relative/path'>x</a>"
        self.assertEqual(_classify_link(body, "https://example.com/"), "")

    def test_other_host_is_not_a_backlink(self) -> None:
        body = "<a href='https://notexample.com/'>x</a>"
        self.assertEqual(_classify_link(body, "https://example.com/"), "")

    def test_unmentioned_target_is_rejected(self) -> None:
        body = "<html><body>" + "filler " * 20000 + "</body></html>"
        self.assertEqual(_classify_link(body, "https://example.com/"), "")

    def test_empty_inputs(self) -> None:
        self.assertEqual(_classify_link("", "https://example.com/"), "")
        self.assertEqual(_classify_link("<html></html>", ""), "")


# --------------------------------------------------------------------------
# P0 #3 — "max sites" counts sites (distinct hosts), once
# --------------------------------------------------------------------------
class CapTargetsTest(unittest.TestCase):
    def _shapes(self, hosts, modes=("query", "append")):
        return [
            PingTarget(url=f"http://{h}/", kind="plain", mode=m)
            for h in hosts
            for m in modes
        ]

    def test_zero_means_unlimited(self) -> None:
        targets = self._shapes(["a", "b", "c"])
        self.assertEqual(len(cap_targets(targets, 0)), len(targets))

    def test_cap_counts_hosts_not_list_entries(self) -> None:
        targets = self._shapes(["a", "b", "c"])  # 6 entries, 3 sites
        capped = cap_targets(targets, 2)
        self.assertEqual(len(capped), 4)  # both shapes of a and b
        self.assertEqual({engine.host_of(t.url) for t in capped}, {"a", "b"})

    def test_fallback_shape_of_an_unselected_host_is_dropped(self) -> None:
        targets = self._shapes(["a", "b", "c"])
        capped = cap_targets(targets, 1)
        self.assertEqual({engine.host_of(t.url) for t in capped}, {"a"})
        self.assertEqual({t.mode for t in capped}, {"query", "append"})


class MaxSitesCapTest(unittest.TestCase):
    def _run(self, cap: int, urls=("http://a.example/", "http://b.example/"), fast=False):
        seen: list[str] = []

        class _Fake(Indexer):
            def submit_batch(self, target, payloads):
                seen.append(target.url)
                return [
                    engine.Result(target=p, url=target.url, status="submitted")
                    for p in payloads
                ]

        targets = [PingTarget(url=f"http://site{i}.example/") for i in range(4)]
        indexer = _Fake(
            Settings(
                max_sites=cap,
                persist_host_cache=False,
                random_delay=False,
                dns_cache=False,
            )
        )
        if fast:
            indexer.run_adaptive(list(urls), targets)
        else:
            indexer.run(list(urls), targets)
        return seen, indexer

    def test_cap_keeps_every_url_for_each_capped_site(self) -> None:
        seen, indexer = self._run(4)
        self.assertEqual(len(set(seen)), 4)
        self.assertEqual(indexer.stats.total, 8)  # 4 sites x 2 URLs
        self.assertEqual(len(indexer.results), 8)

    def test_cap_limits_sites_once(self) -> None:
        seen, indexer = self._run(2)
        self.assertEqual(len(set(seen)), 2)
        self.assertEqual(indexer.stats.total, 4)
        self.assertEqual(len(indexer.results), 4)

    def test_adaptive_mode_caps_the_same_way(self) -> None:
        seen, indexer = self._run(2, fast=True)
        self.assertEqual(len(set(seen)), 2)
        self.assertEqual(indexer.stats.total, 4)  # 2 sites x 2 URLs

    def test_stats_total_matches_the_work_actually_done(self) -> None:
        for cap in (0, 1, 2, 4):
            seen, indexer = self._run(cap)
            done = indexer.stats.submitted + indexer.stats.failed + indexer.stats.skipped
            self.assertEqual(done, len(seen))
            self.assertEqual(indexer.stats.total, len(seen), f"cap={cap}")


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------
class SubmitOneSmokeTest(unittest.TestCase):
    def test_submit_one_reports_the_backlink(self) -> None:
        with local_server() as port:
            indexer = Indexer(
                Settings(
                    persist_host_cache=False,
                    random_delay=False,
                    skip_dead_hosts=False,
                )
            )
            target = PingTarget(url=f"http://127.0.0.1:{port}/", mode="query")
            result = indexer.submit_one(target, "http://smoke.local/")
            indexer.close()

        self.assertEqual(result.status, "submitted")
        self.assertEqual(result.code, 200)
        self.assertTrue(result.indexable)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
# P1 #3 — results are delivered in completion order, not submission order
# --------------------------------------------------------------------------
class DeliveryOrderTest(unittest.TestCase):
    def test_slow_request_does_not_block_the_others(self) -> None:
        delivered: list[str] = []

        class _Fake(Indexer):
            def submit_batch(self, target, payloads):
                if "slow" in target.url:
                    time.sleep(0.30)
                return [
                    engine.Result(target=p, url=target.url, status="submitted")
                    for p in payloads
                ]

        names = ("slow", "a", "b", "c", "d", "e")
        targets = [PingTarget(url=f"http://{n}.example/") for n in names]
        indexer = _Fake(
            Settings(
                threads=6,
                persist_host_cache=False,
                random_delay=False,
                dns_cache=False,
            )
        )
        started = time.perf_counter()
        indexer.run(
            ["http://target.example/"], targets, on_result=lambda r: delivered.append(r.url)
        )
        elapsed = time.perf_counter() - started

        self.assertEqual(len(delivered), len(names))
        self.assertIn("slow", delivered[-1], "the slow target must arrive last")
        # With pool.map this would take 0.30 s before the first callback.
        self.assertLess(elapsed, 0.30 + 0.25)

    def test_a_crashing_worker_is_recorded_not_fatal(self) -> None:
        class _Fake(Indexer):
            def submit_batch(self, target, payloads):
                if "boom" in target.url:
                    raise RuntimeError("worker blew up")
                return [
                    engine.Result(target=p, url=target.url, status="submitted")
                    for p in payloads
                ]

        targets = [PingTarget(url=f"http://{n}.example/") for n in ("boom", "ok")]
        indexer = _Fake(
            Settings(persist_host_cache=False, random_delay=False, dns_cache=False)
        )
        results = indexer.run(["http://target.example/"], targets)

        self.assertEqual(len(results), 2)
        failed = [r for r in results if r.status == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("worker blew up", failed[0].error)


# --------------------------------------------------------------------------
# P1 #1 — pre-run URL checks run in parallel
# --------------------------------------------------------------------------
class CheckUrlsTest(unittest.TestCase):
    class _FakeIndexer:
        def __init__(self, delay_for: str = "", delay: float = 0.0):
            self.delay_for = delay_for
            self.delay = delay
            self.seen: list[str] = []

        def check_url(self, url):
            if self.delay_for and self.delay_for in url:
                time.sleep(self.delay)
            self.seen.append(url)
            if "bad" in url:
                return False, "HTTP 404"
            if "boom" in url:
                raise RuntimeError("check exploded")
            return True, "ok"

    def test_checks_are_parallel_and_keep_input_order(self) -> None:
        fake = self._FakeIndexer(delay_for="slow", delay=0.30)
        urls = ["http://slow/", "http://bad/", "http://ok/"]
        started = time.perf_counter()
        checked = engine.check_urls(fake, urls)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.55, "checks must overlap")
        self.assertEqual([u for u, _ok, _r in checked], urls)
        self.assertEqual([ok for _u, ok, _r in checked], [True, False, True])

    def test_callback_fires_as_checks_finish(self) -> None:
        fake = self._FakeIndexer(delay_for="slow", delay=0.25)
        seen: list[str] = []
        engine.check_urls(
            fake,
            ["http://slow/", "http://fast/"],
            on_checked=lambda url, _ok, _reason: seen.append(url),
        )
        self.assertEqual(seen, ["http://fast/", "http://slow/"])

    def test_an_exception_is_a_reject_not_a_crash(self) -> None:
        fake = self._FakeIndexer()
        checked = engine.check_urls(fake, ["http://boom/", "http://ok/"])
        self.assertEqual(checked[0][1], False)
        self.assertIn("exploded", checked[0][2])
        self.assertEqual(checked[1][1], True)

    def test_empty_list(self) -> None:
        self.assertEqual(engine.check_urls(self._FakeIndexer(), []), [])


# --------------------------------------------------------------------------
# P1 #4 — reports stream, results are slotted
# --------------------------------------------------------------------------
class ReportTest(unittest.TestCase):
    def _indexer(self, results: int, fmt: str) -> Indexer:
        indexer = Indexer(Settings(persist_host_cache=False, dns_cache=False))
        indexer.settings.report_format = fmt
        indexer.results = [
            engine.Result(
                target=f"http://t{i}.example/",
                url=f"http://s{i}.example/?url=x",
                status="submitted",
                code=200,
                indexable=(i % 2 == 0),
                listed=False,
                elapsed_ms=i,
                error="",
            )
            for i in range(results)
        ]
        return indexer

    def test_json_report_is_valid_and_complete(self) -> None:
        indexer = self._indexer(25, "json")
        with tempfile.TemporaryDirectory() as tmp:
            path = indexer.write_report(Path(tmp) / "r.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload), 25)
        self.assertEqual(payload[0]["target"], "http://t0.example/")
        self.assertEqual(payload[24]["elapsed_ms"], 24)

    def test_csv_report_has_one_row_per_result(self) -> None:
        indexer = self._indexer(10, "csv")
        with tempfile.TemporaryDirectory() as tmp:
            path = indexer.write_report(Path(tmp) / "r.csv")
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 11)  # header + 10

    def test_result_uses_slots(self) -> None:
        result = engine.Result(target="http://a/", url="http://b/", status="submitted")
        self.assertFalse(hasattr(result, "__dict__"))
        self.assertEqual(
            engine.asdict(result)["target"], "http://a/"
        )


class BoundedWorkWindowTest(unittest.TestCase):
    """`run()` keeps only 4 x threads futures in flight and stops promptly."""

    def test_a_large_work_list_is_completed_in_full(self) -> None:
        class _Fake(Indexer):
            def submit_batch(self, target, payloads):
                return [
                    engine.Result(target=p, url=target.url, status="submitted")
                    for p in payloads
                ]

        targets = [PingTarget(url=f"http://s{i}.example/") for i in range(200)]
        indexer = _Fake(
            Settings(threads=4, persist_host_cache=False, random_delay=False, dns_cache=False)
        )
        results = indexer.run(["http://t.example/"], targets)
        self.assertEqual(len(results), 200)
        self.assertEqual(indexer.stats.total, 200)
        self.assertEqual(indexer.stats.submitted, 200)

    def test_stop_does_not_drain_the_whole_queue(self) -> None:
        calls: list[str] = []

        class _Fake(Indexer):
            def submit_batch(self, target, payloads):
                calls.append(target.url)
                time.sleep(0.4)
                return [
                    engine.Result(target=p, url=target.url, status="submitted")
                    for p in payloads
                ]

        targets = [PingTarget(url=f"http://s{i}.example/") for i in range(20)]
        indexer = _Fake(
            Settings(threads=2, persist_host_cache=False, random_delay=False, dns_cache=False)
        )
        worker = threading.Thread(
            target=indexer.run, args=(["http://t.example/"], targets), daemon=True
        )
        worker.start()
        time.sleep(0.15)
        indexer.stop()
        started = time.perf_counter()
        worker.join(timeout=5)
        elapsed = time.perf_counter() - started

        self.assertFalse(worker.is_alive(), "run() must return after stop()")
        self.assertLess(elapsed, 2.0, "in-flight requests may finish, the queue may not")
        self.assertLess(len(calls), len(targets))
