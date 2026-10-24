"""Frozen-aware path resolution.

Two kinds of locations matter:

* ``runtime_dir()``  — writable per-user state, next to the .exe when frozen
  (config.ini, keys.json, host_cache.json, last_queue.txt, reports/). Inside a
  PyInstaller onefile bundle the package directory is a temp extraction folder
  that gets deleted on exit, so state must not live there.
* ``resource_dir()`` — read-only bundled data shipped inside the bundle
  (data/, index_service/). ``sys._MEIPASS`` points at the extraction folder
  while frozen.
"""

from __future__ import annotations

import sys
from pathlib import Path


def runtime_dir() -> Path:
    """Directory for writable state: next to the exe when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    """Directory for bundled read-only resources (data/, index_service/)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "gsa_seo_indexer"
    return Path(__file__).resolve().parent
