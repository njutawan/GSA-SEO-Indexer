"""An asyncio transport: one event loop instead of 50 threads.

The thread pool is bound by two things that have nothing to do with the CPU:
each thread costs ~8 MB of stack, and every request blocks its thread for the
whole round trip.  With 11.000 targets that means 50 concurrent requests at
most - the concurrency is bought with memory.  An event loop can hold thousands
of requests in flight for a few MB.

What this module does **not** do is invent a second set of semantics: the URL
is built by ``PingTarget.with_payload``/``post_body``, the decision comes from
``_classify_link``, the dead-host cache, the gzip decoder and the retry policy
are the same objects the thread transport uses.  The differences are only in
the plumbing:

* responses are read with a small HTTP/1.1 parser: status line, headers, then
  ``Content-Length``, chunked, or until EOF;
* connections are either dropped after every request (default, like urllib) or
  kept in a small per-host pool when ``reuse_connections`` is on - the same
  ``--pool`` switch the thread transport uses, now written for the loop;
* proxies speak the two shapes a proxy understands: the absolute request line
  for plain HTTP, and a ``CONNECT`` tunnel for HTTPS (the TLS handshake then
  happens through the tunnel, verify-first with the same per-host fallback).

Everything is opt-in through ``Settings.transport = "asyncio"``.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import ssl
import time
import urllib.parse
from typing import TYPE_CHECKING, Iterable, Sequence

from .httpclient import decode_content, needs_ssl_fallback, remember_ssl_fallback, tls_context

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .engine import Indexer, PingTarget, Result

MAX_REDIRECTS = 5


class _Response:
    """Status, headers and body of one request."""

    __slots__ = ("status", "headers", "body", "error")

    def __init__(self, status: int = 0, headers=None, body: bytes = b"", error: str = "") -> None:
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.error = error


class _Conn:
    """One open connection, plus the key it belongs to in the pool."""

    __slots__ = ("reader", "writer", "stamp")

    def __init__(self, reader, writer) -> None:
        self.reader = reader
        self.writer = writer
        self.stamp = time.monotonic()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.writer.close()

    @property
    def usable(self) -> bool:
        try:
            return not self.writer.is_closing()
        except Exception:  # pragma: no cover - a broken writer is not usable
            return False


class _Pool:
    """Free keep-alive connections per (scheme, host, port, proxy).

    The event loop is single-threaded, so this needs no lock: ``take`` and
    ``give`` never await, and a connection is only ever handed to one
    coroutine at a time.  The limits mirror ``httpclient._Pool`` so a run
    behaves the same whichever transport it uses.
    """

    def __init__(self, max_per_host: int = 2, max_total: int = 96, idle_ttl: float = 20.0) -> None:
        self.max_per_host = max_per_host
        self.max_total = max_total
        self.idle_ttl = idle_ttl
        self._free: dict[tuple, list[_Conn]] = {}
        self.made = 0
        self.reused = 0

    # -- housekeeping ----------------------------------------------------
    def _expire(self, now: float) -> None:
        for key, bucket in list(self._free.items()):
            keep = [conn for conn in bucket if now - conn.stamp <= self.idle_ttl]
            for conn in bucket:
                if conn not in keep:
                    conn.close()
            if keep:
                self._free[key] = keep
            else:
                self._free.pop(key, None)

    def take(self, key: tuple) -> _Conn | None:
        self._expire(time.monotonic())
        bucket = self._free.get(key)
        while bucket:
            conn = bucket.pop()
            if conn.usable:
                self.reused += 1
                return conn
            conn.close()
        if not bucket:
            self._free.pop(key, None)
        return None

    def give(self, key: tuple, conn: _Conn) -> None:
        if len(self._free.get(key, ())) >= self.max_per_host:
            conn.close()
            return
        if sum(len(v) for v in self._free.values()) >= self.max_total:
            conn.close()
            return
        self._free.setdefault(key, []).append(conn)

    def count(self) -> int:
        return sum(len(bucket) for bucket in self._free.values())

    def close(self) -> None:
        for bucket in self._free.values():
            for conn in bucket:
                conn.close()
        self._free.clear()


def _split(url: str):
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "http").lower()
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return scheme, host, port, path, parts.netloc


class AsyncTransport:
    """Runs the submission work of an ``Indexer`` on one event loop."""

    def __init__(self, indexer: "Indexer", concurrency: int | None = None) -> None:
        self.indexer = indexer
        self.settings = indexer.settings
        self.concurrency = max(1, concurrency or self.settings.threads)
        self._sem = asyncio.Semaphore(self.concurrency)
        # Same limits as the thread transport's pool, so --pool means the same
        # thing on both transports.
        self.pool = _Pool(
            max_per_host=max(1, self.settings.pool_per_host),
            max_total=max(1, self.settings.pool_max_total),
        )
        self.requests = 0
        self.retries = 0
        self.proxies_retired = 0

    # -- proxies ----------------------------------------------------------
    @staticmethod
    def parse_proxy(raw: str) -> tuple[str, int]:
        """Split a proxy address into ``(host, port)``.

        Accepts what the CLI and config.ini accept: ``host:port``,
        ``http://host:port``.  Anything that is not an http(s) proxy is
        refused instead of being silently used as a SOCKS server.
        """
        text = (raw or "").strip()
        if not text:
            raise ValueError("proxy kosong")
        parts = urllib.parse.urlsplit(text if "//" in text else f"//{text}")
        scheme = (parts.scheme or "http").lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"skema proxy {scheme!r} tidak didukung (hanya http/https)")
        host = parts.hostname or ""
        if not host:
            raise ValueError(f"proxy tanpa host: {raw!r}")
        return host, parts.port or 8080

    async def _connect_tunnel(
        self, proxy: str, host: str, port: int, verify: bool
    ) -> tuple:
        """Open ``host:port`` through an HTTP proxy using ``CONNECT``.

        The tunnel is a raw socket first (the CONNECT exchange is plain
        HTTP/1.1), then the TLS handshake is layered on top of it - which is
        why the socket is created with ``socket.create_connection`` in an
        executor and handed to ``open_connection(sock=...)`` instead of using
        the streams API twice.
        """
        loop = asyncio.get_running_loop()
        proxy_host, proxy_port = self.parse_proxy(proxy)
        timeout = self.settings.connect_timeout or self.settings.timeout
        sock = await loop.run_in_executor(
            None, socket.create_connection, (proxy_host, proxy_port), timeout
        )
        sock.setblocking(False)
        try:
            request = (
                f"CONNECT {host}:{port} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Proxy-Connection: keep-alive\r\n\r\n"
            ).encode("latin-1")
            await asyncio.wait_for(loop.sock_sendall(sock, request), timeout)
            buffer = b""
            while b"\r\n\r\n" not in buffer:
                chunk = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout)
                if not chunk:
                    raise ConnectionError("proxy menutup koneksi saat CONNECT")
                buffer += chunk
                if len(buffer) > 65536:
                    raise ConnectionError("jawaban CONNECT terlalu panjang")
            status_line = buffer.split(b"\r\n", 1)[0].decode("latin-1")
            pieces = status_line.split(" ")
            if len(pieces) < 2 or not pieces[1].isdigit():
                raise ConnectionError(f"jawaban CONNECT tidak terbaca: {status_line[:60]!r}")
            code = int(pieces[1])
            if code != 200:
                raise ConnectionError(f"proxy CONNECT -> {code}")
            return await asyncio.wait_for(
                asyncio.open_connection(
                    sock=sock,
                    ssl=tls_context(verify),
                    server_hostname=host,
                ),
                timeout,
            )
        except BaseException:
            with contextlib.suppress(Exception):
                sock.close()
            raise

    # -- low level -------------------------------------------------------
    async def _connect(self, scheme: str, host: str, port: int, proxy: str = ""):
        """Open a connection, verifying the certificate unless the host failed."""
        timeout = self.settings.connect_timeout or self.settings.timeout
        if proxy:
            if scheme != "https":
                # Plain HTTP through a proxy: the proxy forwards the request
                # itself, so there is no TLS to set up and no tunnel.
                proxy_host, proxy_port = self.parse_proxy(proxy)
                return await asyncio.wait_for(
                    asyncio.open_connection(proxy_host, proxy_port), timeout
                )
            verify = not needs_ssl_fallback(host)
            try:
                return await self._connect_tunnel(proxy, host, port, verify)
            except ssl.SSLCertVerificationError:
                if not verify:  # pragma: no cover - cannot happen twice
                    raise
                remember_ssl_fallback(host)
                return await self._connect_tunnel(proxy, host, port, False)
        if scheme != "https":
            return await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout
            )
        verify = not needs_ssl_fallback(host)
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(
                    host, port, ssl=tls_context(verify), server_hostname=host
                ),
                timeout,
            )
        except ssl.SSLCertVerificationError:
            if not verify:  # pragma: no cover - cannot happen twice
                raise
            # Same policy as the thread transport: remember the host, then
            # talk to it without verification until the entry expires.
            remember_ssl_fallback(host)
            return await asyncio.wait_for(
                asyncio.open_connection(
                    host, port, ssl=tls_context(False), server_hostname=host
                ),
                timeout,
            )

    async def _read_headers(self, reader, timeout: float):
        line = await asyncio.wait_for(reader.readline(), timeout)
        if not line:
            raise ConnectionError("empty reply")
        parts = line.decode("latin-1").split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise ConnectionError(f"bad status line: {line[:60]!r}")
        status = int(parts[1])
        headers: dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout)
            if line in (b"\r\n", b"\n", b""):
                break
            key, _, value = line.decode("latin-1").partition(":")
            headers[key.strip().lower()] = value.strip()
        return status, headers

    async def _read_body(self, reader, headers, limit: int, timeout: float) -> bytes:
        encoding = headers.get("transfer-encoding", "").lower()
        if "chunked" in encoding:
            return await self._read_chunked(reader, limit, timeout)
        length = headers.get("content-length", "")
        if length.isdigit():
            want = min(int(length), limit)
            if want <= 0:
                return b""
            try:
                return await asyncio.wait_for(reader.readexactly(want), timeout)
            except asyncio.IncompleteReadError as exc:
                return exc.partial
        try:
            return await asyncio.wait_for(reader.read(limit), timeout)
        except asyncio.TimeoutError:
            return b""

    async def _read_chunked(self, reader, limit: int, timeout: float) -> bytes:
        out = bytearray()
        while len(out) < limit:
            line = await asyncio.wait_for(reader.readline(), timeout)
            if not line:
                break
            size_text = line.split(b";")[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError:
                break
            if size == 0:
                # trailer: an empty line ends the message
                with contextlib.suppress(asyncio.TimeoutError, ConnectionError):
                    await asyncio.wait_for(reader.readline(), timeout)
                break
            take = min(size, limit - len(out))
            try:
                out += await asyncio.wait_for(reader.readexactly(take), timeout)
            except asyncio.IncompleteReadError as exc:
                out += exc.partial
                break
            if take < size:
                break  # the cap was hit; the rest is not needed
            with contextlib.suppress(asyncio.TimeoutError, ConnectionError):
                await asyncio.wait_for(reader.readexactly(2), timeout)
        return bytes(out)

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        body: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str = "",
    ) -> _Response:
        """One request, following redirects the way the other transports do."""
        current = url
        current_method = method
        current_body = body
        for _hop in range(MAX_REDIRECTS + 1):
            resp = await self._fetch_once(
                current,
                current_method,
                current_body,
                headers or self.indexer._headers(),
                proxy,
            )
            if resp.error:
                return resp
            if self.settings.follow_redirects and resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("location") or resp.headers.get("Location")
                if not location:
                    return resp
                current = urllib.parse.urljoin(current, location)
                if resp.status in (301, 302, 303):
                    current_method, current_body = "GET", None
                continue
            return resp
        return resp

    def _build_request(
        self,
        method: str,
        path: str,
        netloc: str,
        headers: dict[str, str],
        payload: bytes | None,
        absolute: bool,
        keep_alive: bool,
    ) -> bytes:
        """Render the request line + headers (+ body) for one attempt."""
        target = f"http://{netloc}{path}" if absolute else path
        lines = [f"{method} {target} HTTP/1.1", f"Host: {netloc}"]
        for key, value in headers.items():
            lines.append(f"{key}: {value}")
        if payload is not None:
            lines.append("Content-Type: application/x-www-form-urlencoded")
            lines.append(f"Content-Length: {len(payload)}")
        lines.append("Connection: keep-alive" if keep_alive else "Connection: close")
        request = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        if payload:
            request += payload
        return request

    async def _fetch_once(
        self,
        url: str,
        method: str,
        body: str | None,
        headers: dict[str, str],
        proxy: str = "",
    ) -> _Response:
        scheme, host, port, path, netloc = _split(url)
        if not host:
            return _Response(error="no host in URL")
        read_timeout = self.settings.read_timeout or self.settings.timeout
        payload = body.encode("utf-8") if body is not None else None
        # A plain-HTTP request through a proxy carries the absolute URL in the
        # request line; https goes through a CONNECT tunnel and keeps the
        # origin form.  That is the difference the proxy sees.
        absolute = bool(proxy) and scheme != "https"
        pooling = bool(self.settings.reuse_connections)
        key = (scheme, host, port, proxy or "")

        self.requests += 1
        last_error = "timeout"
        # Two attempts: the first may grab a keep-alive connection the server
        # already closed, and a pooled connection gives no warning before it
        # is written to.  The thread pool does exactly the same.
        for attempt in range(2):
            conn = self.pool.take(key) if pooling else None
            reused = conn is not None
            request = self._build_request(
                method, path, netloc, headers, payload, absolute, keep_alive=pooling
            )
            try:
                if conn is None:
                    reader, writer = await self._connect(scheme, host, port, proxy)
                    conn = _Conn(reader, writer)
                    self.pool.made += 1
                writer = conn.writer
                writer.write(request)
                await asyncio.wait_for(writer.drain(), read_timeout)
                status, response_headers = await self._read_headers(conn.reader, read_timeout)
                raw = await self._read_body(
                    conn.reader, response_headers, self.settings.max_body_bytes, read_timeout
                )
                decoded = decode_content(
                    raw,
                    response_headers.get("content-encoding", ""),
                    self.settings.max_body_bytes,
                )
                if pooling and self._reusable(response_headers, raw):
                    conn.stamp = time.monotonic()
                    self.pool.give(key, conn)
                else:
                    conn.close()
                return _Response(status=status, headers=response_headers, body=decoded)
            except asyncio.TimeoutError:
                last_error = "timeout"
                self._drop(conn)
                return _Response(error="timeout")
            except ssl.SSLCertVerificationError as exc:  # pragma: no cover - handled in _connect
                self._drop(conn)
                return _Response(error=f"SSLCertVerificationError: {exc}")
            except Exception as exc:  # noqa: BLE001 - a failed host never kills the run
                last_error = f"{type(exc).__name__}: {exc}"[:160]
                self._drop(conn)
                if attempt == 0 and (reused or not pooling):
                    # A stale keep-alive connection (or a connection the
                    # server closed early) is worth exactly one retry.
                    continue
                return _Response(error=last_error)
        return _Response(error=last_error)

    @staticmethod
    def _drop(conn: "_Conn | None") -> None:
        """Close a connection if one was opened (``None`` before _connect)."""
        if conn is not None:
            conn.close()

    def _reusable(self, headers: dict[str, str], raw: bytes) -> bool:
        """Can this connection carry another request?

        Only when the server did not ask to close it and the body was read
        completely: a body truncated at ``max_body_bytes`` leaves bytes in the
        socket, and the next request would read them as its status line.
        """
        if headers.get("connection", "").lower() == "close":
            return False
        if headers.get("transfer-encoding", "").lower().startswith("chunked"):
            return len(raw) < self.settings.max_body_bytes
        length = headers.get("content-length", "")
        if length.isdigit():
            return len(raw) == int(length)
        # No length and no chunking: the body ended at EOF, so the connection
        # is gone whether we like it or not.
        return False

    async def _sleep_interruptible(self, delay: float) -> bool:
        """Sleep in slices; ``True`` when the run was stopped meanwhile."""
        deadline = delay
        while deadline > 0:
            if self.indexer._stop.is_set():
                return True
            step = min(0.2, deadline)
            await asyncio.sleep(step)
            deadline -= step
        return self.indexer._stop.is_set()

    # -- one work item ---------------------------------------------------
    async def submit(self, target: "PingTarget", payloads: Sequence[str]) -> list["Result"]:
        """Mirror of ``Indexer.submit_one``/``submit_batch`` for the loop."""
        from .engine import RETRY_STATUSES, Result, _classify_link, host_of, retry_delay

        indexer = self.indexer
        joined = list(payloads)
        if len(joined) > 1:
            separator = "\n" if target.mode == "query" else ","
            url = target.with_payload(separator.join(joined))
            sent = separator.join(joined)
        else:
            url = target.with_payload(joined[0])
            sent = joined[0]
        method = target.http_method
        body = target.post_body(sent) if method == "POST" else None
        host = host_of(url)
        started = time.perf_counter()
        # One proxy per item, exactly like submit_one(): a proxy that fails is
        # retired so the rest of the run does not keep paying for it.
        proxy = indexer._pick_proxy() or ""

        if self.settings.skip_dead_hosts and host and indexer.host_cache.is_dead(host):
            with indexer._lock:
                indexer.skipped_dead += 1
            return [
                Result(
                    target=payload,
                    url=url,
                    status="skipped",
                    code=0,
                    error="host known dead",
                )
                for payload in joined
            ]

        attempts = 1 + max(0, int(self.settings.retries))
        resp = _Response()
        for attempt in range(attempts):
            last = attempt == attempts - 1
            resp = await self.fetch(url, method=method, body=body, proxy=proxy)
            if resp.status in RETRY_STATUSES and self.settings.retries > 0:
                delay = None if last else retry_delay(
                    attempt, resp.headers, self.settings.retry_max_wait
                )
                if delay is not None:
                    self.retries += 1
                    if await self._sleep_interruptible(delay):
                        break
                    continue
                elapsed = int((time.perf_counter() - started) * 1000)
                if host:
                    indexer.host_cache.record_failure(host, f"HTTP {resp.status}")
                return [
                    Result(
                        target=payload,
                        url=url,
                        status="failed",
                        code=resp.status,
                        elapsed_ms=elapsed,
                        error=f"HTTP {resp.status} (retry habis)",
                    )
                    for payload in joined
                ]
            break

        elapsed = int((time.perf_counter() - started) * 1000)
        if resp.error:
            # A proxy that fails says nothing about the target: do not mark the
            # host dead on its behalf (the thread transport has the same rule).
            if proxy:
                indexer._retire_proxy(proxy)
                self.proxies_retired += 1
            elif host:
                indexer.host_cache.record_failure(host, resp.error)
            return [
                Result(
                    target=payload,
                    url=url,
                    status="failed",
                    code=0,
                    elapsed_ms=elapsed,
                    error=resp.error,
                )
                for payload in joined
            ]
        if resp.status >= 400:
            if host:
                if resp.status < 500:
                    indexer.host_cache.record_success(host, elapsed)
                else:
                    indexer.host_cache.record_failure(host, f"HTTP {resp.status}")
            exhausted = (
                " (retry habis)"
                if resp.status in RETRY_STATUSES and self.settings.retries
                else ""
            )
            return [
                Result(
                    target=payload,
                    url=url,
                    status="failed",
                    code=resp.status,
                    elapsed_ms=elapsed,
                    error=f"HTTP {resp.status}{exhausted}",
                )
                for payload in joined
            ]
        if host:
            indexer.host_cache.record_success(host, elapsed)
        text = resp.body.decode("utf-8", "replace")
        out = []
        for payload in joined:
            kind = _classify_link(text, payload)
            out.append(
                Result(
                    target=payload,
                    url=url,
                    status="submitted",
                    code=resp.status,
                    indexable=(kind == "indexed"),
                    listed=(kind == "listed"),
                    elapsed_ms=elapsed,
                )
            )
        return out

    # -- the loop --------------------------------------------------------
    async def _worker(self, item, on_result) -> None:
        from .engine import Result

        indexer = self.indexer
        target, payloads = item
        if indexer._stop.is_set():
            results = [
                Result(target=p, url=target.url, status="skipped", error="stopped")
                for p in payloads
            ]
        else:
            async with self._sem:
                try:
                    results = await self.submit(target, payloads)
                except Exception as exc:  # noqa: BLE001 - never kill the loop
                    results = [
                        Result(
                            target=p,
                            url=target.url,
                            status="failed",
                            error=f"{type(exc).__name__}: {exc}"[:160],
                        )
                        for p in payloads
                    ]
                if self.settings.delay:
                    await asyncio.sleep(self.settings.delay)
                elif self.settings.random_delay:
                    await asyncio.sleep(0.01)
        for res in results:
            indexer._record(res, on_result)

    async def close(self) -> None:
        """Drop every pooled connection (called when the run ends)."""
        self.pool.close()

    async def run(self, work: Iterable[tuple], on_result=None) -> None:
        """Run every work item, keeping ``concurrency`` requests in flight."""
        queue: asyncio.Queue = asyncio.Queue()
        for item in work:
            queue.put_nowait(item)

        async def drain() -> None:
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                if self.indexer._stop.is_set():
                    # Same contract as the thread transport: work that was
                    # never started leaves no row at all, and the item already
                    # in hand is recorded as "skipped - stopped".
                    await self._worker(item, on_result)
                    return
                await self._worker(item, on_result)

        workers = [
            asyncio.ensure_future(drain())
            for _ in range(min(self.concurrency, max(1, queue.qsize())))
        ]
        try:
            await asyncio.gather(*workers)
        except asyncio.CancelledError:  # pragma: no cover - stop is cooperative
            for worker in workers:
                worker.cancel()
            raise
        finally:
            # Pooled sockets must not outlive the run: they would be closed by
            # the garbage collector after the loop is gone, which produces
            # "unclosed transport" noise instead of a clean shutdown.
            await self.close()


def run_work(indexer: "Indexer", work: Iterable[tuple], on_result=None) -> None:
    """Blocking entry point used by ``Indexer.run``."""
    transport = AsyncTransport(indexer)
    asyncio.run(transport.run(work, on_result))
