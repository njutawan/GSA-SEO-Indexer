"""Offline micro-benchmark for the submission hot path.

Starts a local HTTP server that answers instantly and measures how many
``Indexer.submit_one`` calls per second the client can complete.  Network
latency is removed on purpose: what is left is the client's own per-request
cost (TLS context setup, opener construction, header building, body
classification), which is the part a code change can actually improve.

Usage::

    python tools/bench_local.py              # 400 requests, 16 threads
    python tools/bench_local.py 2000 50      # requests, threads
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
    server.request_queue_size = 256
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
    assert submitted >= requests - 2, f"only {submitted}/{requests} submitted"
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
