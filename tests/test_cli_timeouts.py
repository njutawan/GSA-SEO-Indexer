"""The ``--timeout`` / ``--connect-timeout`` / ``--read-timeout`` mapping (P2)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer import cli  # noqa: E402
from gsa_seo_indexer.engine import Settings  # noqa: E402


class ResolveTimeoutsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Settings(connect_timeout=4.0, read_timeout=5.0)

    def test_config_values_are_used_by_default(self) -> None:
        self.assertEqual(cli.resolve_timeouts(None, None, None, self.cfg), (4.0, 5.0))

    def test_timeout_sets_read_and_caps_connect(self) -> None:
        # 3 s must not still spend 4 s connecting to a dead host.
        self.assertEqual(cli.resolve_timeouts(3.0, None, None, self.cfg), (3.0, 3.0))

    def test_a_larger_timeout_only_raises_the_read_phase(self) -> None:
        self.assertEqual(cli.resolve_timeouts(10.0, None, None, self.cfg), (4.0, 10.0))

    def test_explicit_split_values_win(self) -> None:
        self.assertEqual(cli.resolve_timeouts(None, 2.0, 9.0, self.cfg), (2.0, 9.0))

    def test_timeout_still_caps_an_explicit_connect_timeout(self) -> None:
        self.assertEqual(cli.resolve_timeouts(3.0, 6.0, 8.0, self.cfg), (3.0, 3.0))


if __name__ == "__main__":
    unittest.main()
