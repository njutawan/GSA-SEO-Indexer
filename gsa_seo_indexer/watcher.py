"""Watch local files for new URLs and submit them as they appear.

GSA SEO Indexer can watch log files produced by other SEO tools and submit
newly found backlinks the moment they are written.  This module does the same
for a set of files or a directory.
"""

from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path
from typing import Callable, Sequence

from .engine import Indexer, PingTarget


class UrlWatcher:
    """Track files and report URLs that are new since the last poll."""

    def __init__(
        self,
        paths: Sequence[str | os.PathLike],
        poll_interval: float = 5.0,
        max_seen: int = 50_000,
    ) -> None:
        self.paths = [Path(p) for p in paths]
        self.poll_interval = poll_interval
        self._offsets: dict[Path, int] = {}
        # A watch that runs for days sees every URL ever written; the set is
        # therefore bounded, oldest first.  A URL evicted here can only come
        # back if the log repeats it, which append-only logs do not do.
        self.max_seen = max(1_000, max_seen)
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()

    def _expanded(self) -> list[Path]:
        out: list[Path] = []
        for p in self.paths:
            if p.is_dir():
                out.extend(sorted(p.glob("*.txt")))
                out.extend(sorted(p.glob("*.csv")))
            elif p.exists():
                out.append(p)
        return out

    def poll(self) -> list[str]:
        """Return the URLs that appeared since the previous call."""
        found: list[str] = []
        for path in self._expanded():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            start = self._offsets.get(path, 0)
            if size < start:  # file was truncated / rotated
                start = 0
            if size == start:
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(start)
                    chunk = fh.read()
                    self._offsets[path] = fh.tell()
            except OSError:
                continue
            for line in chunk.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "://" not in line:
                    continue
                if line in self._seen:
                    continue
                self._seen.add(line)
                self._seen_order.append(line)
                found.append(line)
        if len(self._seen) > self.max_seen:
            while len(self._seen) > self.max_seen and self._seen_order:
                self._seen.discard(self._seen_order.popleft())
        return found

    def watch(
        self,
        indexer: Indexer,
        targets: Sequence[PingTarget],
        on_urls: Callable[[list[str]], None] | None = None,
        max_iterations: int = 0,
    ) -> int:
        """Poll forever (or for ``max_iterations`` polls) and submit new URLs."""
        iterations = 0
        while not indexer.stopped:
            iterations += 1
            urls = self.poll()
            if urls:
                if on_urls:
                    on_urls(urls)
                indexer.run(urls, targets)
            if max_iterations and iterations >= max_iterations:
                break
            time.sleep(self.poll_interval)
        return iterations
