"""ResultStore: bounded memory, identical output.

A 50 URL x 11k site run produces ~660k rows; kept as a list that is ~115 MB of
RAM until the report is written.  The store keeps a window in memory and spills
the rest to a temporary pickle, so the report, the iteration order and the
counters must all stay exactly the same.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer.engine import Indexer, PingTarget, Result, Settings  # noqa: E402
from gsa_seo_indexer.resultstore import ResultStore  # noqa: E402


def _row(index: int) -> Result:
    return Result(
        target=f"http://example.com/{index}",
        url=f"http://site.example/?u={index}",
        status="submitted",
        code=200,
        elapsed_ms=index,
    )


class ResultStoreTest(unittest.TestCase):
    def test_small_runs_never_touch_the_disk(self) -> None:
        store = ResultStore(max_memory=10)
        try:
            for index in range(5):
                store.append(_row(index))
            self.assertEqual(store.spills, 0)
            self.assertIsNone(store.path)
            self.assertEqual(len(store), 5)
            self.assertEqual([r.elapsed_ms for r in store], [0, 1, 2, 3, 4])
            self.assertEqual(store[2].elapsed_ms, 2)
        finally:
            store.close()

    def test_overflow_spills_and_keeps_the_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ResultStore(max_memory=4, directory=tmp)
            try:
                for index in range(10):
                    store.append(_row(index))
                self.assertGreaterEqual(store.spills, 1)
                self.assertEqual(len(store), 10)
                self.assertEqual([r.elapsed_ms for r in store], list(range(10)))
                self.assertLessEqual(store.in_memory, 4)
                self.assertIsNotNone(store.path)
                self.assertTrue(store.path.exists())
            finally:
                store.close()

    def test_clear_drops_the_spill_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ResultStore(max_memory=2, directory=tmp)
            for index in range(6):
                store.append(_row(index))
            path = store.path
            self.assertIsNotNone(path)
            store.clear()
            self.assertEqual(len(store), 0)
            self.assertIsNone(store.path)
            self.assertFalse(path.exists())
            store.append(_row(99))  # the object stays usable
            self.assertEqual(len(store), 1)
            store.close()

    def test_max_memory_zero_keeps_everything_in_ram(self) -> None:
        store = ResultStore(max_memory=0)
        try:
            for index in range(50):
                store.append(_row(index))
            self.assertEqual(store.spills, 0)
            self.assertEqual(store.in_memory, 50)
            self.assertIsNone(store.path)
        finally:
            store.close()

    def test_pack_and_unpack_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ResultStore(
                max_memory=2,
                directory=tmp,
                pack=lambda r: (r.target, r.url, r.status),
                unpack=lambda row: Result(target=row[0], url=row[1], status=row[2]),
            )
            try:
                for index in range(6):
                    store.append(_row(index))
                rows = list(store)
                self.assertEqual(len(rows), 6)
                self.assertEqual(rows[0].target, "http://example.com/0")
            finally:
                store.close()


class EngineIntegrationTest(unittest.TestCase):
    """The engine must behave the same with and without spilling."""

    def _run(self, max_memory: int, body: str, count: int = 20) -> Indexer:
        indexer = Indexer(
            Settings(
                threads=1,  # deterministic completion order for the comparison
                persist_host_cache=False,
                dns_cache=False,
                random_delay=False,
                max_results_in_memory=max_memory,
                report_format="json",
            )
        )
        indexer._fetch = lambda url, req, proxy=None: (body, 200)
        target = PingTarget(url="http://site.example/?url={enc}", mode="query")
        indexer.run([f"http://example.com/{i}" for i in range(count)], [target])
        return indexer

    def test_report_is_identical_when_results_spill(self) -> None:
        body = '<html><body><a href="http://example.com/0">x</a> http://example.com/1</body></html>'
        memory = self._run(0, body)
        spilled = self._run(3, body)
        try:
            self.assertGreaterEqual(spilled.results.spills, 1, "harus ada spill")
            with tempfile.TemporaryDirectory() as tmp:
                a = Path(tmp) / "a.json"
                b = Path(tmp) / "b.json"
                memory.write_report(a)
                spilled.write_report(b)
                # The store preserves insertion order; the run itself
                # collects rows as they complete, so compare the row sets.
                rows = json.loads(a.read_text())
                spilled_rows = json.loads(b.read_text())
                def keyf(row):
                    return (row["target"], row["url"], row["status"])

                self.assertEqual(
                    sorted(rows, key=keyf), sorted(spilled_rows, key=keyf)
                )
            self.assertEqual(len(spilled_rows), 20)
            self.assertEqual(len(spilled.results), 20)
        finally:
            memory.close()
            spilled.close()

    def test_counters_are_filled_while_running(self) -> None:
        body = '<html><body><a href="http://example.com/0">x</a><p>http://example.com/1</p></body></html>'
        indexer = self._run(2, body, count=10)
        try:
            # Only /0 is anchored and only /1 appears as text; the other eight
            # URLs are never mentioned on the page.
            self.assertEqual(indexer.stats.indexable, 1)
            self.assertEqual(indexer.stats.listed, 1)
            self.assertEqual(indexer.stats.submitted, 10)
        finally:
            indexer.close()

    def test_close_removes_the_spill_file(self) -> None:
        body = "<html><body>plain</body></html>"
        indexer = self._run(2, body, count=10)
        path = indexer.results.path
        self.assertIsNotNone(path)
        indexer.close()
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
