"""Offline micro-benchmark for the submission hot path.

Starts a local HTTP server that answers instantly and measures how many
``Indexer.submit_one`` calls per second the client can complete.  Network
latency is removed on purpose: what is left is the client's own per-request
cost (TLS context setup, opener construction, header building, body
classification), which is the part a code change can actually improve.

Usage::

    python tools/bench_local.py              # 400 requests, 16 threads
    python tools/bench_local.py 2000 50      # requests, threads

With ``threads`` above ~16 the loopback server (one Python accept loop) starts
to gate the result: p95 climbs into the hundreds of milliseconds and a few
requests reach the read timeout.  The tool prints how many were lost so the
numbers are not mistaken for client cost.
"""

from __future__ import annotations

import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gsa_seo_indexer.engine import Indexer, PingTarget, Settings  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    """Answers with a page that contains a real anchor to the target."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server API
        body = (
            "<html><body><h1>directory</h1>"
            '<a href="http://bench.local/">bench.local</a>'
            "<p>" + "filler " * 400 + "</p></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence request logging
        pass


def run(requests: int = 400, threads: int = 16) -> float:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    # Python's accept loop serves one connection at a time, so a small backlog
    # becomes the bottleneck before the client does: at 50 threads with the
    # old 256 the server dropped ~1% of requests and flattened throughput to
    # ~325 req/s.  The number below is what the loopback can actually absorb.
    server.request_queue_size = 1024
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    settings = Settings(
        threads=threads,
        check_url=False,
        skip_dead_hosts=False,
        random_delay=False,
        delay=0.0,
        connect_timeout=2.0,
        read_timeout=2.0,
        persist_host_cache=False,
    )
    indexer = Indexer(settings)
    targets = [
        PingTarget(url=f"http://127.0.0.1:{port}/", kind="plain", mode="query")
        for _ in range(requests)
    ]

    started = time.perf_counter()
    results = indexer.run(["http://bench.local/"], targets)
    elapsed = time.perf_counter() - started
    indexer.close()
    server.shutdown()

    submitted = sum(1 for r in results if r.status == "submitted")
    # At 50+ threads a few requests hit the 2 s read timeout because the local
    # Python server answers one accept at a time; that is the harness, not the
    # client.  A bigger shortfall still means a real regression.
    allowed = max(2, requests // 100)
    assert submitted >= requests - allowed, f"only {submitted}/{requests} submitted"
    if submitted < requests:
        print(f"  ({requests - submitted} dari {requests} tidak submitted - batas server lokal)")
    latencies = [r.elapsed_ms for r in results]
    print(
        f"{requests} requests, {threads} threads: {elapsed:.2f}s -> "
        f"{requests / elapsed:.1f} req/s | "
        f"median {statistics.median(latencies):.0f} ms, "
        f"p95 {sorted(latencies)[int(len(latencies) * 0.95)]} ms"
    )
    return requests / elapsed


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    t = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    run(n, t)
