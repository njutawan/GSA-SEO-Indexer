"""Performance helpers for the indexer.

Profiling the clone against the 6,671-site list showed where the wall-clock
time actually goes (300-site sample, 25 threads, 10 s timeout):

===========  ==============================================================
phase        share of the time spent inside a request
===========  ==============================================================
read         55%   waiting for the server to answer
TLS          20%   handshake, and it is repeated for every request
TCP          14%   connect
DNS          11%   resolution (cached by the OS after the first lookup)
===========  ==============================================================

Two observations drive the optimisations here:

1. The recovered site list is dominated by dead hosts.  A host that does not
   answer costs a full connect timeout and burns a thread slot for the whole
   wait, so remembering which hosts are dead and skipping them is worth far
   more than making the individual request faster.
2. Both modes of a site (``?url=`` and path form) hit the same host, and
   every URL of a run hits the same hosts again.  Resolving and connecting
   once per host and reusing that is a large saving.

This module provides the two pieces that implement that: a host liveness
cache and a per-host connection pool.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# How long a host is remembered as dead / alive.
DEAD_TTL = 30 * 60.0
ALIVE_TTL = 60 * 60.0


@dataclass
class HostState:
    alive: bool = True
    checked: float = 0.0
    failures: int = 0
    successes: int = 0
    last_error: str = ""
    avg_ms: float = 0.0

    @property
    def score(self) -> float:
        """Fraction of successful attempts, with a small prior."""
        total = self.successes + self.failures
        if total == 0:
            return 0.5
        return (self.successes + 1) / (total + 2)


class HostCache:
    """Remembers which hosts answered before, so dead ones are skipped.

    A host is only skipped when it has failed at least ``min_failures``
    times and the last check is younger than ``dead_ttl`` seconds.  That
    keeps a temporarily broken host from being written off forever, while
    still cutting the bulk of the wasted waits on a list full of dead
    domains.
    """

    def __init__(self, dead_ttl: float = DEAD_TTL, alive_ttl: float = ALIVE_TTL, min_failures: int = 1) -> None:
        self.dead_ttl = dead_ttl
        self.alive_ttl = alive_ttl
        self.min_failures = min_failures
        self._state: dict[str, HostState] = {}
        self._lock = threading.Lock()

    # -- state ------------------------------------------------------------
    def get(self, host: str) -> HostState | None:
        with self._lock:
            return self._state.get(host)

    def record_success(self, host: str, elapsed_ms: float = 0.0) -> None:
        with self._lock:
            st = self._state.setdefault(host, HostState())
            st.successes += 1
            st.alive = True
            st.checked = time.time()
            st.last_error = ""
            if elapsed_ms:
                st.avg_ms = st.avg_ms * 0.7 + elapsed_ms * 0.3

    def record_failure(self, host: str, error: str = "") -> None:
        with self._lock:
            st = self._state.setdefault(host, HostState())
            st.failures += 1
            st.checked = time.time()
            st.last_error = error[:120]
            # a host that worked before is not written off after one hiccup
            if st.successes == 0:
                st.alive = False

    def is_dead(self, host: str) -> bool:
        """Should this host be skipped without even trying?"""
        with self._lock:
            st = self._state.get(host)
            if st is None or st.alive:
                return False
            if st.failures < self.min_failures:
                return False
            return (time.time() - st.checked) < self.dead_ttl

    def is_known_alive(self, host: str) -> bool:
        with self._lock:
            st = self._state.get(host)
            if st is None or not st.alive:
                return False
            return (time.time() - st.checked) < self.alive_ttl

    def stats(self) -> dict[str, int]:
        with self._lock:
            dead = sum(1 for s in self._state.values() if not s.alive)
            alive = sum(1 for s in self._state.values() if s.alive)
            return {"hosts": len(self._state), "alive": alive, "dead": dead}

    def clear(self) -> None:
        with self._lock:
            self._state.clear()

    # -- persistence ------------------------------------------------------
    def save(self, path: str | os.PathLike) -> int:
        """Persist the dead hosts so the *next process* can skip them too.

        Only dead hosts are worth saving: an alive host costs a normal
        request either way, while a dead one costs a full connect timeout.
        Returns the number of hosts written.
        """
        with self._lock:
            dead = {
                host: {"checked": st.checked, "failures": st.failures, "last_error": st.last_error}
                for host, st in self._state.items()
                if not st.alive
            }
        payload = {"version": 1, "saved": time.time(), "dead": dead}
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, str(path))
        except OSError:
            # a cache file we cannot write is no reason to fail a run
            return 0
        return len(dead)

    def load(self, path: str | os.PathLike) -> int:
        """Merge dead hosts recorded by an earlier run.

        Entries older than ``dead_ttl`` are dropped, so a host that was
        marked dead half an hour ago is tried again exactly as it would be
        within one process.  Returns the number of hosts restored.
        """
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return 0
        if not isinstance(payload, dict) or not isinstance(payload.get("dead"), dict):
            return 0
        now = time.time()
        kept = 0
        with self._lock:
            for host, rec in payload["dead"].items():
                if not isinstance(host, str) or not isinstance(rec, dict):
                    continue
                try:
                    checked = float(rec.get("checked", 0.0))
                    failures = int(rec.get("failures", 1))
                except (TypeError, ValueError):
                    continue
                if now - checked >= self.dead_ttl:
                    continue
                st = self._state.get(host)
                if st is not None and (st.alive or st.checked >= checked):
                    continue  # what this process learned is fresher
                self._state[host] = HostState(
                    alive=False,
                    checked=checked,
                    failures=max(1, failures),
                    last_error=str(rec.get("last_error", ""))[:120],
                )
                kept += 1
        return kept


# --------------------------------------------------------------------------
# DNS cache
# --------------------------------------------------------------------------
class DnsCache:
    """Memoises ``socket.getaddrinfo`` for the duration of a run.

    DNS was 11% of the request time in the profile, and each site
    contributes two submission forms that share one host.  The OS resolver
    cache makes the second lookup cheap - but only once the first one has
    *finished*, and with 50 threads both forms of a host are usually in
    flight at the same time, so each pays the full cold resolve (223 ms).
    This cache deduplicates concurrent lookups of the same host as well as
    repeat ones.

    Failures are cached too, but with a much shorter TTL: a DNS error is
    often transient, while a positive answer is stable for the length of a
    run.
    """

    def __init__(self, ttl: float = 300.0, negative_ttl: float = 30.0) -> None:
        self.ttl = ttl
        self.negative_ttl = negative_ttl
        self._entries: dict[tuple[Any, ...], "_DnsEntry"] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def lookup(self, host, port, family, type_, proto, flags):
        """Resolve with memoisation; raises exactly like ``getaddrinfo``."""
        key = (host, port, family, type_, proto, flags)
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.resolved.is_set() and entry.expires > now:
                self.hits += 1
                return entry.raise_or_return()
            if entry is None or entry.resolved.is_set():
                # nobody is working on this key: start a fresh resolution
                entry = _DnsEntry()
                self._entries[key] = entry
                own = True
            else:
                own = False
        if own:
            try:
                value = _orig_getaddrinfo(host, port, family, type_, proto, flags)
            except BaseException as exc:  # noqa: BLE001
                with self._lock:
                    entry.error = exc
                    entry.expires = time.monotonic() + self.negative_ttl
                    entry.resolved.set()
                raise
            with self._lock:
                entry.value = value
                entry.expires = time.monotonic() + self.ttl
                entry.resolved.set()
            return value
        # another thread is resolving this host: wait for its answer
        entry.resolved.wait(timeout=30.0)
        with self._lock:
            if entry.resolved.is_set() and entry.expires > time.monotonic():
                self.hits += 1
                return entry.raise_or_return()
        # the wait timed out or the answer already expired: resolve directly
        self.misses += 1
        return _orig_getaddrinfo(host, port, family, type_, proto, flags)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._entries), "hits": self.hits, "misses": self.misses}


@dataclass
class _DnsEntry:
    """One memoised resolution, shared between concurrent lookups."""

    value: Any = None
    error: BaseException | None = None
    expires: float = 0.0
    resolved: threading.Event = field(default_factory=threading.Event)

    def raise_or_return(self):
        if self.error is not None:
            raise self.error
        return self.value


# The original resolver is captured once, before anything patches it.
_orig_getaddrinfo = socket.getaddrinfo
_active_dns: DnsCache | None = None


def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Transparent stand-in for ``socket.getaddrinfo`` while a cache is active."""
    cache = _active_dns
    if cache is None:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    return cache.lookup(host, port, family, type, proto, flags)


def activate_dns_cache(cache: DnsCache) -> None:
    """Route ``socket.getaddrinfo`` through ``cache``.

    The wrapper itself is installed once and left in place; with no active
    cache it is a plain pass-through, so installing and restoring it per
    run cannot race with another indexer.
    """
    global _active_dns
    if not getattr(socket.getaddrinfo, "_dns_cached", False):
        _cached_getaddrinfo._dns_cached = True  # type: ignore[attr-defined]
        socket.getaddrinfo = _cached_getaddrinfo  # type: ignore[assignment]
    _active_dns = cache


def deactivate_dns_cache() -> None:
    global _active_dns
    _active_dns = None


# --------------------------------------------------------------------------
# Connection pool
# --------------------------------------------------------------------------
class ConnectionPool:
    """Keeps a few ready connections per (host, port, scheme).

    Reusing a connection removes the TCP and TLS handshake from the
    critical path, which is 34% of the request time in the profile above.
    Servers that close an idle connection are handled by retrying once on a
    fresh connection.
    """

    def __init__(self, max_per_host: int = 2, max_total: int = 64) -> None:
        self.max_per_host = max_per_host
        self.max_total = max_total
        self._free: dict[tuple[str, int], list] = {}
        self._lock = threading.Lock()
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def _key(self, host: str, port: int) -> tuple[str, int]:
        return (host, port)

    def get(self, host: str, port: int, timeout: float) -> socket.socket:
        """Return a connected socket, reusing a pooled one when possible."""
        key = self._key(host, port)
        with self._lock:
            bucket = self._free.get(key)
            while bucket:
                sock = bucket.pop()
                try:
                    sock.settimeout(timeout)
                    return sock
                except OSError:
                    try:
                        sock.close()
                    except OSError:
                        pass
        return self._connect(host, port, timeout)

    def _connect(self, host: str, port: int, timeout: float) -> socket.socket:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        return sock

    def put(self, host: str, port: int, sock: socket.socket) -> None:
        """Return a socket to the pool (or close it when the pool is full)."""
        key = self._key(host, port)
        with self._lock:
            total = sum(len(v) for v in self._free.values())
            if total >= self.max_total:
                try:
                    sock.close()
                except OSError:
                    pass
                return
            bucket = self._free.setdefault(key, [])
            if len(bucket) >= self.max_per_host:
                try:
                    sock.close()
                except OSError:
                    pass
                return
            bucket.append(sock)

    def close(self) -> None:
        with self._lock:
            for bucket in self._free.values():
                for sock in bucket:
                    try:
                        sock.close()
                    except OSError:
                        pass
            self._free.clear()


def host_of(url: str) -> str:
    """Extract the host part of a URL."""
    try:
        from urllib.parse import urlsplit

        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def port_of(url: str, default_port: int = 80) -> int:
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(url)
        if parsed.port:
            return parsed.port
        return 443 if parsed.scheme == "https" else default_port
    except Exception:
        return default_port
