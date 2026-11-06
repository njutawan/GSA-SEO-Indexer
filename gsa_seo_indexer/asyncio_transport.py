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

* ``Connection: close`` per request: a pool would need per-connection
  bookkeeping in the event loop, and the measurements (OPTIMIZATION-REVIEW
  §12) showed pooling is slower in a wide run anyway;
* proxy support is absent (the thread transport keeps it);
* responses are read with a small HTTP/1.1 parser: status line, headers,
  then ``Content-Length``, chunked, or until EOF.

Everything is opt-in through ``Settings.transport = "asyncio"``.
"""

from __future__ import annotations

import asyncio
import contextlib
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
        self.requests = 0
        self.retries = 0

    # -- low level -------------------------------------------------------
    async def _connect(self, scheme: str, host: str, port: int):
        """Open a connection, verifying the certificate unless the host failed."""
        timeout = self.settings.connect_timeout or self.settings.timeout
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
    ) -> _Response:
        """One request, following redirects the way the other transports do."""
        current = url
        current_method = method
        current_body = body
        for _hop in range(MAX_REDIRECTS + 1):
            resp = await self._fetch_once(
                current, current_method, current_body, headers or self.indexer._headers()
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

    async def _fetch_once(
        self, url: str, method: str, body: str | None, headers: dict[str, str]
    ) -> _Response:
        scheme, host, port, path, netloc = _split(url)
        if not host:
            return _Response(error="no host in URL")
        read_timeout = self.settings.read_timeout or self.settings.timeout
        payload = body.encode("utf-8") if body is not None else None
        lines = [f"{method} {path} HTTP/1.1", f"Host: {netloc}"]
        for key, value in headers.items():
            lines.append(f"{key}: {value}")
        if payload is not None:
            lines.append("Content-Type: application/x-www-form-urlencoded")
            lines.append(f"Content-Length: {len(payload)}")
        # One connection per request: a pool inside the loop would have to
        # track which coroutine owns which socket, and the measurements say
        # pooling does not pay off in a wide run anyway.
        lines.append("Connection: close")
        request = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        if payload:
            request += payload

        self.requests += 1
        writer = None
        try:
            reader, writer = await self._connect(scheme, host, port)
            writer.write(request)
            await asyncio.wait_for(writer.drain(), read_timeout)
            status, response_headers = await self._read_headers(reader, read_timeout)
            raw = await self._read_body(
                reader, response_headers, self.settings.max_body_bytes, read_timeout
            )
            decoded = decode_content(
                raw, response_headers.get("content-encoding", ""), self.settings.max_body_bytes
            )
            return _Response(status=status, headers=response_headers, body=decoded)
        except asyncio.TimeoutError:
            return _Response(error="timeout")
        except ssl.SSLCertVerificationError as exc:  # pragma: no cover - handled in _connect
            return _Response(error=f"SSLCertVerificationError: {exc}")
        except Exception as exc:  # noqa: BLE001 - a failed host never kills the run
            return _Response(error=f"{type(exc).__name__}: {exc}"[:160])
        finally:
            if writer is not None:
                with contextlib.suppress(Exception):
                    writer.close()
                    await writer.wait_closed()

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
            resp = await self.fetch(url, method=method, body=body)
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
            if host:
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

        workers = [asyncio.ensure_future(drain()) for _ in range(min(self.concurrency, max(1, queue.qsize())))]
        try:
            await asyncio.gather(*workers)
        except asyncio.CancelledError:  # pragma: no cover - stop is cooperative
            for worker in workers:
                worker.cancel()
            raise


def run_work(indexer: "Indexer", work: Iterable[tuple], on_result=None) -> None:
    """Blocking entry point used by ``Indexer.run``."""
    transport = AsyncTransport(indexer)
    asyncio.run(transport.run(work, on_result))
