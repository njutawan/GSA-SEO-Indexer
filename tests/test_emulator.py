"""The index-service emulator answers even with an empty service list (P3).

Ruff's F821 check found that ``do_GET`` referenced ``path`` without defining
it, so the root page raised NameError whenever no services were loaded.
"""

from __future__ import annotations

import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer import emulator  # noqa: E402


class EmulatorRootPageTest(unittest.TestCase):
    def setUp(self) -> None:
        config = emulator.EmulatorConfig(services=[])
        self.server = emulator.IndexServiceEmulator(config)
        _host, self.port = self.server.start()

    def tearDown(self) -> None:
        self.server.stop()

    def _get(self, path: str) -> tuple[int, str]:
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode("utf-8", "replace")

    def test_root_page_without_services(self) -> None:
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Indexer Server here", body)

    def test_unknown_path_falls_back_to_the_root_page(self) -> None:
        status, body = self._get("/no/such/service")
        self.assertEqual(status, 200)
        self.assertIn("Indexer Server here", body)


if __name__ == "__main__":
    unittest.main()
