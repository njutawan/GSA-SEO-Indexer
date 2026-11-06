"""Tests for the host liveness cache and the DNS cache (P2)."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gsa_seo_indexer.perf as perf  # noqa: E402
from gsa_seo_indexer.perf import (  # noqa: E402
    DnsCache,
    HostCache,
    activate_dns_cache,
    deactivate_dns_cache,
)


class HostFailureStreakTest(unittest.TestCase):
    def test_host_that_never_answered_dies_after_one_failure(self) -> None:
        cache = HostCache()
        cache.record_failure("dead.example", "connect timeout")
        self.assertTrue(cache.is_dead("dead.example"))

    def test_host_that_answered_survives_one_hiccup(self) -> None:
        cache = HostCache()
        cache.record_success("alive.example", 12.0)
        cache.record_failure("alive.example", "read timeout")
        self.assertFalse(cache.is_dead("alive.example"))

        cache.record_failure("alive.example", "read timeout")
        self.assertTrue(cache.is_dead("alive.example"))

    def test_a_success_clears_the_streak(self) -> None:
        cache = HostCache()
        cache.record_success("flaky.example")
        cache.record_failure("flaky.example")
        cache.record_success("flaky.example")
        cache.record_failure("flaky.example")
        self.assertFalse(cache.is_dead("flaky.example"))

    def test_streak_survives_a_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host_cache.json"
            cache = HostCache()
            cache.record_success("gone.example", 20.0)
            cache.record_failure("gone.example", "timeout")
            cache.record_failure("gone.example", "timeout")
            self.assertEqual(cache.save(path), 1)

            restored = HostCache()
            self.assertEqual(restored.load(path), 1)
            self.assertTrue(restored.is_dead("gone.example"))
            state = restored.get("gone.example")
            self.assertIsNotNone(state)
            self.assertGreaterEqual(state.consecutive_failures, 2)

    def test_version_1_cache_file_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "saved": time.time(),
                        "dead": {
                            "old.example": {
                                "checked": time.time(),
                                "failures": 1,
                                "last_error": "no route",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            cache = HostCache()
            self.assertEqual(cache.load(path), 1)
            self.assertTrue(cache.is_dead("old.example"))

    def test_expired_dead_entry_is_retried(self) -> None:
        cache = HostCache(dead_ttl=0.01)
        cache.record_failure("maybe.example")
        time.sleep(0.02)
        self.assertFalse(cache.is_dead("maybe.example"))


class DnsCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._real = perf._orig_getaddrinfo
        self.calls: list[str] = []

    def tearDown(self) -> None:
        perf._orig_getaddrinfo = self._real
        deactivate_dns_cache()

    def _install(self, behavior) -> None:
        def fake(host, port, family=0, type=0, proto=0, flags=0):
            self.calls.append(host)
            return behavior(host)

        perf._orig_getaddrinfo = fake

    def test_a_resolution_is_memoised(self) -> None:
        self._install(lambda host: [(host, 80)])
        cache = DnsCache(ttl=60)
        self.assertEqual(cache.lookup("a.example", 80, 0, 0, 0, 0), [("a.example", 80)])
        self.assertEqual(cache.lookup("a.example", 80, 0, 0, 0, 0), [("a.example", 80)])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(cache.stats()["hits"], 1)

    def test_expired_entries_are_pruned(self) -> None:
        self._install(lambda host: [(host, 80)])
        cache = DnsCache(ttl=0.01, negative_ttl=0.01, max_entries=100)
        for index in range(10):
            cache.lookup(f"h{index}.example", 80, 0, 0, 0, 0)
        self.assertEqual(len(cache._entries), 10)

        time.sleep(0.02)
        self.assertEqual(cache.prune(), 10)
        self.assertEqual(len(cache._entries), 0)

    def test_the_table_is_capped(self) -> None:
        self._install(lambda host: [(host, 80)])
        cache = DnsCache(ttl=600, max_entries=64)
        for index in range(300):
            cache.lookup(f"h{index}.example", 80, 0, 0, 0, 0)
        self.assertLessEqual(len(cache._entries), 64)
        self.assertGreater(cache.evictions, 0)

    def test_failure_caches_the_type_and_message_not_the_exception(self) -> None:
        def boom(host):
            raise OSError(f"cannot resolve {host}")

        self._install(boom)
        cache = DnsCache(ttl=60, negative_ttl=30)
        with self.assertRaises(OSError) as first:
            cache.lookup("missing.example", 80, 0, 0, 0, 0)
        self.assertIn("cannot resolve missing.example", str(first.exception))

        entry = next(iter(cache._entries.values()))
        self.assertIs(entry.error_type, OSError)
        self.assertIn("cannot resolve missing.example", entry.error_msg)
        self.assertFalse(hasattr(entry, "error"), "no exception object is retained")

        with self.assertRaises(OSError) as second:
            cache.lookup("missing.example", 80, 0, 0, 0, 0)
        self.assertIn("cannot resolve missing.example", str(second.exception))

    def test_negative_entries_expire(self) -> None:
        def boom(_host):
            raise OSError("nope")

        self._install(boom)
        cache = DnsCache(ttl=60, negative_ttl=0.01)
        with self.assertRaises(OSError):
            cache.lookup("x.example", 80, 0, 0, 0, 0)
        time.sleep(0.02)
        self._install(lambda host: [(host, 80)])
        self.assertEqual(cache.lookup("x.example", 80, 0, 0, 0, 0), [("x.example", 80)])

    def test_deactivation_respects_the_active_cache(self) -> None:
        first, second = DnsCache(), DnsCache()
        activate_dns_cache(first)
        activate_dns_cache(second)
        deactivate_dns_cache(first)  # must not switch off the active one
        self.assertIs(perf._active_dns, second)
        deactivate_dns_cache(second)
        self.assertIsNone(perf._active_dns)


if __name__ == "__main__":
    unittest.main()
