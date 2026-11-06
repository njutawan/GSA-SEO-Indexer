"""Result storage that spills to disk instead of growing without bound.

A run of 50 URLs against the recovered list produces ~660k ``Result`` rows.
Kept as a plain list that is ~115 MB of RAM - live for the whole run, and the
report is only written once the run ends.  ``ResultStore`` keeps a window in
memory and appends the older rows to one temporary pickle file, in order, so
``write_report`` (and anything else that iterates) sees exactly the same
sequence a list would have produced.

The window is deliberately generous: below it nothing is written to disk at
all, so an ordinary run (a few thousand rows) behaves like the old list.
"""

from __future__ import annotations

import os
import pickle
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator


class ResultStore:
    """Append-mostly, list-like log of results with bounded memory."""

    def __init__(
        self,
        max_memory: int = 20_000,
        directory: str | os.PathLike | None = None,
        pack=None,
        unpack=None,
    ) -> None:
        # ``max_memory <= 0`` keeps everything in RAM, like the old list.
        self.max_memory = int(max_memory) if max_memory and max_memory > 0 else 0
        # Rows are pickled through these two hooks.  Pickling a plain tuple is
        # 10x cheaper than pickling the dataclass (0.3 us vs 3.2 us per row)
        # and the file is 27% smaller, so the engine spills tuples.
        self._pack = pack or (lambda row: row)
        self._unpack = unpack or (lambda row: row)
        self._directory = directory
        self._rows: list[Any] = []
        self._spilled = 0
        self._file = None
        self._path: Path | None = None
        self._lock = threading.Lock()
        self.spills = 0

    # -- writing ---------------------------------------------------------
    def append(self, result: Any) -> None:
        with self._lock:
            self._rows.append(result)
            if self.max_memory and len(self._rows) >= self.max_memory:
                self._spill_locked()

    def extend(self, results: Iterable[Any]) -> None:
        for result in results:
            self.append(result)

    def _spill_locked(self) -> None:
        if not self._rows:
            return
        if self._file is None:
            handle, name = tempfile.mkstemp(
                prefix="gsa-results-", suffix=".pkl", dir=self._directory
            )
            self._file = os.fdopen(handle, "wb")
            self._path = Path(name)
        pickle.dump(
            [self._pack(row) for row in self._rows],
            self._file,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
        self._spilled += len(self._rows)
        self._rows = []
        self.spills += 1

    # -- reading (same order as a list) ----------------------------------
    def __len__(self) -> int:
        return self._spilled + len(self._rows)

    def __bool__(self) -> bool:
        return bool(len(self))

    def __iter__(self) -> Iterator[Any]:
        with self._lock:
            path = self._path
            file_handle = self._file
            window = list(self._rows)
        if file_handle is not None and path is not None:
            file_handle.flush()
            with path.open("rb") as reader:
                while True:
                    try:
                        chunk = pickle.load(reader)
                    except EOFError:
                        break
                    for row in chunk:
                        yield self._unpack(row)
        yield from window

    def __getitem__(self, index):
        # Indexing costs a full replay; the callers that index are tests and
        # the GUI's small service runs.
        return list(self)[index]

    # -- lifecycle -------------------------------------------------------
    def clear(self) -> None:
        """Drop every row (and the spill file) but keep the object usable."""
        with self._lock:
            self._close_locked()
            self._rows = []
            self._spilled = 0
            self.spills = 0

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None
        if self._path is not None:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass
            self._path = None

    # -- introspection ---------------------------------------------------
    @property
    def spilled(self) -> int:
        """Rows living in the spill file."""
        return self._spilled

    @property
    def in_memory(self) -> int:
        """Rows still held in RAM."""
        return len(self._rows)

    @property
    def path(self) -> Path | None:
        return self._path
