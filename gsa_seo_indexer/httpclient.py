"""A small HTTP client with per-host connection pooling.

Profiling the indexer showed that a TCP connect plus a TLS handshake account
for about a third of the time spent per request, and that ``urllib`` opens a
fresh connection for every single request.  Every server that the indexer
talks to was checked and supports HTTP keep-alive, so keeping the
connection open between requests removes that cost.

The client is deliberately small: it does one GET per call, follows a
bounded number of redirects, and returns the body plus the status.  A
connection that the server closed is transparently retried once on a fresh
connection, which is the common case with servers that keep a short
keep-alive timeout.
"""

from __future__ import annotations

import http.client
import select
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_BODY = 512 * 1024


@dataclass
class Response:
    status: int
    body: str
    headers: dict = field(default_factory=dict)
    elapsed_ms: int = 0
    final_url: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400


class _Pool:
    """Free connections per (scheme, host, port)."""

    def __init__(self, max_per_host: int = 2, max_total: int = 96, idle_ttl: float = 20.0) -> None:
        self.max_per_host = max_per_host
        self.max_total = max_total
        self.idle_ttl = idle_ttl
        self._free: dict[tuple[str, str, str, int], list[tuple[float, http.client.HTTPConnection]]] = {}
        self._lock = threading.Lock()
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    # -- housekeeping ----------------------------------------------------
    def _expire(self, now: float) -> None:
        for key, bucket in list(self._free.items()):
            keep = []
            for stamp, conn in bucket:
                if now - stamp > self.idle_ttl:
                    try:
                        conn.close()
                    except Exception:
                        pass
                else:
                    keep.append((stamp, conn))
            if keep:
                self._free[key] = keep
            else:
                self._free.pop(key, None)

    def take(self, scheme: str, host: str, port: int, proxy: str = "") -> http.client.HTTPConnection | None:
        key = (scheme, proxy, host, port)
        now = time.time()
        with self._lock:
            self._expire(now)
            bucket = self._free.get(key)
            while bucket:
                stamp, conn = bucket.pop()
                if self._usable(conn):
                    return conn
                try:
                    conn.close()
                except Exception:
                    pass
            return None

    def give(self, scheme: str, host: str, port: int, conn: http.client.HTTPConnection, proxy: str = "") -> None:
        key = (scheme, proxy, host, port)
        with self._lock:
            total = sum(len(v) for v in self._free.values())
            if total >= self.max_total:
                try:
                    conn.close()
                except Exception:
                    pass
                return
            bucket = self._free.setdefault(key, [])
            if len(bucket) >= self.max_per_host:
                try:
                    conn.close()
                except Exception:
                    pass
                return
            bucket.append((time.time(), conn))

    def _usable(self, conn: http.client.HTTPConnection) -> bool:
        """Cheap check that the pooled connection is still open.

        An SSL socket cannot be peeked, so the check is done with a
        non-blocking select: readable with no data means the server closed
        the connection, not readable means it is still alive.
        """
        sock = getattr(conn, "sock", None)
        if sock is None:
            return False
        try:
            ready, _, _ = select.select([sock], [], [], 0)
            if not ready:
                return True  # nothing waiting: the connection is healthy
            try:
                sock.setblocking(False)
                data = sock.recv(1)
                return data != b""
            finally:
                sock.setblocking(True)
        except (OSError, ValueError):
            return False

    def close(self) -> None:
        with self._lock:
            for bucket in self._free.values():
                for _stamp, conn in bucket:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self._free.clear()


class PooledHttpClient:
    """GET requests over pooled, keep-alive connections."""

    def __init__(
        self,
        timeout: float = 10.0,
        connect_timeout: float = 6.0,
        max_per_host: int = 2,
        max_total: int = 96,
        max_redirects: int = 5,
        user_agents: list[str] | None = None,
        proxy: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.max_redirects = max_redirects
        # A proxy changes the connection target, so pooled connections are
        # keyed by (scheme, proxy, host, port) instead of (scheme, host, port).
        self.proxy = proxy
        # A short UA like "Mozilla/5.0" is refused by some of these sites
        # (Mod_Security answers 406), so the full browser string is used.
        self.user_agents = user_agents or [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ]
        self.pool = _Pool(max_per_host=max_per_host, max_total=max_total)
        self.requests = 0
        self.connections_made = 0
        self.connections_reused = 0
        self._lock = threading.Lock()

    # -- stats ------------------------------------------------------------
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "requests": self.requests,
                "connections": self.connections_made,
                "reused": self.connections_reused,
            }

    # -- request ----------------------------------------------------------
    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> Response:
        started = time.perf_counter()
        current = url
        for _hop in range(self.max_redirects + 1):
            resp = self._get_once(current, headers)
            if resp.error and resp.status == 0:
                return resp
            if follow_redirects and resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location") or resp.headers.get("location")
                if not location:
                    resp.elapsed_ms = int((time.perf_counter() - started) * 1000)
                    return resp
                current = _join(current, location)
                continue
            resp.elapsed_ms = int((time.perf_counter() - started) * 1000)
            resp.final_url = current
            return resp
        resp.elapsed_ms = int((time.perf_counter() - started) * 1000)
        resp.final_url = current
        return resp

    def _get_once(self, url: str, headers: dict[str, str] | None) -> Response:
        parts = urlsplit(url)
        scheme = (parts.scheme or "http").lower()
        host = parts.hostname or ""
        port = parts.port or DEFAULT_PORTS.get(scheme, 80)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        if not host:
            return Response(status=0, body="", error="no host in URL")

        # The header set matters: some of these sites answer 406 when the
        # Accept-Encoding header is missing, and the browser-like set is
        # what the original GSA program sends.
        req_headers = {
            "Host": parts.netloc,
            "User-Agent": self.user_agents[0],
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
            # "Upgrade-Insecure-Requests" is deliberately NOT sent: some of
            # these sites run Mod_Security and answer 406 when they see it.
        }
        if headers:
            req_headers.update(headers)

        with self._lock:
            self.requests += 1

        last_error = ""
        for attempt in range(2):  # one retry, for a connection the server closed
            conn = self.pool.take(scheme, host, port, self.proxy or "")
            reused = conn is not None
            try:
                if conn is None:
                    conn = self._connect(scheme, host, port)
                    with self._lock:
                        self.connections_made += 1
                else:
                    with self._lock:
                        self.connections_reused += 1
                conn.timeout = self.timeout
                if self.proxy and scheme != "https":
                    # plain HTTP through a proxy: the request line carries
                    # the absolute URL
                    conn.request("GET", f"http://{parts.netloc}{path}", headers=req_headers)
                else:
                    conn.request("GET", path, headers=req_headers)
                raw = conn.getresponse()
                body = raw.read(MAX_BODY)
                hdrs = {k.lower(): v for k, v in raw.getheaders()}
                status = raw.status
                # keep the connection only when the server allows it
                if hdrs.get("connection", "").lower() == "close" or raw.will_close:
                    try:
                        conn.close()
                    except Exception:
                        pass
                else:
                    self.pool.give(scheme, host, port, conn, self.proxy or "")
                return Response(
                    status=status,
                    body=body.decode("utf-8", "replace"),
                    headers=hdrs,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"[:160]
                try:
                    conn.close()
                except Exception:
                    pass
                if attempt == 0:
                    continue  # the pooled connection may have been closed by the server
        return Response(status=0, body="", error=last_error)

    def _connect(self, scheme: str, host: str, port: int) -> http.client.HTTPConnection:
        if self.proxy:
            return self._connect_via_proxy(scheme, host, port)
        if scheme == "https":
            conn = http.client.HTTPSConnection(
                host, port, timeout=self.connect_timeout, context=self.pool._ctx
            )
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.connect_timeout)
        conn.connect()
        return conn

    def _connect_via_proxy(self, scheme: str, host: str, port: int) -> http.client.HTTPConnection:
        """Connect through an HTTP proxy.

        For https the tunnel is established with CONNECT first; for plain
        HTTP the request line keeps the absolute form, which is what a
        proxy expects.
        """
        from urllib.parse import urlsplit

        raw = self.proxy or ""
        p = urlsplit(raw if "//" in raw else f"//{raw}")
        p_host = p.hostname or ""
        p_port = p.port or (1080 if p.scheme == "socks5" else 8080)

        if scheme == "https":
            conn = http.client.HTTPSConnection(
                p_host, p_port, timeout=self.connect_timeout, context=self.pool._ctx
            )
            conn.set_tunnel(host, port)
            conn.connect()
            return conn

        conn = http.client.HTTPConnection(p_host, p_port, timeout=self.connect_timeout)
        conn.connect()
        return conn

    def close(self) -> None:
        self.pool.close()


def _join(base: str, location: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, location)
