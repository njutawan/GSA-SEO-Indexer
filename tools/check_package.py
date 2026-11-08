#!/usr/bin/env python3
"""Build a wheel and check that a ``pip install`` gets a working package.

Everything the program reads at runtime is a *data* file (the ping site list,
the modern target list, the 48 index-service INIs, the tray/GUI icon).  Those
are exactly the files a wheel silently leaves out, and the failure only shows up
after publishing: the program installs, then reports "0 sites".

This script builds a wheel from the checkout, opens it, and asserts that:

* the runtime data files are inside,
* the console script ``gsa-seo-indexer`` is registered,
* ``METADATA`` carries a name, a version and ``Requires-Python``,
* the release workflow exists and uses PyPI trusted publishing.

Usage::

    python tools/check_package.py          # build + check, prints a summary
    python tools/check_package.py --keep   # leave the wheel in build/wheel/
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Files the runtime opens by path, so they must survive the packaging.
REQUIRED_DATA = (
    "gsa_seo_indexer/data/ping_sites.txt",
    "gsa_seo_indexer/data/modern_targets.json",
    "gsa_seo_indexer/assets/app.ico",
    "gsa_seo_indexer/index_service/2Index.Ninja.ini",
    "gsa_seo_indexer/index_service/Add To Index.ini",
)

#: At least this many index-service INIs must be packaged (the folder ships 48).
MIN_SERVICES = 40


def build_wheel(out_dir: Path) -> Path:
    """Build a wheel and return its path.

    The first attempt skips build isolation: it uses the setuptools already
    installed and needs no network.  The second allows isolation, because a
    fresh environment on Python 3.12+ has **no setuptools** - and that is
    exactly the GitHub runner the release workflow uses, where the isolated
    build is the only one that works.
    """
    last = ""
    for extra in (["--no-build-isolation"], []):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                ".",
                "--no-deps",
                "-w",
                str(out_dir),
                *extra,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if result.returncode == 0:
            break
        last = (result.stderr or result.stdout)[-800:]
    else:
        raise RuntimeError("pip wheel gagal (tanpa dan dengan build isolation):\n" + last)
    wheels = sorted(out_dir.glob("*.whl"))
    if not wheels:
        raise RuntimeError("tidak ada wheel yang dihasilkan")
    return wheels[-1]


def check_wheel(wheel: Path) -> tuple[list[str], dict[str, str]]:
    """Return (problems, facts) for the built wheel."""
    problems: list[str] = []
    facts: dict[str, str] = {"wheel": wheel.name, "size_mb": f"{wheel.stat().st_size / 1e6:.2f}"}
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        present = set(names)
        for required in REQUIRED_DATA:
            if required not in present:
                problems.append(f"hilang: {required}")

        services = [n for n in names if "/index_service/" in n and n.endswith(".ini")]
        facts["services"] = str(len(services))
        if len(services) < MIN_SERVICES:
            problems.append(f"index_service hanya {len(services)} INI (min {MIN_SERVICES})")

        entry_points = next(
            (n for n in names if n.endswith("entry_points.txt")), None
        )
        if entry_points is None:
            problems.append("entry_points.txt tidak ada (console script hilang)")
        else:
            text = archive.read(entry_points).decode("utf-8", "replace")
            if "gsa-seo-indexer" not in text:
                problems.append("console script gsa-seo-indexer tidak terdaftar")
            else:
                facts["entry_point"] = "gsa-seo-indexer"

        metadata = next((n for n in names if n.endswith("METADATA")), None)
        if metadata is None:
            problems.append("METADATA tidak ada")
        else:
            text = archive.read(metadata).decode("utf-8", "replace")
            for field in ("Name:", "Version:", "Requires-Python:"):
                if field not in text:
                    problems.append(f"METADATA tanpa {field}")
            for line in text.splitlines():
                if line.startswith("Version:"):
                    facts["version"] = line.split(":", 1)[1].strip()
                elif line.startswith("Requires-Python:"):
                    facts["python"] = line.split(":", 1)[1].strip()

        modules = [
            n for n in names if n.endswith(".py") and n.startswith("gsa_seo_indexer/")
        ]
        facts["modules"] = str(len(modules))
        if not any(n.endswith("cli.py") for n in modules):
            problems.append("cli.py tidak ikut terpaket")
    return problems, facts


def check_release_workflow() -> list[str]:
    """The publish path must exist and must not carry a stored API token."""
    problems: list[str] = []
    path = ROOT / ".github" / "workflows" / "release.yml"
    facts: list[str] = []
    if not path.exists():
        return ["workflow rilis tidak ada"]
    text = path.read_text(encoding="utf-8")
    if "pypi-publish" not in text:
        problems.append("workflow rilis tidak memakai pypa/gh-action-pypi-publish")
    if "id-token: write" not in text:
        problems.append("workflow rilis tidak memakai trusted publishing (id-token)")
    if re.search(r"password:\s*\$\{\{\s*secrets\.", text):
        problems.append("workflow rilis menyimpan token PyPI sebagai secret")
    if "twine check" not in text:
        problems.append("workflow rilis tidak menjalankan twine check")
    facts.append("workflow ok")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bangun wheel dan periksa isinya.")
    parser.add_argument("--keep", action="store_true", help="simpan wheel di build/wheel/")
    args = parser.parse_args(argv)

    out_dir = (ROOT / "build" / "wheel") if args.keep else Path(tempfile.mkdtemp())
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        wheel = build_wheel(out_dir)
        problems, facts = check_wheel(wheel)
    except Exception as exc:  # noqa: BLE001 - the message is the report
        print(f"[FAIL] paket: {exc}")
        return 1
    problems += check_release_workflow()

    summary = ", ".join(f"{key}={value}" for key, value in facts.items())
    if problems:
        print(f"[FAIL] paket: {summary}")
        for problem in problems:
            print(f"       - {problem}")
        return 1
    print(f"[PASS] paket: {summary}, data lengkap, entry point ok, rilis ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
