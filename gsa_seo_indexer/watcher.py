"""Watch local files for new URLs and submit them as they appear.

GSA SEO Indexer can watch log files produced by other SEO tools and submit
newly found backlinks the moment they are written.  This module does the same
for a set of files or a directory.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .engine import Indexer, PingTarget, read_url_file


class UrlWatcher:
    """Track files and report URLs that are new since the last poll."""

    def __init__(self, paths: Sequence[str | os.PathLike], poll_interval: float = 5.0) -> None:
        self.paths = [Path(p) for p in paths]
        self.poll_interval = poll_interval
        self._offsets: dict[Path, int] = {}
        self._seen: set[str] = set()

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
                found.append(line)
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
