"""Packaging checks (P11): what a ``pip install`` actually receives.

The failure mode these tests exist for is silent: a wheel that is missing
``data/ping_sites.txt`` installs cleanly and then reports "0 sites", and one
missing ``assets/app.ico`` only shows up when the tray icon fails to load.  The
last wheel built from this repository was missing the icon.

The heavy part (building a wheel) lives in ``tools/check_package.py``; the
first test here runs it end to end, the rest are cheap static guards.

Run with::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_FILES = (
    "gsa_seo_indexer/data/ping_sites.txt",
    "gsa_seo_indexer/data/modern_targets.json",
    "gsa_seo_indexer/assets/app.ico",
)


class ProjectMetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_the_runtime_files_exist_in_the_checkout(self) -> None:
        for relative in RUNTIME_FILES:
            self.assertTrue((ROOT / relative).exists(), relative)

    def test_package_data_covers_every_runtime_file(self) -> None:
        patterns = self.data["tool"]["setuptools"]["package-data"]["gsa_seo_indexer"]
        for relative in RUNTIME_FILES:
            folder = relative.split("/")[1]  # data, assets, index_service
            self.assertIn(
                f"{folder}/*",
                patterns,
                f"{relative} tidak tercakup package-data",
            )

    def test_the_console_script_still_points_at_main(self) -> None:
        self.assertEqual(
            self.data["project"]["scripts"]["gsa-seo-indexer"],
            "gsa_seo_indexer.cli:main",
        )

    def test_the_runtime_stays_dependency_free(self) -> None:
        self.assertEqual(self.data["project"]["dependencies"], [])
        self.assertGreaterEqual(self.data["project"]["requires-python"], ">=3.10")

    def test_the_version_is_declared_in_one_place_and_matches_the_code(self) -> None:
        from gsa_seo_indexer import engine

        self.assertEqual(self.data["project"]["version"], engine.APP_VERSION)


class ReleaseWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    def test_it_publishes_through_trusted_publishing(self) -> None:
        self.assertIn("pypa/gh-action-pypi-publish", self.text)
        self.assertIn("id-token: write", self.text)
        self.assertNotIn("secrets.PYPI", self.text)

    def test_it_only_publishes_on_a_tag(self) -> None:
        self.assertIn("tags: [\"v*\"]", self.text)
        self.assertIn("twine check", self.text)

    def test_it_checks_the_wheel_contents_before_uploading(self) -> None:
        self.assertIn("tools/check_package.py", self.text)


class WheelContentsTest(unittest.TestCase):
    def test_the_built_wheel_carries_the_runtime_files(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_package.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        self.assertEqual(
            result.returncode,
            0,
            (result.stdout + result.stderr)[-1500:],
        )
        self.assertIn("data lengkap", result.stdout)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
