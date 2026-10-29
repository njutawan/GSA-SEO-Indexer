"""Frozen-aware path resolution.

Two kinds of locations matter:

* ``runtime_dir()``  — writable per-user state (config.ini, keys.json,
  host_cache.json, last_queue.txt, reports/). Next to the .exe when frozen;
  when pip-installed, in the OS user-data directory; otherwise the package
  directory (source checkout). Inside a PyInstaller onefile bundle the
  package directory is a temp extraction folder that gets deleted on exit,
  so state must never live there — and for pip installs the package lives in
  site-packages, where writing user state is wrong too.
* ``resource_dir()`` — read-only bundled data shipped inside the bundle or
  the wheel (data/, index_service/). ``sys._MEIPASS`` points at the
  extraction folder while frozen.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_installed = None


def _is_installed() -> bool:
    """True when this very file is imported from site-packages, i.e. the
    pip-installed copy rather than a source checkout.

    Distribution metadata alone is not a safe signal: a source checkout that
    carries (or runs next to) ``gsa_seo_indexer.egg-info`` makes the
    distribution discoverable from that directory without anything being
    installed, which used to flip the runtime dir to the OS user-data
    folder and silently read/write the wrong config.ini.
    """
    global _installed
    if _installed is None:
        here = str(Path(__file__).resolve()).lower()
        _installed = "site-packages" in here or "dist-packages" in here
    return _installed


def runtime_dir() -> Path:
    """Directory for writable state; created on first use."""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    elif _is_installed():
        if os.name == "nt":
            root = Path(os.environ.get("APPDATA", Path.home())) / "GSA-SEO-Indexer"
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "GSA-SEO-Indexer"
        else:
            root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "gsa-seo-indexer"
    else:
        root = Path(__file__).resolve().parent
    root.mkdir(parents=True, exist_ok=True)
    return root


def resource_dir() -> Path:
    """Directory for bundled read-only resources (data/, index_service/)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "gsa_seo_indexer"
    return Path(__file__).resolve().parent
