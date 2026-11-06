"""Regression tests for the submission hot path (P0 optimisations).

Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import ssl
import sys
import threading
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
