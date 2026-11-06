"""URL watcher: incremental reads, and the bounded "already seen" set."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer.watcher import UrlWatcher  # noqa: E402


class UrlWatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.log = self.dir / "links.txt"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _append(self, *urls: str) -> None:
        with self.log.open("a", encoding="utf-8") as fh:
            for url in urls:
                fh.write(url + "\n")

    def test_only_new_lines_are_reported(self) -> None:
        self._append("http://a.example/", "http://b.example/")
        watcher = UrlWatcher([self.log])
        self.assertEqual(
            watcher.poll(), ["http://a.example/", "http://b.example/"]
        )
        self.assertEqual(watcher.poll(), [], "poll kedua tidak boleh mengulang")

        self._append("http://c.example/")
        self.assertEqual(watcher.poll(), ["http://c.example/"])

    def test_comments_blanks_and_non_urls_are_ignored(self) -> None:
        self._append("# a comment", "", "bukan url", "http://a.example/")
        self.assertEqual(UrlWatcher([self.log]).poll(), ["http://a.example/"])

    def test_truncated_file_is_read_from_the_start(self) -> None:
        self._append("http://a.example/")
        watcher = UrlWatcher([self.log])
        watcher.poll()
        # A rotated log is shorter than the offset already consumed.
        self.log.write_text("http://b.co/\n", encoding="utf-8")
        self.assertEqual(watcher.poll(), ["http://b.co/"])

    def test_seen_set_is_bounded_oldest_first(self) -> None:
        watcher = UrlWatcher([self.log], max_seen=1_000)
        for index in range(1_600):
            self._append(f"http://h{index}.example/")
            watcher.poll()
        self.assertEqual(len(watcher._seen), 1_000)
        self.assertIn("http://h1599.example/", watcher._seen)
        self.assertNotIn("http://h0.example/", watcher._seen)

    def test_a_directory_is_expanded(self) -> None:
        other = self.dir / "more.txt"
        other.write_text("http://d.example/\n", encoding="utf-8")
        self._append("http://a.example/")
        found = UrlWatcher([self.dir]).poll()
        self.assertEqual(sorted(found), ["http://a.example/", "http://d.example/"])


if __name__ == "__main__":
    unittest.main()
