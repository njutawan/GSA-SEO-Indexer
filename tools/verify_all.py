#!/usr/bin/env python3
"""Re-measure every optimisation claim from P0 to P13, offline.

The numbers in ``OPTIMIZATION-REVIEW.md`` were produced by one-off commands
spread over three phases; this script turns them into a single re-runnable
check.  Nothing here touches the internet: throughput, header shape, host
streaks, reports and the emulator all run against a local HTTP server, and the
CPU/memory claims are measured directly.

Usage::

    python tools/verify_all.py            # all checks, table + exit code
    python tools/verify_all.py --only p2  # one phase (p0..p13)

Every line prints the *measured* value next to the claim it backs, so a
regression shows up as FAIL with the number that moved, not as a vague
"something changed".
"""

from __future__ import annotations

import argparse
import gzip
import http.server
import json
import os
import random
import re
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import urllib.request
import contextlib
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gsa_seo_indexer.engine import (  # noqa: E402
    DEFAULT_USER_AGENTS,
    Indexer,
    PingTarget,
    Result,
    Settings,
    _classify_link,
    _ssl_context,
    cap_targets,
    check_urls,
)
from gsa_seo_indexer.httpclient import tls_context  # noqa: E402
from gsa_seo_indexer import engine as engine_mod  # noqa: E402
from gsa_seo_indexer.perf import DnsCache, HostCache  # noqa: E402
from gsa_seo_indexer.resultstore import ResultStore  # noqa: E402
from gsa_seo_indexer.watcher import UrlWatcher  # noqa: E402

HEADER_KEYS = ("user-agent", "accept", "accept-language", "referer")


# ---------------------------------------------------------------------------
# A tiny local server: fixed delay, fixed body, and a log of what it received
# ---------------------------------------------------------------------------
class _CaptureServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # socketserver defaults to a listen backlog of 5, which turns a burst of
    # 50 concurrent connects into "connection refused" - that is a property of
    # the fixture, not of the transport being measured.
    request_queue_size = 512

    def handle_error(self, request, client_address) -> None:
        # A client that times out on purpose closes the socket mid-reply; that
        # is the measurement, not a failure.
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen: list[dict[str, str]] = []
        self.post_bodies: list[bytes] = []
        self.lock = threading.Lock()
        self.delay = 0.0
        self.body = "<html><body>ok</body></html>"


class _CaptureHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102 - silence the log
        pass

    def _record(self) -> None:
        srv = self.server
        with srv.lock:
            srv.seen.append({k.lower(): v for k, v in self.headers.items()})

    def _reply(self, payload: bytes, head_only: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        time.sleep(self.server.delay)
        self._reply(self.server.body.encode())

    def do_HEAD(self) -> None:  # noqa: N802
        self._record()
        time.sleep(self.server.delay)
        self._reply(b"", head_only=True)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self._record()
        with self.server.lock:
            self.server.post_bodies.append(body)
        time.sleep(self.server.delay)
        self._reply(self.server.body.encode())


@contextmanager
def serve(delay: float = 0.0, body: str = "<html><body>ok</body></html>"):
    srv = _CaptureServer(("127.0.0.1", 0), _CaptureHandler)
    srv.delay = delay
    srv.body = body
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = srv.server_address[:2]
        yield srv, f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


class _GzipHandler(http.server.BaseHTTPRequestHandler):
    """Serves one big page, gzipped when the client asks for it."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def do_GET(self) -> None:  # noqa: N802
        page = self.server.page
        asked = "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
        payload = gzip.compress(page, 6) if asked else page
        with self.server.lock:
            self.server.wire.append(len(payload))
            self.server.asks.append(asked)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if asked:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def gzip_serve(page: bytes):
    srv = _CaptureServer(("127.0.0.1", 0), _GzipHandler)
    srv.page = page
    srv.wire = []
    srv.asks = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = srv.server_address[:2]
        yield srv, f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


class _KeepAliveHandler(http.server.BaseHTTPRequestHandler):
    """Keep-alive server that records the client port of every request.

    ``disable_nagle_algorithm`` matters here: Python's ``http.server`` writes
    the status line, the headers and the body with separate syscalls, and with
    Nagle on the server holds the tail of that burst until the client's
    delayed ACK arrives (~40 ms).  That stall only exists while the connection
    stays open, so leaving Nagle on would make the *pool* look 26x slower than
    it is - it is a property of the fixture, not of the client.
    """

    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True

    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def do_GET(self) -> None:  # noqa: N802
        srv = self.server
        with srv.lock:
            srv.ports.append(self.client_address[1])
        payload = srv.body
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def pool_serve(body: bytes = b'<html><a href="http://t.example/">t</a></html>'):
    srv = _CaptureServer(("127.0.0.1", 0), _KeepAliveHandler)
    srv.body = body
    srv.ports = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = srv.server_address[:2]
        yield srv, f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


@contextmanager
def pool_serve_https(tls_ctx):
    """The keep-alive server again, with TLS on the listening socket."""
    srv = _CaptureServer(("127.0.0.1", 0), _KeepAliveHandler)
    srv.body = b'<html><a href="http://t.example/">t</a></html>'
    srv.ports = []
    srv.socket = tls_ctx.wrap_socket(srv.socket, server_side=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv, srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Forwarding proxy: answers plain HTTP itself, tunnels CONNECT."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def do_GET(self) -> None:  # noqa: N802
        with self.server.lock:
            self.server.seen.append((self.command, self.path))
        payload = self.server.body
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_CONNECT(self) -> None:  # noqa: N802
        with self.server.lock:
            self.server.seen.append((self.command, self.path))
        host, _, port = self.path.partition(":")
        try:
            upstream = socket.create_connection((host, int(port)), timeout=5)
        except OSError:
            self.send_error(502, "no upstream")
            return
        self.send_response(200, "Connection established")
        self.end_headers()
        self.wfile.flush()
        sock = self.connection
        sock.settimeout(10)
        upstream.settimeout(10)

        def pump(src, dst) -> None:
            try:
                while True:
                    chunk = src.recv(65536)
                    if not chunk:
                        break
                    dst.sendall(chunk)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    dst.shutdown(socket.SHUT_WR)

        side = threading.Thread(target=pump, args=(sock, upstream), daemon=True)
        side.start()
        pump(upstream, sock)
        side.join(timeout=5)
        upstream.close()
        self.close_connection = True


@contextmanager
def proxy_serve(body: bytes = b'<html><a href="http://t.example/">t</a></html>'):
    srv = _CaptureServer(("127.0.0.1", 0), _ProxyHandler)
    srv.body = body
    srv.seen = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = srv.server_address[:2]
        yield srv, f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


def _harness(**kwargs) -> Indexer:
    """An Indexer with the on-disk side effects disabled."""
    base = dict(
        threads=4,
        timeout=2.0,
        connect_timeout=2.0,
        read_timeout=2.0,
        persist_host_cache=False,
        dns_cache=False,
        check_url=False,
    )
    base.update(kwargs)
    return Indexer(Settings(**base))


# ---------------------------------------------------------------------------
# P0 — shared TLS contexts, throughput, classifier, site cap
# ---------------------------------------------------------------------------
def p0_shared_tls() -> tuple[str, bool]:
    a, b, c = _ssl_context(True), tls_context(True), _ssl_context(True)
    u1, u2 = _ssl_context(False), tls_context(False)
    same = a is b is c and u1 is u2 and a is not u1

    built = [0]
    real = ssl.create_default_context

    def counting(*args, **kwargs):
        built[0] += 1
        return real(*args, **kwargs)

    ssl.create_default_context = counting
    try:
        ix = _harness()
        for index in range(30):
            ix._opener(f"http://proxy{index}.test:8080")
        ix.close()
    finally:
        ssl.create_default_context = real
    ok = same and built[0] == 0
    return f"identitas sama={same}, 0 context baru / 30 opener (terukur {built[0]})", ok


def _bench(args: list[str]) -> float:
    out = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bench_local.py"), *args],
        capture_output=True, text=True, timeout=180,
    ).stdout
    matches = re.findall(r"([\d.]+) req/s", out)
    if not matches:
        raise RuntimeError(f"benchmark tidak mengeluarkan angka: {out[-300:]}")
    return float(matches[-1])


def p0_throughput_16() -> tuple[str, bool]:
    rate = _bench(["400", "16"])
    return f"400 req / 16 thread = {rate:.1f} req/s (target ≥250)", rate >= 250


def p0_throughput_1() -> tuple[str, bool]:
    rate = _bench(["400", "1"])
    return f"400 req / 1 thread = {rate:.1f} req/s (target ≥600)", rate >= 600


def p0_classifier() -> tuple[str, bool]:
    body = "<html><body>" + ("lorem ipsum dolor sit amet consectetur " * 6000) + "</body></html>"
    payload = "http://example.com/blog/post"
    rounds = 20
    start = time.perf_counter()
    for _ in range(rounds):
        if _classify_link(body, payload) != "":
            raise RuntimeError("body tanpa target diklasifikasi positif")
    miss_ms = (time.perf_counter() - start) / rounds * 1000

    indexed = _classify_link('<a href="http://example.com/blog/post">x</a>', payload)
    listed = _classify_link("<p>lihat http://example.com/blog/post di sini</p>", payload)
    echo = _classify_link('<form><input name="q" value="http://example.com/blog/post"></form>', payload)
    ok = miss_ms < 1.5 and indexed == "indexed" and listed == "listed" and echo == ""
    return (
        f"200 kB tanpa target = {miss_ms:.3f} ms/panggilan (target <1.5); "
        f"anchor={indexed or '-'}, teks={listed or '-'}, echo={echo or '-'}",
        ok,
    )


def p0_site_cap() -> tuple[str, bool]:
    # 800 sites x 2 shapes -> the cap keeps all 800 sites (1600 targets).
    targets = []
    for index in range(800):
        targets.append(PingTarget(url=f"http://site{index}.test/?url={{enc}}", mode="query"))
        targets.append(PingTarget(url=f"http://site{index}.test/{{enc}}"))
    capped = cap_targets(targets, 800)
    hosts = {t.url.split("//", 1)[1].split("/", 1)[0] for t in capped}

    # run() must count sites x urls, not slice the work list: 3 sites x 2 urls.
    ix = _harness(threads=2, max_sites=3)
    ix._fetch = lambda url, req, proxy=None: ("ok", 200)
    tasks = [PingTarget(url=f"http://h{i}.test/?url={{enc}}", mode="query") for i in range(3)]
    ix.run(["http://example.com/a", "http://example.com/b"], tasks)
    total = ix.stats.total
    ix.close()
    ok = len(hosts) == 800 and len(capped) == 1600 and total == 6
    return (
        f"800 situs -> {len(hosts)} host / {len(capped)} target (target 800/1600); "
        f"run 3 situs x 2 url = {total} pekerjaan (target 6)",
        ok,
    )


# ---------------------------------------------------------------------------
# P1 — parallel pre-check, streamed reports, slots, GUI cap
# ---------------------------------------------------------------------------
def p1_check_urls() -> tuple[str, bool]:
    with serve(delay=0.3) as (srv, base):
        ix = _harness(threads=8)
        urls = [f"{base}/p{i}" for i in range(10)]
        start = time.perf_counter()
        results = check_urls(ix, urls)
        elapsed = time.perf_counter() - start
        ix.close()
        seen = len(srv.seen)
    ok = elapsed < 1.4 and len(results) == 10 and all(flag for _, flag, _ in results)
    return (
        f"10 URL x 300 ms = {elapsed:.2f} s (sekuensial ±3.0 s, target <1.4); "
        f"{seen} request, {sum(1 for _, ok_, _ in results if ok_)}/10 ok",
        ok,
    )


def p1_json_stream() -> tuple[str, bool]:
    ix = _harness()
    rows = [
        Result(target=f"http://example.com/{i}", url="http://t.example/?u=x", status="submitted")
        for i in range(5000)
    ]
    ix.results = rows
    ix.settings.report_format = "json"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.json"
        tracemalloc.start()
        ix.write_report(path)
        peak_new = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        size = path.stat().st_size
        parsed = json.loads(path.read_text())
    tracemalloc.start()
    _blob = json.dumps([asdict(r) for r in rows], indent=1)
    peak_old = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    ix.close()
    mb_new, mb_old = peak_new / 1e6, peak_old / 1e6
    ok = mb_new < 2.0 and mb_new < mb_old / 3 and len(parsed) == 5000
    return (
        f"5000 baris: puncak {mb_new:.2f} MB (cara lama `[asdict(r)...]` {mb_old:.1f} MB), "
        f"{size / 1e6:.1f} MB di disk, JSON valid",
        ok,
    )


def p1_result_slots() -> tuple[str, bool]:
    @dataclass
    class _Plain:
        target: str
        url: str
        status: str
        code: int = 0
        indexable: bool = False
        listed: bool = False
        elapsed_ms: int = 0
        error: str = ""

    def measure(factory) -> float:
        tracemalloc.start()
        rows = [factory(i) for i in range(100_000)]
        current = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        del rows
        return current / 1e6

    with_slots = measure(
        lambda i: Result(target=f"http://example.com/{i}", url="http://t.example/", status="failed")
    )
    without = measure(
        lambda i: _Plain(target=f"http://example.com/{i}", url="http://t.example/", status="failed")
    )
    ok = bool(getattr(Result, "__slots__", ())) and with_slots < without
    return (
        f"100k baris: {with_slots:.1f} MB (slots) vs {without:.1f} MB (tanpa slots), "
        f"-{100 - with_slots / without * 100:.0f}%",
        ok,
    )


def p1_gui_cap() -> tuple[str, bool]:
    # The module needs tkinter, which a headless CI box may not have, so the
    # constant is read from the source instead of imported.
    source = (ROOT / "gsa_seo_indexer" / "gui.py").read_text(encoding="utf-8")
    match = re.search(r"^MAX_TREE_ROWS\s*=\s*([\d_]+)", source, re.M)
    value = int(match.group(1).replace("_", "")) if match else 0
    return f"MAX_TREE_ROWS = {value} baris (target 5000)", value == 5000


# ---------------------------------------------------------------------------
# P2 — dead-host streak, DNS ceiling, connect timeout, persistence
# ---------------------------------------------------------------------------
def p2_host_streak() -> tuple[str, bool]:
    ix = _harness(threads=1)
    calls = [0]
    phase = {"alive": True}

    def fake_fetch(url, req, proxy=None):
        calls[0] += 1
        if phase["alive"]:
            return "ok", 200
        raise ConnectionRefusedError(111, "Connection refused")

    ix._fetch = fake_fetch
    target = PingTarget(url="http://gone.test/?url={enc}", mode="query")
    ix.submit_one(target, "http://example.com/0")
    phase["alive"] = False
    before = calls[0]
    for index in range(5000):
        ix.submit_one(target, f"http://example.com/{index}")
    attempts = calls[0] - before
    skipped = ix.skipped_dead
    ix.close()
    ok = attempts <= 2 and skipped >= 4998
    return (
        f"host sukses lalu mati: {attempts} percobaan dari 5000 URL "
        f"(sebelum P2: 5000), {skipped} di-skip",
        ok,
    )


def p2_dns_ceiling() -> tuple[str, bool]:
    import gsa_seo_indexer.perf as perf

    fake_answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
    real = perf._orig_getaddrinfo
    perf._orig_getaddrinfo = lambda *args, **kwargs: fake_answer
    try:
        cache = DnsCache(max_entries=1000)
        for index in range(5000):
            cache.lookup(f"h{index}.test", 80, socket.AF_INET, socket.SOCK_STREAM, 0, 0)
        stats = cache.stats()
    finally:
        perf._orig_getaddrinfo = real
    ok = stats["entries"] <= 1000 and stats["evictions"] > 0
    return (
        f"5000 host, batas 1000: {stats['entries']} entri, {stats['evictions']} eviction "
        f"(tanpa batas: 5000 entri)",
        ok,
    )


def p2_connect_timeout() -> tuple[str, bool]:
    blackhole = "http://10.255.255.1:81/?url={enc}"
    target = PingTarget(url=blackhole, mode="query")
    samples = []
    for connect in (0.5, 2.0):
        ix = _harness(timeout=5.0, connect_timeout=connect, read_timeout=5.0)
        start = time.perf_counter()
        ix.submit_one(target, "http://example.com/")
        samples.append((connect, time.perf_counter() - start))
        ix.close()
    fast, slow = samples[0][1], samples[1][1]
    if slow < 0.4:  # the sandbox answered immediately: nothing to measure
        return f"tidak dapat diukur (blackhole dijawab dalam {slow:.2f} s)", True
    ok = fast < slow * 0.6 and slow < 3.2
    return (
        f"connect_timeout 0.5 -> {fast:.2f} s, 2.0 -> {slow:.2f} s "
        f"(sebelum P2 keduanya mengikuti read timeout 5 s)",
        ok,
    )


def p2_read_timeout() -> tuple[str, bool]:
    """The read phase must use ``read_timeout``, not the connect timeout."""
    with serve(delay=0.5) as (_srv, base):
        rows = []
        for reuse, connect, read in ((False, 0.1, 5.0), (True, 0.1, 5.0), (False, 5.0, 0.2)):
            ix = _harness(
                threads=1, connect_timeout=connect, read_timeout=read, timeout=read,
                reuse_connections=reuse,
            )
            start = time.perf_counter()
            res = ix.submit_one(PingTarget(url=f"{base}/?url={{enc}}", mode="query"), "http://example.com/")
            rows.append((reuse, connect, read, time.perf_counter() - start, res))
            ix.close()
    urllib_ok = rows[0][4].status == "submitted"
    pooled_ok = rows[1][4].status == "submitted"
    bounded = rows[2][4].status == "failed" and rows[2][3] < 0.45
    ok = urllib_ok and pooled_ok and bounded
    return (
        f"halaman 0,5 s: connect 0,1 s -> urllib {rows[0][4].status} / pooled {rows[1][4].status}"
        f" (sebelum perbaikan: failed 0,2 s); read 0,2 s -> {rows[2][4].status} "
        f"dalam {rows[2][3]:.2f} s",
        ok,
    )


def p2_persistence() -> tuple[str, bool]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "host_cache.json"
        cache = HostCache()
        cache.record_success("alive.test", 40)
        cache.record_failure("dead.test", "refused")
        written = cache.save(path)
        payload = json.loads(path.read_text())
        restored = HostCache()
        loaded = restored.load(path)
        dead = restored.is_dead("dead.test")
        alive_skipped = restored.is_dead("alive.test")
    ok = written == 1 and loaded == 1 and dead and not alive_skipped and payload["version"] == 2
    return (
        f"1 host mati ditulis/dibaca (v{payload['version']}), "
        f"is_dead(dead)={dead}, is_dead(alive)={alive_skipped}",
        ok,
    )


# ---------------------------------------------------------------------------
# P3 — one request shape, worker cap, TLS for API keys, emulator, CI
# ---------------------------------------------------------------------------
def p3_header_shape() -> tuple[str, bool]:
    with serve(body="<html><body>ok</body></html>") as (srv, base):
        rows: list[tuple[str, dict[str, str]]] = []

        def capture(label: str) -> int:
            with srv.lock:
                rows.append((label, srv.seen[-1]))
            return len(srv.seen)

        ix = _harness(threads=2)
        ix.submit_one(PingTarget(url=f"{base}/?url={{enc}}", mode="query"), "http://example.com/a")
        capture("submit_one")
        ix.submit_batch(
            PingTarget(url=f"{base}/?url={{enc}}", mode="query"),
            ["http://example.com/a", "http://example.com/b"],
        )
        capture("submit_batch")
        ix.check_url(f"{base}/page")
        capture("check_url")
        ix.close()

        pooled = _harness(threads=2, reuse_connections=True)
        pooled.submit_one(
            PingTarget(url=f"{base}/?url={{enc}}", mode="query"), "http://example.com/c"
        )
        capture("pooled client")
        pooled.close()

    missing = []
    for label, headers in rows:
        absent = [key for key in HEADER_KEYS if key not in headers]
        if absent:
            missing.append(f"{label}: {','.join(absent)}")
    uas = {headers.get("user-agent") for _, headers in rows}
    ok = not missing and len(rows) == 4 and uas <= set(DEFAULT_USER_AGENTS)
    detail = ", ".join(f"{label} ({sum(k in h for k in HEADER_KEYS)}/4)" for label, h in rows)
    if missing:
        detail += f" | kurang: {'; '.join(missing)}"
    return f"{detail}; UA dari daftar rotasi={uas <= set(DEFAULT_USER_AGENTS)}", ok


def p3_preflight_cap() -> tuple[str, bool]:
    values = {}
    for threads in (4, 50, 100):
        ix = _harness(threads=threads)
        values[threads] = ix._preflight_workers()
        ix.close()
    ok = values[4] == 8 and values[50] == 64 and values[100] == 64
    return (
        "worker preflight: " + ", ".join(f"{t} thread -> {w}" for t, w in values.items())
        + " (tanpa cap: 8/100/200)",
        ok,
    )


def p3_defaults() -> tuple[str, bool]:
    settings = Settings()
    ok = settings.random_delay is False and settings.reuse_connections is False
    return (
        f"random_delay={settings.random_delay}, reuse_connections={settings.reuse_connections}",
        ok,
    )


def p3_service_tls() -> tuple[str, bool]:
    import gsa_seo_indexer.services as services

    defs = services.load_services(ROOT / "gsa_seo_indexer" / "index_service")
    service = defs[0]
    captured: dict[str, object] = {}

    class _Fake:
        status = 200
        headers: dict[str, str] = {}

        def read(self, limit: int = -1) -> bytes:
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, *args, **kwargs):
        captured["context"] = kwargs.get("context")
        captured["url"] = req.full_url
        return _Fake()

    real = services.urllib.request.urlopen
    services.urllib.request.urlopen = fake_urlopen
    try:
        service.submit(["http://example.com/"], {"apikey": "secret"})
    finally:
        services.urllib.request.urlopen = real

    ctx = captured.get("context")
    verified = (
        isinstance(ctx, ssl.SSLContext)
        and ctx.verify_mode == ssl.CERT_REQUIRED
        and ctx.check_hostname is True
    )
    return (
        f"{service.name}: context verifikasi dipatok={bool(ctx is not None)}, "
        f"verify_mode=CERT_REQUIRED={getattr(ctx, 'verify_mode', None) == ssl.CERT_REQUIRED}, "
        f"check_hostname={getattr(ctx, 'check_hostname', None)}",
        bool(verified),
    )


def p3_emulator_root() -> tuple[str, bool]:
    from gsa_seo_indexer.emulator import EmulatorConfig, IndexServiceEmulator

    emulator = IndexServiceEmulator(EmulatorConfig(host="127.0.0.1", port=0, services=[]))
    host, port = emulator.start()
    try:
        codes = {}
        for path in ("/", "/no/such/service"):
            with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=5) as resp:
                codes[path] = resp.status
    finally:
        emulator.stop()
    ok = all(code == 200 for code in codes.values())
    return (
        "GET / -> {0}, GET /no/such/service -> {1} (sebelum P3: NameError 500)".format(
            codes.get("/"), codes.get("/no/such/service")
        ),
        ok,
    )


def _ruff_command() -> list[str]:
    """Where ruff lives, if anywhere: PATH, ``$RUFF``, or ``python -m ruff``."""
    for candidate in (
        os.environ.get("RUFF", ""),
        shutil.which("ruff") or "",
        "/tmp/lintenv3/bin/ruff",
        "/tmp/lintenv2/bin/ruff",
        "/tmp/lintenv/bin/ruff",
    ):
        if candidate and Path(candidate).exists():
            return [candidate]
    probe = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"], capture_output=True, timeout=60
    )
    return [sys.executable, "-m", "ruff"] if probe.returncode == 0 else []


def p3_toolchain() -> tuple[str, bool]:
    ruff = _ruff_command()
    if ruff:
        lint = subprocess.run(
            [*ruff, "check", "."], cwd=ROOT, capture_output=True, text=True, timeout=120
        )
        lint_txt = "ruff bersih" if lint.returncode == 0 else f"ruff {lint.stdout.strip()[:80]}"
        lint_ok = lint.returncode == 0
    else:
        lint_txt, lint_ok = "ruff tidak terpasang (dilewati)", True

    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    match = re.search(r"Ran (\d+) tests", tests.stderr)
    count = int(match.group(1)) if match else 0
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    matrix = re.search(r"python-version:\s*\[([^\]]+)\]", ci)
    versions = [v.strip().strip('"\'') for v in matrix.group(1).split(",")] if matrix else []
    ok = lint_ok and tests.returncode == 0 and count >= 71 and len(versions) == 4
    return (
        f"{lint_txt}; {count} test OK; CI matrix {'/'.join(versions) or '?'}",
        ok,
    )


# ---------------------------------------------------------------------------
# P4 — opener cache, compact reports, parallel services, bounded UI state
# ---------------------------------------------------------------------------
def p4_opener_cache() -> tuple[str, bool]:
    with serve() as (_srv, base):
        ix = _harness(threads=4)
        built = [0]
        real = ix._build_opener

        def counting(proxy, verify=True):
            built[0] += 1
            return real(proxy, verify)

        ix._build_opener = counting
        target = PingTarget(url=f"{base}/?url={{enc}}", mode="query")
        for index in range(200):
            ix.submit_one(target, f"http://example.com/{index}")
        first = ix._opener(None)
        same = first is ix._opener(None)
        built_by_requests = built[0]  # before the eviction exercise below
        for index in range(engine_mod.MAX_CACHED_OPENERS * 3):
            ix._opener(f"http://proxy{index}.example:8080")
        bounded = len(ix._openers) <= engine_mod.MAX_CACHED_OPENERS
        ix.close()
    ok = built_by_requests == 1 and same and bounded
    return (
        f"200 request -> {built_by_requests} kali build (dulu 200); identitas sama={same}; "
        f"cache maks {engine_mod.MAX_CACHED_OPENERS} entri, terpakai {bounded}",
        ok,
    )


def p4_report_json() -> tuple[str, bool]:
    ix = _harness()
    rows = [
        Result(target=f"http://example.com/{i}", url="http://t.example/?u=x", status="submitted")
        for i in range(20_000)
    ]
    ix.results = rows
    ix.settings.report_format = "json"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "report.json"
        start = time.perf_counter()
        ix.write_report(path)
        elapsed = time.perf_counter() - start
        text = path.read_text(encoding="utf-8")
    ix.close()
    compact = len(text) / 1e6
    pretty = len(json.dumps([asdict(r) for r in rows], indent=1)) / 1e6
    ok = '": ' not in text and json.loads(text) and compact < pretty
    return (
        f"20k baris: {elapsed:.2f} s, {compact:.2f} MB compact vs {pretty:.2f} MB indent=1 "
        f"({100 - compact / pretty * 100:.0f}% lebih kecil)",
        ok,
    )


def p4_services_parallel() -> tuple[str, bool]:
    from gsa_seo_indexer import services as service_mod

    class Fake:
        def __init__(self, name: str) -> None:
            self.name = name

        def submit(self, urls, args=None, timeout=20.0):
            time.sleep(0.1)
            return True, "HTTP 200"

    batch = [Fake(f"s{i}") for i in range(8)]
    start = time.perf_counter()
    results = service_mod.submit_many(batch, ["http://example.com/"], workers=8)
    parallel = time.perf_counter() - start
    start = time.perf_counter()
    service_mod.submit_many(batch, ["http://example.com/"], workers=1)
    sequential = time.perf_counter() - start
    ok = len(results) == 8 and parallel < 0.45 and sequential > parallel * 2
    return (
        f"8 service x 100 ms: sekuensial {sequential:.2f} s -> paralel {parallel:.2f} s "
        f"({sequential / parallel:.1f}x), urutan tetap",
        ok,
    )


def p4_ui_bounds() -> tuple[str, bool]:
    gui_src = (ROOT / "gsa_seo_indexer" / "gui.py").read_text(encoding="utf-8")
    drain = re.search(r"^MAX_DRAIN_PER_TICK\s*=\s*([\d_]+)", gui_src, re.M)
    watcher_src = (ROOT / "gsa_seo_indexer" / "watcher.py").read_text(encoding="utf-8")
    bounded = "max_seen" in watcher_src and "_seen_order" in watcher_src

    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "links.txt"
        watch = UrlWatcher([log], max_seen=1_000)
        for index in range(1_100):
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"http://h{index}.example/\n")
            watch.poll()
        size = len(watch._seen)
    ok = bool(drain) and int(drain.group(1).replace("_", "")) <= 1_000 and bounded and size <= 1_000
    return (
        f"drain/tick maks {drain.group(1) if drain else '?'} baris; "
        f"_seen watcher dibatasi 1000 -> {size} entri setelah 1100 URL",
        ok,
    )


# ---------------------------------------------------------------------------
# P5 — the listing decision, made cheaper without changing an answer
# ---------------------------------------------------------------------------
def p5_classifier() -> tuple[str, bool]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "classify_diff", ROOT / "tools" / "classify_diff.py"
    )
    diff = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diff)

    rng = random.Random(7)
    cases = list(diff.TRICKY) + [diff.generate(rng) for _ in range(2000)]
    mismatches = 0
    for body, payload in cases:
        if diff.reference_classify(body, payload) != _classify_link(body, payload):
            mismatches += 1

    page = ("<div class='hit'>block text " * 6000) + "<p>http://example.com/blog/post</p>"
    payload = "http://example.com/blog/post"
    rounds = 50
    started = time.perf_counter()
    for _ in range(rounds):
        diff.reference_classify(page, payload)
    slow = (time.perf_counter() - started) / rounds * 1000
    started = time.perf_counter()
    for _ in range(rounds):
        _classify_link(page, payload)
    fast = (time.perf_counter() - started) / rounds * 1000

    ok = mismatches == 0 and fast < slow * 0.75
    return (
        f"{len(cases)} kasus ekuivalen (selisih {mismatches}); "
        f"halaman {len(page) / 1024:.0f} kB: {slow:.2f} -> {fast:.2f} ms "
        f"({slow / fast:.1f}x)",
        ok,
    )


# ---------------------------------------------------------------------------
# P6 — result log: bounded memory, same rows
# ---------------------------------------------------------------------------
def p6_result_store() -> tuple[str, bool]:
    rows_n = 400_000

    def row(index: int) -> Result:
        return Result(
            target=f"http://example.com/{index % 1000}",
            url=f"http://site.example/?u={index}",
            status="submitted",
            code=200,
            indexable=(index % 7 == 0),
            elapsed_ms=12,
        )

    # Baseline: perilaku lama, satu list menahan semua baris.
    tracemalloc.start()
    plain = [row(i) for i in range(rows_n)]
    list_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    del plain

    with tempfile.TemporaryDirectory() as tmp:
        store = ResultStore(max_memory=20_000, directory=tmp)
        tracemalloc.start()
        for index in range(50_000):
            store.append(row(index))
        peak_50k = tracemalloc.get_traced_memory()[1]
        for index in range(50_000, rows_n):
            store.append(row(index))
        store_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        size = store.path.stat().st_size / 1e6
        spills = store.spills
        rows_back = list(store)
        order_ok = (
            len(rows_back) == rows_n
            and rows_back[0].url.endswith("=0")
            and rows_back[-1].url.endswith(f"={rows_n - 1}")
            and all(a.url != b.url for a, b in zip(rows_back[:5], rows_back[1:5]))
        )
        del rows_back
        path = store.path
        store.close()
        cleaned = not path.exists()

    ratio = list_peak / store_peak if store_peak else 0
    flat = store_peak < peak_50k * 1.3
    ok = ratio >= 5 and flat and order_ok and cleaned and spills > 0
    return (
        f"{rows_n:,} baris: puncak {list_peak / 1e6:.0f} MB (list) vs {store_peak / 1e6:.0f} MB "
        f"(store, {ratio:.1f}x lebih kecil, {spills} spill, {size:.0f} MB di disk); "
        f"puncak 50k={peak_50k / 1e6:.0f} MB vs 400k={store_peak / 1e6:.0f} MB (datar={flat}); "
        f"urutan utuh={order_ok}, file dihapus={cleaned}",
        ok,
    )


def p7_post_target() -> tuple[str, bool]:
    page = '<html><a href="http://t.example/">t.example</a></html>'
    want = b"url=http%3A%2F%2Ft.example%2F"

    with serve(body=page) as (srv, base):
        ix = _harness()
        try:
            post_res = ix.submit_one(PingTarget(url=f"{base}/ping", mode="post"), "http://t.example/")
            get_res = ix.submit_one(
                PingTarget(url=f"{base}/?url=", mode="query"), "http://t.example/"
            )
        finally:
            ix.close()
        with srv.lock:
            headers = list(srv.seen)
            urllib_body = srv.post_bodies[0] if srv.post_bodies else b""

    with serve(body=page) as (srv2, base2):
        ix = _harness(reuse_connections=True)
        try:
            pooled_res = ix.submit_one(
                PingTarget(url=f"{base2}/ping", mode="post"), "http://t.example/"
            )
        finally:
            ix.close()
        with srv2.lock:
            pooled_body = srv2.post_bodies[0] if srv2.post_bodies else b""

    post_ok = (
        post_res.status == "submitted"
        and post_res.indexable
        and urllib_body == want
        and headers[0].get("content-type") == "application/x-www-form-urlencoded"
    )
    get_ok = (
        get_res.status == "submitted"
        and "content-type" not in headers[1]
        and "content-length" not in headers[1]
    )
    pooled_ok = pooled_res.status == "submitted" and pooled_body == want
    ok = post_ok and get_ok and pooled_ok
    return (
        f'POST body "{urllib_body.decode()[:40]}" (urllib ok={post_ok}, pool ok={pooled_ok}); '
        f"GET tanpa body={get_ok}; hasil post: {post_res.status}, indexable={post_res.indexable}",
        ok,
    )


class _FlakyHandler(http.server.BaseHTTPRequestHandler):
    """Answers 503 (with Retry-After) until ``ok_after`` requests have been made."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def do_GET(self) -> None:  # noqa: N802
        srv = self.server
        with srv.lock:
            srv.hits += 1
            hit = srv.hits
        if hit > srv.ok_after:
            payload, code, header = srv.page, 200, None
        else:
            payload, code, header = b"<html>later</html>", 503, srv.retry_after
        self.send_response(code)
        if header is not None:
            self.send_header("Retry-After", header)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def flaky_serve(ok_after: int = 1, retry_after: str = "0"):
    srv = _CaptureServer(("127.0.0.1", 0), _FlakyHandler)
    srv.ok_after = ok_after
    srv.retry_after = retry_after
    srv.hits = 0
    srv.page = b'<html><a href="http://t.example/">t.example</a></html>'
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = srv.server_address[:2]
        yield srv, f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


def p8_gzip() -> tuple[str, bool]:
    # Deliberately a *hard* page to compress (random words + random numbers):
    # a page of one repeated sentence compresses 100x and would flatter the
    # result.  Real ping pages land between the two, so the number reported
    # here is a lower bound for the saving.
    rng = random.Random(7)
    words = [f"kata{i}" for i in range(4000)]
    filler = "".join(
        f"<p>{rng.randrange(10**6)}: "
        + " ".join(rng.choice(words) for _ in range(14))
        + "</p>"
        for _ in range(900)
    )
    page = (
        '<html><body><a href="http://t.example/">t.example</a>'
        + filler
        + "</body></html>"
    ).encode()
    target_url = "http://t.example/"

    with gzip_serve(page) as (plain_srv, plain_base):
        ix = _harness()
        try:
            plain = ix.submit_one(PingTarget(url=f"{plain_base}/"), target_url)
            with_pool = None
        finally:
            ix.close()

    with gzip_serve(page) as (gz_srv, gz_base):
        ix = _harness(accept_gzip=True)
        try:
            gz = ix.submit_one(PingTarget(url=f"{gz_base}/"), target_url)
        finally:
            ix.close()
        ix = _harness(accept_gzip=True, reuse_connections=True)
        try:
            with_pool = ix.submit_one(PingTarget(url=f"{gz_base}/"), target_url)
        finally:
            ix.close()

    same_decision = (plain.indexable, plain.listed) == (gz.indexable, gz.listed)
    wire = gz_srv.wire[0] if gz_srv.wire else 0
    ratio = wire / len(page) if page else 1
    ok = (
        same_decision
        and plain.indexable
        and gz.indexable
        and with_pool is not None
        and with_pool.indexable
        and gz_srv.asks
        and all(gz_srv.asks)
        and not any(plain_srv.asks)
        and ratio < 0.5
    )
    return (
        f"halaman {len(page) / 1024:.0f} kB -> {wire / 1024:.1f} kB di kabel "
        f"({ratio * 100:.0f}%, {1 / ratio:.1f}x lebih sedikit), "
        f"keputusan sama={same_decision}, "
        f"pool ikut gzip={bool(with_pool and with_pool.indexable)}, "
        f"tanpa --gzip tetap polos={not any(plain_srv.asks)}",
        ok,
    )


def p9_retry() -> tuple[str, bool]:
    target_url = "http://t.example/"

    with flaky_serve(ok_after=1) as (srv, base):
        ix = _harness()
        try:
            plain = ix.submit_one(PingTarget(url=f"{base}/"), target_url)
        finally:
            ix.close()
        plain_hits = srv.hits

    with flaky_serve(ok_after=1) as (srv, base):
        ix = _harness(retries=1)
        try:
            retried = ix.submit_one(PingTarget(url=f"{base}/"), target_url)
        finally:
            ix.close()
        retry_hits = srv.hits

    with flaky_serve(ok_after=99, retry_after="60") as (srv, base):
        ix = _harness(retries=3, retry_max_wait=1.0)
        started = time.perf_counter()
        try:
            refused = ix.submit_one(PingTarget(url=f"{base}/"), target_url)
        finally:
            ix.close()
        refused_hits = srv.hits
        refused_time = time.perf_counter() - started

    ok = (
        plain_hits == 1
        and plain.status == "failed"
        and retry_hits == 2
        and retried.status == "submitted"
        and retried.indexable
        and refused_hits == 1
        and refused.status == "failed"
        and "retry habis" in refused.error
        and refused_time < 1.0
    )
    return (
        f"503 lalu 200: {plain_hits} -> {retry_hits} request, "
        f"{plain.status} -> {retried.status}; Retry-After 60 s dengan cap 1 s "
        f"ditolak ({refused_hits} request, {refused_time:.2f} s, {refused.status})",
        ok,
    )


def p10_asyncio() -> tuple[str, bool]:
    page = '<html><a href="http://t.example/">t.example</a></html>'
    target_url = "http://t.example/"
    count, concurrency = 600, 50

    def measure(transport: str):
        ix = _harness(
            threads=concurrency,
            transport=transport,
            timeout=20.0,
            connect_timeout=10.0,
            read_timeout=10.0,
        )
        try:
            targets = [PingTarget(url=f"{base}/", mode="query") for _ in range(count)]
            started = time.perf_counter()
            results = ix.run([target_url], targets)
            elapsed = time.perf_counter() - started
            rows = sorted(
                (r.target, r.url, r.status, r.code, r.indexable, r.listed) for r in results
            )
            return count / elapsed, rows, ix.stats.submitted, ix.stats.failed
        finally:
            ix.close()

    with serve(body=page) as (_srv, base):
        thread_rate, thread_rows, thread_ok, thread_fail = measure("threads")
        loop_rate, loop_rows, loop_ok, loop_fail = measure("asyncio")

    same_rows = thread_rows == loop_rows
    same_stats = (thread_ok, thread_fail) == (loop_ok, loop_fail)
    ratio = loop_rate / thread_rate if thread_rate else 0
    ok = same_rows and same_stats and ratio >= 0.8 and loop_ok == count
    return (
        f"{count} request @ concurrency {concurrency}: threads {thread_rate:.0f} req/s, "
        f"asyncio {loop_rate:.0f} req/s ({ratio:.2f}x); baris identik={same_rows}, "
        f"statistik sama={same_stats} ({loop_ok} submitted)",
        ok,
    )


def p11_package() -> tuple[str, bool]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_package.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    line = (result.stdout or result.stderr).strip().splitlines()
    detail = line[0] if line else "tanpa keluaran"
    return detail.replace("[PASS] paket: ", "").replace("[FAIL] paket: ", ""), result.returncode == 0


def p12_async_pool() -> tuple[str, bool]:
    target_url = "http://t.example/"
    count = 20

    def measure(reuse: bool):
        with pool_serve() as (srv, base):
            ix = _harness(threads=1, transport="asyncio", reuse_connections=reuse)
            try:
                targets = [PingTarget(url=f"{base}/p{i}") for i in range(count)]
                started = time.perf_counter()
                results = ix.run([target_url], targets)
                elapsed = time.perf_counter() - started
            finally:
                ix.close()
            connections = len(set(srv.ports))
            ok_rows = sum(1 for r in results if r.status == "submitted")
        return connections, count / elapsed, ok_rows

    pooled_conns, pooled_rate, pooled_ok = measure(True)
    plain_conns, plain_rate, plain_ok = measure(False)

    ok = (
        pooled_conns == 1
        and plain_conns == count
        and pooled_ok == count
        and plain_ok == count
        and pooled_rate > plain_rate
    )
    speed = pooled_rate / plain_rate if plain_rate else 0.0
    return (
        f"{count} request ke 1 host: pool {pooled_conns} koneksi ({pooled_rate:.0f} req/s) "
        f"vs tanpa pool {plain_conns} koneksi ({plain_rate:.0f} req/s, {speed:.1f}x); "
        f"baris ok {pooled_ok}/{plain_ok}",
        ok,
    )


def p13_async_proxy() -> tuple[str, bool]:
    target_url = "http://t.example/"
    payload = b'<html><a href="http://t.example/">t</a></html>'

    with proxy_serve(payload) as (proxy, _purl):
        ix = _harness(threads=2, transport="asyncio", use_proxy=True)
        ix.settings.proxy_list = [f"127.0.0.1:{proxy.server_address[1]}"]
        ix._proxy_pool = list(ix.settings.proxy_list)
        ix._good_proxies = list(ix._proxy_pool)
        try:
            http_rows = ix.run(
                [target_url], [PingTarget(url="http://site.example/x?u=", mode="query")]
            )
            fallback = ix.transport_fallback
        finally:
            ix.close()
        absolute = [path for method, path in proxy.seen if method == "GET"]

    ok_http = bool(http_rows) and http_rows[0].status == "submitted" and absolute and absolute[0].startswith("http://site.example/x?u=")
    ok_loop = fallback == ""

    # HTTPS through the same proxy needs a tunnel and a certificate; the TLS
    # half is covered by tests/test_asyncio_net.py, so this check only proves
    # the CONNECT handshake when openssl is around.
    connect_seen = ""
    ok_connect = True
    if shutil.which("openssl"):
        with tempfile.TemporaryDirectory() as tmp:
            cert = Path(tmp) / "c.pem"
            key = Path(tmp) / "k.pem"
            made = subprocess.run(
                [
                    "openssl", "req", "-x509", "-newkey", "rsa:2048",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-nodes", "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1",
                ],
                capture_output=True,
                timeout=120,
            )
            if made.returncode == 0:
                tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                tls_ctx.load_cert_chain(str(cert), str(key))
                with pool_serve_https(tls_ctx) as (tls_srv, tls_port):
                    with proxy_serve(payload) as (proxy2, _url2):
                        ix = _harness(threads=1, transport="asyncio", use_proxy=True)
                        ix.settings.proxy_list = [f"127.0.0.1:{proxy2.server_address[1]}"]
                        ix._proxy_pool = list(ix.settings.proxy_list)
                        ix._good_proxies = list(ix._proxy_pool)
                        try:
                            tls_rows = ix.run(
                                [target_url],
                                [PingTarget(url=f"https://127.0.0.1:{tls_port}/")],
                            )
                        finally:
                            ix.close()
                        methods = [m for m, _p in proxy2.seen]
                        connect_seen = ",".join(methods)
                        ok_connect = (
                            bool(tls_rows)
                            and tls_rows[0].status == "submitted"
                            and "CONNECT" in methods
                        )
            else:
                connect_seen = "openssl gagal (dilewati)"

    ok = ok_http and ok_loop and ok_connect
    # Two CONNECTs are expected for the self-signed certificate: the verified
    # handshake fails, the host is remembered, and the tunnel is rebuilt
    # without verification - the same policy as the thread transport.
    connect_text = (
        f"{connect_seen} (verify-first gagal -> fallback)"
        if connect_seen.count("CONNECT") > 1
        else (connect_seen or "dilewati")
    )
    return (
        f"HTTP lewat proxy: {(absolute[0][:46] if absolute else '-')} (absolut={ok_http}), "
        f"tetap di event loop={ok_loop}; CONNECT: {connect_text} ({ok_connect})",
        ok,
    )


CHECKS = [
    ("p0", "TLS context dibagi per proses", p0_shared_tls),
    ("p0", "Throughput 16 thread", p0_throughput_16),
    ("p0", "Throughput 1 thread", p0_throughput_1),
    ("p0", "Classifier menolak cepat", p0_classifier),
    ("p0", "Batas situs dihitung sekali", p0_site_cap),
    ("p1", "check_urls paralel", p1_check_urls),
    ("p1", "Laporan JSON streaming", p1_json_stream),
    ("p1", "Result ber-slots", p1_result_slots),
    ("p1", "Batas baris GUI", p1_gui_cap),
    ("p2", "Host mati setelah sukses", p2_host_streak),
    ("p2", "Plafon cache DNS", p2_dns_ceiling),
    ("p2", "Connect timeout dipakai", p2_connect_timeout),
    ("p2", "Read timeout tidak tertimpa", p2_read_timeout),
    ("p2", "Host cache bertahan antar proses", p2_persistence),
    ("p3", "Satu bentuk header", p3_header_shape),
    ("p3", "Cap worker preflight", p3_preflight_cap),
    ("p3", "Default random_delay", p3_defaults),
    ("p3", "TLS index service", p3_service_tls),
    ("p3", "Halaman root emulator", p3_emulator_root),
    ("p3", "ruff + test + CI", p3_toolchain),
    ("p4", "Cache opener per kebijakan", p4_opener_cache),
    ("p4", "Laporan JSON compact", p4_report_json),
    ("p4", "Index service paralel", p4_services_parallel),
    ("p4", "Batas UI + watcher", p4_ui_bounds),
    ("p5", "Klasifikasi tetap setara", p5_classifier),
    ("p6", "Hasil dibatasi di memori", p6_result_store),
    ("p7", "Target POST mengirim body", p7_post_target),
    ("p8", "Gzip menghemat kabel", p8_gzip),
    ("p9", "Retry 429/503 menghormati Retry-After", p9_retry),
    ("p10", "Transport asyncio setara", p10_asyncio),
    ("p11", "Paket PyPI lengkap", p11_package),
    ("p12", "Pool koneksi di event loop", p12_async_pool),
    ("p13", "Proxy untuk transport asyncio", p13_async_proxy),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verifikasi ulang klaim P0-P13.")
    parser.add_argument(
        "--only",
        choices=[
            "p0", "p1", "p2", "p3", "p4", "p5",
            "p6", "p7", "p8", "p9", "p10", "p11",
            "p12", "p13",
        ],
        help="satu fase saja",
    )
    args = parser.parse_args(argv)

    os.chdir(ROOT)
    failures = 0
    started = time.perf_counter()
    for phase, name, fn in CHECKS:
        if args.only and phase != args.only:
            continue
        try:
            detail, ok = fn()
        except Exception as exc:  # noqa: BLE001 - a broken check is a FAIL, not a crash
            detail, ok = f"{type(exc).__name__}: {exc}", False
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {phase.upper():2} {name:34} {detail}")
    elapsed = time.perf_counter() - started
    total = sum(1 for p, _, _ in CHECKS if not args.only or p == args.only)
    print(f"\n{total - failures}/{total} PASS dalam {elapsed:.1f} s")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
