"""``_classify_link`` must keep deciding exactly as it did.

The listing decision used to strip echoes with one regex over the whole body
and search the remainder; now it searches the gaps between the same matches
(two cheaper regexes), which is ~3.5x faster on a 165 kB page.  "Faster" is
only acceptable if the answer never changes, because a page wrongly marked
"listed" is never repaired.

The corpus and the reference implementation live in ``tools/classify_diff.py``
so the fuzz harness and this test cannot drift apart.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gsa_seo_indexer.engine import _classify_link  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "classify_diff", ROOT / "tools" / "classify_diff.py"
)
classify_diff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(classify_diff)


class FuzzEquivalenceTest(unittest.TestCase):
    def test_the_harness_finds_no_difference(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "classify_diff.py"),
                "--fuzz", "3000",
                "--seed", "7",
                "--quiet",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fixed_corpus_matches_the_reference(self) -> None:
        for body, payload in classify_diff.TRICKY:
            with self.subTest(payload=payload, body=body[:60]):
                self.assertEqual(
                    _classify_link(body, payload),
                    classify_diff.reference_classify(body, payload),
                )


class ListingDecisionTest(unittest.TestCase):
    """The shapes that decide real runs, spelled out one by one."""

    def test_echo_only_is_not_listed(self) -> None:
        payload = "http://example.com/blog/post"
        for body in (
            f"<a href='/?url={payload}'>cari</a>",
            f'?u={payload}',
            f'<meta content="{payload}">',
        ):
            with self.subTest(body=body):
                self.assertEqual(_classify_link(body, payload), "")

    def test_visible_text_is_listed(self) -> None:
        payload = "http://example.com/blog/post"
        for body in (
            f"<p>{payload}</p>",
            f"<a href='/?url={payload}'>x</a> <p>{payload}</p>",
            "example.com/blog/post",
        ):
            with self.subTest(body=body):
                self.assertEqual(_classify_link(body, payload), "listed")

    def test_a_query_before_a_quoted_url_still_lists_it(self) -> None:
        # The single pass never lets the '=' match here (the query is in the
        # way), so the value survives and the page counts as listed.
        payload = "http://example.com/blog/post"
        body = f'<a href= ?url="{payload}">x</a>'
        self.assertEqual(_classify_link(body, payload), "listed")
        self.assertEqual(classify_diff.reference_classify(body, payload), "listed")

    def test_a_real_anchor_is_indexed(self) -> None:
        payload = "http://example.com/blog/post"
        self.assertEqual(_classify_link(f'<a href="{payload}">p</a>', payload), "indexed")

    def test_needle_at_the_very_start(self) -> None:
        payload = "http://example.com/blog/post"
        self.assertEqual(_classify_link(f"{payload} and text", payload), "listed")

    def test_payload_with_a_query_string(self) -> None:
        # A payload that itself contains '?' is partly eaten by the echo regex
        # (the '?a=1' looks like a query run), so even a visible mention is not
        # reported as "listed".  That is the shipped behaviour, not the new
        # code's; both implementations are pinned to it here.
        payload = "http://example.com/x?a=1"
        for body in (f"?u={payload}", f"<p>{payload}</p>"):
            with self.subTest(body=body):
                self.assertEqual(_classify_link(body, payload), "")
                self.assertEqual(classify_diff.reference_classify(body, payload), "")


if __name__ == "__main__":
    unittest.main()
