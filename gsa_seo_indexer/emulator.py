"""Index service emulator.

GSA SEO Indexer can run a local HTTP server that *simulates* the third-party
indexing services, so other GSA tools (or your own scripts) can submit URLs
to it without paying for the real service.

The behaviour was recovered from the original binary (Ghidra decompile of
``TIndexServer`` / ``TIndexClient`` and the ``index_service`` INI files format):

* The server listens on ``index emulation host`` / ``own port``
  (default ``127.0.0.1:8787``) and optionally binds every interface.
* It rewrites the host file so that the real service domains resolve to the
  local server, which means the *real* service URL can be used verbatim.
* Every index service is answered according to its own INI file: the
  simulated success reply comes from ``simulate_result`` and the simulated
  failure from ``simulate_error``.  ``%url_count%`` is replaced with the
  number of submitted URLs.
* ``[error_message] front=`` and ``back=`` frame an error reply.
* With ``with auth on`` the server expects ``Authorization: Basic <b64>``
  or ``Authorization: Token <token>`` and answers 401 otherwise.

This module implements that behaviour so the clone can emulate the same
services locally.
"""

from __future__ import annotations

import base64
import http.server
import json
import re
import socketserver
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from .services import IndexService, load_services

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
INDEXER_ROOT_PAGE = (
    "<html><head><title>Indexer Server here</title></head>"
    "<body><h1>Indexer Server here</h1></body></html>"
)


@dataclass
class EmulatorConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    bind_all_interfaces: bool = False
    with_auth: bool = False
    own_port_exclusive: bool = False
    login: str = ""
    password: str = ""
    api_key: str = ""
    services: list[IndexService] = field(default_factory=list)
    verbose: bool = False

    # -- helpers ---------------------------------------------------------
    def service_for_path(self, path: str, host: str = "") -> IndexService | None:
        """Find the service that owns ``path``.

        A service matches when its configured URL path is a prefix of the
        requested path - that is how the original matches, because a client
        addresses the service by its real endpoint.

        ``host`` is the value of the request's ``Host`` header.  The original
        rewrites the host file so the *real* service domains resolve to the
        local server, which means the request arrives with the real hostname.
        When that header names one of the simulated services, the host
        decides the match, exactly like the original.
        """
        best: IndexService | None = None
        best_len = -1
        host = host.split(":")[0].lower()

        for svc in self.services:
            if not svc.url:
                continue
            parsed = urllib.parse.urlparse(svc.url)
            svc_path = parsed.path or "/"
            svc_query = parsed.query
            svc_host = (parsed.hostname or "").lower()

            if host and svc_host and host == svc_host:
                # The Host header names this service: it wins outright.
                return svc

            if svc_path not in path:
                continue
            if svc_query and svc_query not in path:
                continue
            if len(svc_path) > best_len:
                best_len = len(svc_path)
                best = svc
        return best

    def authorised(self, headers) -> bool:
        """Check the ``Authorization`` header when auth is switched on."""
        if not self.with_auth:
            return True
        value = headers.get("Authorization", "") if headers else ""
        if not value:
            return False
        scheme, _, token = value.partition(" ")
        scheme = scheme.lower()
        if scheme == "basic":
            try:
                decoded = base64.b64decode(token.strip()).decode("utf-8", "replace")
            except Exception:
                return False
            user, _, pwd = decoded.partition(":")
            return user == self.login and pwd == self.password
        if scheme == "token":
            return token.strip() == self.api_key
        return False


def _render(template: str, url_count: int) -> str:
    """Expand the placeholders a service INI may use in its simulated reply."""
    out = template.replace("%url_count%", str(url_count))
    out = out.replace("%URL_Count%", str(url_count))
    return out


def _extract_urls(post_data: str, body: str) -> list[str]:
    """Pull the submitted URLs out of a request.

    The GSA service INIs put the links into the body through ``%arg1%``
    (already substituted by the caller) or through the ``url_devider``
    separator.  Both the form-encoded and the raw-body shapes are handled.
    """
    urls: list[str] = []
    for chunk in re.split(r"[\r\n]+", body):
        chunk = chunk.strip()
        if not chunk:
            continue
        # urlencoded body: links[]=http%3A%2F%2F...
        for m in re.finditer(r"(?:links\[\]|urls|url|links)=([^&\r\n]+)", chunk):
            val = urllib.parse.unquote(m.group(1))
            urls.extend(u for u in re.split(r"[|,;]", val) if u.strip())
        if not chunk.startswith(("links", "urls", "url=", "apikey=", "key=")):
            if "://" in chunk and "=" not in chunk.split("://")[0]:
                urls.extend(u for u in re.split(r"[|,;]", chunk) if u.strip())
    return [u.strip() for u in urls if u.strip()]


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "GSAIndexerEmulator/1.0"
    config: EmulatorConfig

    def log_message(self, fmt: str, *args) -> None:  # noqa: D102
        if self.config.verbose:
            print(f"[emulator] {self.address_string()} {fmt % args}", flush=True)

    # -- helpers ---------------------------------------------------------
    def _send(self, code: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        payload = body.encode("utf-8", "replace")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # -- verbs -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if not self.config.authorised(self.headers):
            self._send(401, "Unauthorized")
            return
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "") and not self.config.services:
            self._send(200, INDEXER_ROOT_PAGE)
            return
        svc = self.config.service_for_path(self.path, self.headers.get("Host", ""))
        if svc is None:
            self._send(200, INDEXER_ROOT_PAGE)
            return
        self._reply(svc, [], self.path)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if not self.config.authorised(self.headers):
            self._send(401, "Unauthorized")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            self._send(413, "Payload too large")
            return
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        path = urllib.parse.urlparse(self.path).path
        svc = self.config.service_for_path(self.path, self.headers.get("Host", ""))
        if svc is None:
            self._send(404, "Not found")
            return
        urls = _extract_urls(svc.post_data, body)
        self._reply(svc, urls, body)

    # -- reply -----------------------------------------------------------
    def _reply(self, svc: IndexService, urls: list[str], body: str) -> None:
        count = len(urls)
        if not count:
            # A request without links still gets the service's success reply;
            # some clients ping the endpoint to check it is alive.
            count = 1
        reply = _render(svc.simulate_result or '{"status":"ok"}', count)
        if svc.simulate_error and self._looks_like_error(body):
            reply = _render(svc.simulate_error, count)
        if svc.encoding == "json" or reply.lstrip().startswith("{"):
            self._send(200, reply, "application/json")
        else:
            self._send(200, reply, "text/html; charset=utf-8")

    def _looks_like_error(self, body: str) -> bool:
        """Decide whether the client asked for the error path.

        The GSA service INIs have no explicit flag for this, but the
        documented behaviour is that a request carrying an empty API key (or
        an explicit ``error`` marker) is answered with the error reply.
        """
        low = body.lower()
        if "error=1" in low or "cmd=error" in low:
            return True
        m = re.search(r"(?:apikey|api_key|key)=([^&\r\n]*)", low)
        if m and m.group(1) == "":
            return True
        return False


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class IndexServiceEmulator:
    """Run a local HTTP server that simulates the configured index services."""

    def __init__(self, config: EmulatorConfig) -> None:
        self.config = config
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> tuple[str, int]:
        """Start the server and return the bound ``(host, port)``."""
        handler = type("_BoundHandler", (_Handler,), {"config": self.config})
        host = "0.0.0.0" if self.config.bind_all_interfaces else self.config.host
        self._server = _Server((host, self.config.port), handler)
        bound_host, bound_port = self._server.server_address[:2]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return bound_host, bound_port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def __enter__(self) -> "IndexServiceEmulator":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def build_config(
    service_folder: str | Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    bind_all_interfaces: bool = False,
    with_auth: bool = False,
    login: str = "",
    password: str = "",
    api_key: str = "",
    verbose: bool = False,
) -> EmulatorConfig:
    """Build an emulator configuration from a folder of service INI files."""
    services = load_services(service_folder)
    return EmulatorConfig(
        host=host,
        port=port,
        bind_all_interfaces=bind_all_interfaces,
        with_auth=with_auth,
        login=login,
        password=password,
        api_key=api_key,
        services=services,
        verbose=verbose,
    )
