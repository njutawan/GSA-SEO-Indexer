"""Core indexing engine for the GSA SEO Indexer clone.

The engine reproduces the mechanism observed in GSA SEO Indexer v3.09:
a target URL is handed to thousands of third-party statistics / whois /
network-log sites so that the target is exposed as a backlink on a domain that
search-engine crawlers visit constantly.  The submission shape recovered from
the original binary is ``http://<host><path><target-url-with-scheme>``.
"""

from __future__ import annotations

import concurrent.futures
import csv
import json
import os
import queue
import re
import random
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

from .httpclient import PooledHttpClient
from .perf import DnsCache, HostCache, activate_dns_cache, deactivate_dns_cache, host_of, port_of

APP_NAME = "GSA SEO Indexer Clone"
APP_VERSION = "1.0.0"
APP_DEVELOPER = "RIZAL DRAG"
APP_YEAR = "2026"
APP_DESCRIPTION = (
    "A from-scratch reimplementation of the mechanism behind GSA SEO "
    "Indexer v3.09, recovered through reverse engineering with Ghidra.\n"
    "Pure standard-library Python - no third-party dependencies.\n"
    "6.671 legacy ping sites + 18 verified modern targets, plus 48 "
    "configurable index services."
)
DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# Ping targets
# --------------------------------------------------------------------------
# The original binary keeps two kinds of destinations:
#   * a large list of statistics/whois hosts that are pinged with the plain
#     target URL appended to the host root
#   * a small list of "authority" statistics services that take a path
#     segment plus the target URL
#
# Both forms were recovered from the unpacked image of SEO_Indexer.exe and
# from the site's own pr_cache.dat list file.

# Host + optional path prefix.  The target URL is appended verbatim (it keeps
# its scheme, exactly like the original does).
STATISTICS_TARGETS: tuple[tuple[str, str], ...] = (
    ("https://www.alexa.com", "/siteinfo/"),
    ("https://www.siteadvisor.com", "/sites/"),
    ("http://traffic.alexa.com", "/graph/"),
    ("http://grapher.compete.com", "/"),
    ("http://siteanalytics.compete.com", "/"),
    ("http://quantcast.com", "/"),
    ("https://api.thumbalizr.com", "/api/v1/"),
    ("https://open.thumbshots.org", "/?url="),
    ("https://www.pagepeeker.com", "/r/"),
    ("https://www.majesticseo.com", "/charts/backlinks-discovery?url="),
    ("http://www.yandex.ru", "/cycounter?url="),
)

# Search engines are present in the original binary's list, but a search
# result page is not a backlink source: it never links out to the target and
# it only produces a self-referencing URL.  They are therefore kept separate
# and are not used by default.
SEARCH_ENGINE_TARGETS: tuple[tuple[str, str], ...] = (
    ("https://www.bing.com", "/search?q="),
    ("https://www.google.com", "/search?q="),
    ("https://trends.google.com", "/trends/viz?q="),
)

# Statistic sites that expose the submitted URL through a query parameter
# instead of a path segment.
QUERY_STATISTICS_TARGETS: tuple[str, ...] = (
    "https://open.thumbshots.org/",
    "https://www.websnapr.com/",
    "https://www.majesticseo.com/",
)

# Hosts that simply take the target URL appended to their root.
PLAIN_HOST_TARGETS: tuple[str, ...] = (
    "www.strongtool.com",
    "www.websiteloutlook.com",
    "www.linkadd.info",
)


@dataclass(frozen=True)
class PingTarget:
    """A single destination that accepts a URL for indexing.

    ``mode`` selects how the target URL is combined with the destination:

    ``append``
        ``<base><target>`` - the target keeps its scheme and is glued straight
        onto the base.  This is the shape the original binary uses for its
        statistics services (``/siteinfo/`` + ``http://example.com``).
    ``query``
        ``<base>?url=<urlencoded target>`` - used for the statistic sites that
        expose the submitted URL through a query parameter.
    ``post``
        ``<base>`` with the target sent as the request body.
    """

    url: str
    kind: str = "statistics"  # statistics | plain | index_service
    mode: str = "append"

    def with_payload(self, payload: str) -> str:
        """Return the concrete request URL for ``payload``.

        Two placeholder styles are supported in the target URL:

        ``{url}``  the payload, unencoded
        ``{enc}``  the payload, URL-encoded
        """
        if self.mode == "post":
            return self.url
        if "{url}" in self.url or "{enc}" in self.url:
            return (
                self.url.replace("{url}", payload)
                .replace("{enc}", urllib.parse.quote(payload, safe=""))
            )
        if self.mode == "query":
            # The target already names the parameter it wants
            # ("?url=", "?query=", ...).  Append the payload to the *value*
            # of that parameter, so the finished URL has exactly one
            # populated parameter instead of an empty one plus a second.
            base, sep, tail = self.url.partition("?")
            if sep:
                key, eq, _old = tail.partition("=")
                return f"{base}?{key}={urllib.parse.quote(payload, safe='')}"
            return f"{self.url}?url={urllib.parse.quote(payload, safe='')}"
        if self.url.endswith("="):
            return self.url + urllib.parse.quote(payload, safe="")
        if self.url.endswith("/"):
            return self.url + payload
        return self.url + "/" + payload


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass
class Settings:
    # Measured on the recovered site list: throughput peaks around 50
    # threads (14.5 req/s) and drops again at 100 because the connections
    # start to overwhelm the servers.
    threads: int = 50
    timeout: float = 5.0
    delay: float = 0.0
    use_proxy: bool = False
    proxy_list: list[str] = field(default_factory=list)
    # Public proxies die within a request or two, so a proxy that fails is
    # retired instead of being picked again.
    retire_failed_proxies: bool = True
    check_url: bool = True
    site_filter: str = ""
    delete_when_done: bool = False
    report_format: str = "csv"
    report_with_link_type: bool = False
    user_agents: list[str] = field(default_factory=list)
    follow_redirects: bool = True
    max_sites: int = 0  # 0 == unlimited
    random_delay: bool = True
    # Measured: 42% of the successful requests need more than 3 s and
    # those account for 62% of the time.  Cutting the cap from 10 s to 5 s
    # is 1.5x faster with an identical number of HTTP replies, backlinks
    # and listings - the long tail was only ever timeouts.
    connect_timeout: float = 4.0
    read_timeout: float = 5.0
    skip_dead_hosts: bool = True
    dead_ttl: float = 30 * 60.0
    # Dead hosts survive across processes: the legacy list is mostly dead
    # domains, and without persistence every run pays again to rediscover
    # that, one connect timeout at a time.  An empty path means the
    # default cache file next to config.ini.
    persist_host_cache: bool = True
    host_cache_path: str = ""
    # DNS is 11% of the request time; an in-process cache stops the two
    # forms of one site (which share a host) from each paying a cold
    # resolve when their requests are in flight at the same time.
    dns_cache: bool = True
    dns_ttl: float = 300.0
    dns_negative_ttl: float = 30.0
    max_body_bytes: int = 200_000
    # Profiling showed the pre-flight probe is pure overhead on this
    # workload: most hosts in the list still answer, so the probe costs
    # a round of connections without saving many requests.  It stays
    # available behind --preflight for lists that are mostly dead.
    preflight: bool = False
    # Batching is 4.5x faster but drops the backlinks (a site usually
    # stores only the first URL of a batch), so it stays opt-in.
    batch_size: int = 0
    # Reuse the TCP/TLS connection between requests to the same host.
    # Measured in isolation this is 2.3-2.6x faster for repeated requests
    # to ONE host, but in a real run it is ~10% SLOWER: the bottleneck is
    # server latency, not the handshake, and holding a connection open
    # limits how many hosts can be worked at once.  Off by default.
    reuse_connections: bool = False
    pool_per_host: int = 2
    pool_max_total: int = 96
    # ---- GUI-side state, persisted in the same config.ini ---------------
    # Which target list to submit to.  "modern" is the 2024-2026 verified
    # list (~37x more real backlinks per request than the legacy list).
    target_list: str = "legacy"  # legacy | modern | both
    fast_mode: bool = True
    repair_misses: bool = True
    statistics_only: bool = False
    # The original's "custom mode" (config.ini: "custom mode" /
    # "custom mode number", default 800): submit to only the first N ping
    # sites instead of the whole list.  This caps SITES, not targets -
    # each site still yields its two submission forms, unlike max_sites
    # which truncates the final target list.
    custom_mode: bool = False
    custom_mode_number: int = 800
    # The original's "deep link indexer only" (config key of the same
    # name): submit only to sites that index deep links - inner-page URLs
    # like example.com/blog/post, not just root domains.  The recovered
    # legacy ping list carries no per-site capability flags, so the clone
    # maps this to the site sets PROVEN to store full URLs: the authority
    # statistics hosts and the modern verified list.
    deep_link_only: bool = False
    # The original's "min to tray": minimising hides the window to the
    # notification area instead of the taskbar (GUI only; the key round-
    # trips here so a GSA-written config.ini loads unchanged).
    min_to_tray: bool = False
    theme: str = "light"  # light | dark (GUI only)

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Settings":
        """Read config.ini, falling back to defaults for missing keys.

        Key names mirror the original program's own ``config.ini`` where it
        had one (``threads``, ``fast mode``, ``check url``, ``use proxy``,
        ``delete when done``, ``report format csv``, ``report with link
        type``, ``site filter``) so a config written by GSA SEO Indexer
        itself still loads.
        """
        import configparser

        cfg = configparser.ConfigParser()
        if os.path.exists(path):
            cfg.read(path, encoding="utf-8")
        s = cls()

        def get(sec: str, key: str, cast, cur):
            try:
                if cast is bool:
                    raw = cfg.get(sec, key, fallback=None)
                    if raw is None:
                        return cur
                    return raw.strip().lower() in ("1", "true", "yes", "on")
                if cast is int:
                    return cfg.getint(sec, key, fallback=cur)
                if cast is float:
                    return cfg.getfloat(sec, key, fallback=cur)
                return cfg.get(sec, key, fallback=cur)
            except (ValueError, TypeError):
                return cur

        if cfg.has_section("misc"):
            s.threads = get("misc", "threads", int, s.threads)
            s.timeout = get("misc", "timeout", float, s.timeout)
            s.delay = get("misc", "delay", float, s.delay)
            s.check_url = get("misc", "check url", bool, s.check_url)
            s.use_proxy = get("misc", "use proxy", bool, s.use_proxy)
            s.delete_when_done = get("misc", "delete when done", bool, s.delete_when_done)
            raw_fmt = get("misc", "report format csv", str, "1" if s.report_format == "csv" else "0")
            s.report_format = (
                "csv" if str(raw_fmt).strip().lower() in ("1", "true", "yes", "on", "csv") else "json"
            )
            s.report_with_link_type = get("misc", "report with link type", bool, s.report_with_link_type)
            s.follow_redirects = get("misc", "follow redirects", bool, s.follow_redirects)
            s.max_sites = get("misc", "max sites", int, s.max_sites)
            s.random_delay = get("misc", "random delay", bool, s.random_delay)
            s.connect_timeout = get("misc", "connect timeout", float, s.connect_timeout)
            s.read_timeout = get("misc", "read timeout", float, s.read_timeout)
            s.skip_dead_hosts = get("misc", "skip dead hosts", bool, s.skip_dead_hosts)
            s.persist_host_cache = get("misc", "persist host cache", bool, s.persist_host_cache)
            s.host_cache_path = get("misc", "host cache", str, s.host_cache_path).strip()
            s.dns_cache = get("misc", "dns cache", bool, s.dns_cache)
            s.dns_ttl = get("misc", "dns ttl", float, s.dns_ttl)
            s.dns_negative_ttl = get("misc", "dns negative ttl", float, s.dns_negative_ttl)
            s.preflight = get("misc", "preflight", bool, s.preflight)
            s.reuse_connections = get("misc", "connection pool", bool, s.reuse_connections)
            s.batch_size = get("misc", "batch size", int, s.batch_size)
            s.fast_mode = get("misc", "fast mode", bool, s.fast_mode)
            s.repair_misses = get("misc", "repair misses", bool, s.repair_misses)
            s.statistics_only = get("misc", "statistics only", bool, s.statistics_only)
            s.custom_mode = get("misc", "custom mode", bool, s.custom_mode)
            s.custom_mode_number = get("misc", "custom mode number", int, s.custom_mode_number)
            s.deep_link_only = get("misc", "deep link indexer only", bool, s.deep_link_only)
            s.min_to_tray = get("misc", "min to tray", bool, s.min_to_tray)
            th = get("misc", "theme", str, s.theme).strip().lower()
            if th in ("light", "dark"):
                s.theme = th
            tl = get("misc", "target list", str, s.target_list).strip().lower()
            if tl in ("legacy", "modern", "both"):
                s.target_list = tl
            s.proxy_list = [
                p.strip() for p in get("misc", "proxy list", str, "").split(",") if p.strip()
            ]
        if cfg.has_section("options"):
            s.site_filter = get("options", "site filter", str, s.site_filter)
        return s

    def save(self, path: str | os.PathLike) -> None:
        """Write config.ini; the GUI and the CLI share the same file."""
        import configparser

        cfg = configparser.ConfigParser()
        cfg["misc"] = {
            "threads": str(self.threads),
            "timeout": f"{self.timeout:g}",
            "delay": f"{self.delay:g}",
            "check url": "1" if self.check_url else "0",
            "use proxy": "1" if self.use_proxy else "0",
            "delete when done": "1" if self.delete_when_done else "0",
            "report format csv": "1" if self.report_format == "csv" else "0",
            "report with link type": "1" if self.report_with_link_type else "0",
            "follow redirects": "1" if self.follow_redirects else "0",
            "max sites": str(self.max_sites),
            "random delay": "1" if self.random_delay else "0",
            "connect timeout": f"{self.connect_timeout:g}",
            "read timeout": f"{self.read_timeout:g}",
            "skip dead hosts": "1" if self.skip_dead_hosts else "0",
            "persist host cache": "1" if self.persist_host_cache else "0",
            "host cache": self.host_cache_path,
            "dns cache": "1" if self.dns_cache else "0",
            "dns ttl": f"{self.dns_ttl:g}",
            "dns negative ttl": f"{self.dns_negative_ttl:g}",
            "preflight": "1" if self.preflight else "0",
            "connection pool": "1" if self.reuse_connections else "0",
            "batch size": str(self.batch_size),
            "fast mode": "1" if self.fast_mode else "0",
            "repair misses": "1" if self.repair_misses else "0",
            "statistics only": "1" if self.statistics_only else "0",
            "custom mode": "1" if self.custom_mode else "0",
            "custom mode number": str(self.custom_mode_number),
            "deep link indexer only": "1" if self.deep_link_only else "0",
            "min to tray": "1" if self.min_to_tray else "0",
            "theme": self.theme,
            "target list": self.target_list,
            "proxy list": ",".join(self.proxy_list),
        }
        cfg["options"] = {"site filter": self.site_filter}
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# GSA SEO Indexer clone - shared by the GUI and the CLI\n")
            cfg.write(fh)


# --------------------------------------------------------------------------
# Statistics of a run
# --------------------------------------------------------------------------
@dataclass
class Result:
    target: str
    url: str
    status: str  # submitted | failed | skipped | not-indexed
    code: int = 0
    indexable: bool = False
    listed: bool = False
    elapsed_ms: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "submitted"


@dataclass
class RunStats:
    total: int = 0
    submitted: int = 0
    failed: int = 0
    skipped: int = 0
    started: float = field(default_factory=time.time)
    last_result: Result | None = None

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    @property
    def links_per_minute(self) -> float:
        mins = self.elapsed / 60.0
        return (self.submitted / mins) if mins > 0 else 0.0


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------
class Indexer:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.stats = RunStats()
        self.results: list[Result] = []
        self._ua_pool = list(self.settings.user_agents) or DEFAULT_USER_AGENTS
        self._proxy_pool = list(self.settings.proxy_list)
        self._good_proxies = list(self.settings.proxy_list)
        self._retired: set[str] = set()
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE
        self.host_cache = HostCache(dead_ttl=self.settings.dead_ttl)
        self.skipped_dead = 0
        self.restored_dead = 0
        if self.settings.persist_host_cache:
            self.restored_dead = self.host_cache.load(self._host_cache_path())
        # A memoising resolver serves both the fetch path (urllib resolves
        # through socket.getaddrinfo internally) and the liveness probe.
        self.dns = DnsCache(ttl=self.settings.dns_ttl, negative_ttl=self.settings.dns_negative_ttl)
        if self.settings.dns_cache:
            activate_dns_cache(self.dns)
        self._client: PooledHttpClient | None = None
        self._clients: dict[str, PooledHttpClient] = {}
        self._client_lock = threading.Lock()

    def _host_cache_path(self) -> Path:
        """Where the dead-host cache lives; the default sits next to config.ini."""
        if self.settings.host_cache_path:
            return Path(self.settings.host_cache_path)
        return CACHE_DIR / "host_cache.json"

    # -- lifecycle --------------------------------------------------------
    def apply_settings(self, settings: Settings) -> None:
        """Adopt new settings for the next run without losing run history.

        The GUI used to build a fresh ``Indexer`` per run, which threw away
        the dead-host cache every time; on the legacy list that cache is
        the single biggest speed-up on a second run, so only the parts that
        genuinely start fresh (proxy pool, user agents) are reset here.
        """
        self.settings = settings
        self._ua_pool = list(settings.user_agents) or DEFAULT_USER_AGENTS
        self._proxy_pool = list(settings.proxy_list)
        self._good_proxies = list(settings.proxy_list)
        self._retired = set()
        self.host_cache.dead_ttl = settings.dead_ttl

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # -- helpers ----------------------------------------------------------
    def _pick_ua(self) -> str:
        return random.choice(self._ua_pool)

    def _pick_proxy(self) -> str | None:
        """Choose a proxy, preferring the ones that have not failed yet.

        Public proxies typically survive one or two requests, so a proxy
        that just failed is retired and the next one is tried.  When the
        whole pool has been marked bad, the pool is reset once, because
        most of the failures are transient.
        """
        if not self.settings.use_proxy or not self._proxy_pool:
            return None
        with self._lock:
            if not self._proxy_pool:
                return None
            if not self._good_proxies:
                if self.settings.retire_failed_proxies and self._retired:
                    # everything failed - give the pool a second chance
                    self._good_proxies = list(self._proxy_pool)
                    self._retired = set()
                else:
                    self._good_proxies = list(self._proxy_pool)
            proxy = random.choice(self._good_proxies)
        return proxy

    def _retire_proxy(self, proxy: str | None) -> None:
        """Mark a proxy as unusable for the rest of the run."""
        if not proxy or not self.settings.retire_failed_proxies:
            return
        with self._lock:
            if proxy in self._good_proxies:
                self._good_proxies.remove(proxy)
            self._retired.add(proxy)

    def _opener(self, proxy: str | None) -> urllib.request.OpenerDirector:
        """Build an opener for one request.

        urllib opens a fresh connection per request, so the TCP and TLS
        handshake (34% of the request time in the profile) is paid every
        time.  An ``http.client`` session would keep the connection
        alive, but urllib does not expose that; the per-host connection
        pool in :mod:`.perf` is used instead when it is enabled.
        """
        handlers: list[urllib.request.BaseHandler] = [
            urllib.request.HTTPSHandler(context=self._ssl_ctx),
        ]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            handlers.append(urllib.request.ProxyHandler({}))
        if not self.settings.follow_redirects:
            handlers.append(_NoRedirect())
        return urllib.request.build_opener(*handlers)

    # -- URL health check -------------------------------------------------
    def check_url(self, url: str) -> tuple[bool, str]:
        """Verify the target URL is reachable and indexable.

        Returns ``(ok, reason)``.  A URL is considered indexable when the
        server answers and the page does not carry a ``noindex`` directive.
        """
        if not url.lower().startswith(("http://", "https://")):
            url = "http://" + url
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self._pick_ua(),
                "Accept": "text/html,application/xhtml+xml",
            },
            method="GET",
        )
        try:
            with self._opener(self._pick_proxy()).open(req, timeout=self.settings.timeout) as resp:
                body = resp.read(120000).decode("utf-8", "replace").lower()
                code = resp.status
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 405, 429):
                return True, f"reachable but not readable ({exc.code})"
            return exc.code < 500, f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - report any transport error
            return False, str(exc)[:120]

        if "noindex" in body:
            return False, "page contains noindex"
        if code >= 400:
            return False, f"HTTP {code}"
        return True, "ok"

    # -- single submission ------------------------------------------------
    def submit_one(self, target: PingTarget, payload: str) -> Result:
        url = target.with_payload(payload)
        host = host_of(url)
        proxy = self._pick_proxy()
        started = time.perf_counter()

        # A host that is known to be dead is skipped outright: on a list
        # full of dead domains this is the single biggest saving, because
        # otherwise every one of them burns a thread slot for a full
        # connect timeout.
        if self.settings.skip_dead_hosts and host and self.host_cache.is_dead(host):
            with self._lock:
                self.skipped_dead += 1
            return Result(
                target=payload,
                url=url,
                status="skipped",
                code=0,
                error="host known dead",
            )

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self._pick_ua(),
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
            },
            method="GET",
        )
        try:
            body, code = self._fetch(url, req, proxy)
            elapsed = int((time.perf_counter() - started) * 1000)
            if host:
                self.host_cache.record_success(host, elapsed)
            kind = _classify_link(body, payload)
            return Result(
                target=payload,
                url=url,
                status="submitted",
                code=code,
                indexable=(kind == "indexed"),
                listed=(kind == "listed"),
                elapsed_ms=elapsed,
            )
        except urllib.error.HTTPError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            # An HTTP error means the host answered: it is alive, just not
            # willing to store the link.
            if host and exc.code < 500:
                self.host_cache.record_success(host, elapsed)
            elif host and not proxy:
                self.host_cache.record_failure(host, f"HTTP {exc.code}")
            if exc.code in (407, 429, 500, 502, 503):
                self._retire_proxy(proxy)
            return Result(
                target=payload,
                url=url,
                status="failed",
                code=exc.code,
                elapsed_ms=elapsed,
                error=f"HTTP {exc.code}",
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - started) * 1000)
            # Only a failure that came from the target itself marks the host
            # as dead.  A proxy that times out says nothing about the
            # target, and marking the host dead on that basis would skip
            # it for the rest of the run.
            if host and not proxy:
                self.host_cache.record_failure(host, str(exc))
            self._retire_proxy(proxy)
            return Result(
                target=payload,
                url=url,
                status="failed",
                code=0,
                elapsed_ms=elapsed,
                error=str(exc)[:160],
            )

    def _fetch(self, url: str, req: urllib.request.Request, proxy: str | None = None) -> tuple[str, int]:
        """Fetch a URL, preferring the pooled client.

        The pooled client keeps the connection open, which removes the TCP
        and TLS handshake from the critical path.  urllib is kept as the
        fallback for the cases the pool cannot serve (a proxy, or a
        transport error the pool ran into), so behaviour never regresses.
        """
        if self.settings.reuse_connections:
            resp = self._http(proxy or "").get(
                url,
                headers={"User-Agent": self._pick_ua()},
                follow_redirects=self.settings.follow_redirects,
            )
            if resp.status != 0:
                return resp.body[: self.settings.max_body_bytes], resp.status
            # the pooled client failed - fall back to urllib once
        with self._opener(proxy).open(req, timeout=self._timeout()) as resp:
            return resp.read(self.settings.max_body_bytes).decode("utf-8", "replace"), resp.status

    def _timeout(self) -> float:
        """The timeout used for a single request."""
        return max(self.settings.connect_timeout, self.settings.read_timeout)

    def _http(self, proxy: str = "") -> PooledHttpClient:
        """The pooled HTTP client for a given proxy (created on first use).

        A client is bound to one proxy, because the connection target
        depends on it; a client per proxy keeps the pool keys correct.
        """
        if not proxy:
            if self._client is None:
                with self._client_lock:
                    if self._client is None:
                        self._client = self._new_client(None)
            return self._client
        client = self._clients.get(proxy)
        if client is None:
            with self._client_lock:
                client = self._clients.get(proxy)
                if client is None:
                    client = self._new_client(proxy)
                    self._clients[proxy] = client
        return client

    def _new_client(self, proxy: str | None) -> PooledHttpClient:
        return PooledHttpClient(
            timeout=self.settings.read_timeout,
            connect_timeout=self.settings.connect_timeout,
            max_per_host=self.settings.pool_per_host,
            max_total=self.settings.pool_max_total,
            user_agents=self._ua_pool,
            proxy=proxy,
        )

    def close(self) -> None:
        """Release the pooled connections and stop intercepting DNS."""
        deactivate_dns_cache()
        if self._client is not None:
            self._client.close()
            self._client = None
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def submit_batch(self, target: PingTarget, payloads: Sequence[str]) -> list[Result]:
        """Submit several URLs to one site in a single request.

        Statistic sites accept several URLs at once, separated by a newline or
        a comma.  The original program submits one URL per request by default
        but the same sites honour a batch, which is much faster.
        """
        if not payloads:
            return []
        if len(payloads) == 1:
            return [self.submit_one(target, payloads[0])]
        # Statistic sites accept several URLs in one request; they separate
        # them with a newline (URL-encoded) or a comma.
        if target.mode == "query":
            joined = "\n".join(payloads)
        else:
            joined = ",".join(payloads)
        url = target.with_payload(joined)
        started = time.perf_counter()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self._pick_ua(),
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Referer": "https://www.google.com/",
            },
            method="GET",
        )
        try:
            with self._opener(self._pick_proxy()).open(req, timeout=self.settings.timeout) as resp:
                body = resp.read(300000).decode("utf-8", "replace")
                code = resp.status
            elapsed = int((time.perf_counter() - started) * 1000)
            out = []
            for p in payloads:
                kind = _classify_link(body, p)
                out.append(
                    Result(
                        target=p,
                        url=url,
                        status="submitted",
                        code=code,
                        indexable=(kind == "indexed"),
                        listed=(kind == "listed"),
                        elapsed_ms=elapsed,
                    )
                )
            return out
        except urllib.error.HTTPError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return [
                Result(
                    target=p,
                    url=url,
                    status="failed",
                    code=exc.code,
                    elapsed_ms=elapsed,
                    error=f"HTTP {exc.code}",
                )
                for p in payloads
            ]
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - started) * 1000)
            return [
                Result(
                    target=p,
                    url=url,
                    status="failed",
                    code=0,
                    elapsed_ms=elapsed,
                    error=str(exc)[:160],
                )
                for p in payloads
            ]

    # -- bulk run ---------------------------------------------------------
    def run(
        self,
        urls: Sequence[str],
        targets: Sequence[PingTarget],
        on_result: Callable[[Result], None] | None = None,
        batch_size: int = 0,
        reset: bool = True,
    ) -> list[Result]:
        """Submit every url to every target using a thread pool.

        ``batch_size`` > 0 groups that many URLs into a single request per
        site, which is much faster on the statistic sites that accept it.

        ``reset`` is turned off by the adaptive driver, which runs two
        passes and wants the results of both.
        """
        if reset:
            self._stop.clear()
            self.stats = RunStats(total=len(urls) * len(targets))
            self.results = []
            self.skipped_dead = 0

        if batch_size and batch_size > 1:
            chunks = [list(urls[i : i + batch_size]) for i in range(0, len(urls), batch_size)]
            work: list[tuple[PingTarget, list[str]]] = [(t, c) for t in targets for c in chunks]
        else:
            work = [(t, [u]) for t in targets for u in urls]
        if self.settings.max_sites:
            work = work[: self.settings.max_sites]

        def worker(item: tuple[PingTarget, list[str]]) -> list[Result]:
            if self._stop.is_set():
                return [Result(target=u, url=item[0].url, status="skipped", error="stopped") for u in item[1]]
            res = self.submit_batch(item[0], item[1])
            if self.settings.delay:
                time.sleep(self.settings.delay)
            elif self.settings.random_delay:
                time.sleep(random.uniform(0.01, 0.08))
            return res

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.settings.threads)) as pool:
            for results in pool.map(worker, work):
                if self._stop.is_set():
                    break
                for res in results:
                    with self._lock:
                        self.results.append(res)
                        if res.status == "submitted":
                            self.stats.submitted += 1
                        elif res.status == "skipped":
                            self.stats.skipped += 1
                        else:
                            self.stats.failed += 1
                        self.stats.last_result = res
                    if on_result:
                        # notification only - the bookkeeping above is the
                        # single owner of results and stats
                        on_result(res)
        # A run that learned which hosts are dead carries that knowledge
        # into the next process - even a stopped run has usually already
        # discovered most of them.
        if self.settings.persist_host_cache:
            self.host_cache.save(self._host_cache_path())
        return self.results

    def preflight(
        self,
        targets: Sequence[PingTarget],
        on_alive: Callable[[str], None] | None = None,
    ) -> set[str]:
        """Probe the hosts in parallel and return the ones that answered.

        The recovered list is dominated by dead domains.  Asking every
        worker to discover that on its own costs a full connect timeout per
        dead host, all of it serialised behind the thread pool.  Probing
        first turns N timeouts into one round of short parallel probes, and
        the submission pass then only talks to hosts that are actually up.
        """
        hosts: list[tuple[str, int]] = []
        seen: set[str] = set()
        for t in targets:
            h = host_of(t.url)
            if h and h not in seen:
                seen.add(h)
                hosts.append((h, port_of(t.url)))
        if not hosts:
            return set()

        alive: set[str] = set()
        lock = threading.Lock()

        def probe(item: tuple[str, int]) -> None:
            if self._stop.is_set():
                return
            host, port = item
            # A host the cache already vouches for needs no probe.
            if self.host_cache.is_known_alive(host):
                with lock:
                    alive.add(host)
                    if on_alive:
                        on_alive(host)
                return
            # Neither does one the cache has already written off: probing it
            # again would just burn a full connect timeout to relearn the
            # same answer.  It stays dead until the TTL in is_dead() expires.
            if self.host_cache.is_dead(host):
                return
            ok = self._probe_host(host, port)
            with lock:
                if ok:
                    alive.add(host)
                    # teach the cache, so a later probe can skip this host
                    self.host_cache.record_success(host)
                    if on_alive:
                        on_alive(host)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.settings.threads * 2)) as pool:
            list(pool.map(probe, hosts))
        return alive

    def _probe_host(self, host: str, port: int = 80) -> bool:
        """Cheap liveness probe: does the host complete a TCP connect?"""
        import socket

        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except Exception:
            self.host_cache.record_failure(host, "dns")
            return False
        last = None
        for family, socktype, proto, _canon, sockaddr in infos:
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                sock.settimeout(min(self.settings.connect_timeout, 4.0))
                sock.connect(sockaddr)
                sock.close()
                return True
            except Exception as exc:  # noqa: BLE001
                last = exc
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
        if last is not None:
            self.host_cache.record_failure(host, str(last))
        return False

    def run_batched(
        self,
        urls: Sequence[str],
        targets: Sequence[PingTarget],
        on_result: Callable[[Result], None] | None = None,
        batch_size: int = 10,
    ) -> list[Result]:
        """Submit in batches, then repair the URLs that did not land.

        Batching is dramatically faster (one request per site instead of
        one per URL), but a site that stores the submitted URL usually
        stores only the first one of a batch.  So after the batch pass the
        URLs that are not on the page are re-submitted one at a time, which
        keeps the speed-up without losing the links.
        """
        self._stop.clear()
        self.results = []
        self.skipped_dead = 0
        self.stats = RunStats(total=len(urls) * len(targets))

        primary = dedupe_by_host(targets)
        primary_all = list(primary)
        if self.settings.preflight:
            alive = self.preflight(primary)
            if alive:
                primary = [t for t in primary if host_of(t.url) in alive]


        # pass 1: batches
        batched = self.run(
            urls, primary, on_result=on_result, batch_size=batch_size, reset=False
        )

        # pass 2: repair - the URLs a site did not keep.
        # Only a host that actually answered is worth retrying; a host that
        # timed out would just burn another full timeout.
        by_host = {host_of(t.url): t for t in primary}
        repair: dict[str, list[str]] = {}
        answered: dict[str, bool] = {}
        for res in batched:
            h = host_of(res.url)
            if h not in answered:
                answered[h] = res.status == "submitted" or bool(res.code and res.code < 500)
            if res.status != "submitted":
                continue
            if res.indexable or res.listed:
                continue
            if h in by_host:
                repair.setdefault(h, []).append(res.target)

        if repair and not self._stop.is_set():
            repair_targets = [by_host[h] for h in repair]
            repair_urls = sorted({u for lst in repair.values() for u in lst})
            with self._lock:
                self.stats.total += len(repair_urls) * len(repair_targets)
            self.run(
                repair_urls, repair_targets, on_result=on_result, batch_size=0, reset=False
            )
        return self.results

    def run_adaptive(
        self,
        urls: Sequence[str],
        targets: Sequence[PingTarget],
        on_result: Callable[[Result], None] | None = None,
        batch_size: int = 0,
        use_fallback: bool = True,
    ) -> list[Result]:
        """Submit with one request per host, retrying only where needed.

        The recovered list offers two URL shapes per site.  Sending both
        unconditionally doubles the traffic, so this sends the first shape
        everywhere and then retries only the hosts that did not put the
        target on the page with the second shape.
        """
        self._stop.clear()
        self.results = []
        self.skipped_dead = 0

        primary = dedupe_by_host(targets)
        self.stats = RunStats(total=len(urls) * len(primary))

        # run() owns the bookkeeping; this is only a progress notification.
        def report(res: Result) -> None:
            if on_result:
                on_result(res)

        primary_all = list(primary)
        if self.settings.preflight:
            alive = self.preflight(primary)
            if alive:
                primary = [t for t in primary if host_of(t.url) in alive]


        first = self.run(urls, primary, on_result=report, batch_size=batch_size, reset=False)
        if not use_fallback or self._stop.is_set():
            return first

        # Retry only the hosts that actually answered but did not put the
        # target on the page.  A host that timed out or does not resolve is
        # dead - sending it a second request would just burn another full
        # timeout, which is exactly the cost this pass avoids.
        seen_hosts: set[str] = set()
        retry_hosts: set[str] = set()
        for res in first:
            h = host_of(res.url)
            if h in seen_hosts:
                continue
            seen_hosts.add(h)
            # A host that answered at all (any status below 500, plus
            # redirects) is worth trying with the other URL shape: the
            # shape can be wrong even when the host is healthy.  A host
            # that timed out or does not resolve is skipped, because a
            # second request would only burn another full timeout.
            answered = res.status == "submitted" or bool(res.code and res.code < 500)
            if not answered:
                continue
            if not (res.indexable or res.listed):
                retry_hosts.add(h)

        if not retry_hosts:
            return first

        by_host: dict[str, PingTarget] = {host_of(t.url): t for t in primary_all}
        second = [
            t for t in fallback_targets(targets)
            if host_of(t.url) in retry_hosts and host_of(t.url) in by_host
        ]
        if not second:
            return first

        with self._lock:
            self.stats.total += len(urls) * len(second)
        self.run(urls, second, on_result=report, batch_size=batch_size, reset=False)
        return self.results

    # -- reporting --------------------------------------------------------
    def write_report(self, path: str | os.PathLike) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.report_format == "json":
            with path.open("w", encoding="utf-8") as fh:
                json.dump([asdict(r) for r in self.results], fh, indent=1)
        else:
            with path.open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                if self.settings.report_with_link_type:
                    w.writerow(
                        [
                            "Target URL", "Submitted URL", "Status", "Code",
                            "Backlink", "Listed", "Elapsed ms", "Error",
                        ]
                    )
                else:
                    w.writerow(["Target URL", "Submitted URL", "Status", "Code", "Elapsed ms", "Error"])
                for r in self.results:
                    if self.settings.report_with_link_type:
                        w.writerow(
                            [
                                r.target, r.url, r.status, r.code,
                                r.indexable, r.listed, r.elapsed_ms, r.error,
                            ]
                        )
                    else:
                        w.writerow([r.target, r.url, r.status, r.code, r.elapsed_ms, r.error])
        return path


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _looks_indexed(body: str, payload: str) -> bool:
    """Heuristic: did the page expose our target URL as a real link?

    A bare echo of the query parameter does not count: the target only
    matters when it ends up in an ``<a href>`` on the page, which is what a
    crawler follows.  The check is deliberately strict - the anchor has to
    point at the target host, not merely contain the target text somewhere
    (a page full of ``example.com`` in its own markup is not a backlink).
    """
    return _classify_link(body, payload) == "indexed"


# Hosts whose result pages merely *list* the target among search hits.  A
# search engine is not a backlink source, so a hit there is reported
# separately from a real indexed link.
LISTING_ONLY_HOSTS = (
    "google.", "bing.", "yandex.", "baidu.", "duckduckgo.", "mojeek.",
    "seznam.", "naver.", "startpage.", "qwant.", "searx.", "ecosia.",
    "brave.com", "marginalia.nu",
)


# Matches a query string ("?a=b&c=d") inside a page, so that a URL echoed
# back by the site can be told apart from a real mention of the target.
_QUERY_ECHO_RE = re.compile(r"\?[^\"'<>\s]*|=\s*[\"'](https?://[^\"']*)[\"']")

_URL_RE = re.compile(
    r"^https?://"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,63}"
    r"(?::\d{1,5})?"
    r"(?:[/?#][^\s]*)?$"
)


def validate_url(url: str) -> tuple[bool, str]:
    """Cheap syntactic check of a target URL.

    Running the full reachability check costs a request per URL, but a
    syntactically broken URL would then be pushed to every one of the
    13,000 targets, so it is worth rejecting before the run starts.
    """
    u = url.strip()
    if not u:
        return False, "empty URL"
    if not u.lower().startswith(("http://", "https://")):
        return False, "missing http:// or https://"
    if " " in u:
        return False, "contains a space"
    if not _URL_RE.match(u):
        # fall back to a looser check so unusual but valid URLs are not lost
        host = u.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        if "." not in host or " " in host:
            return False, "not a valid host"
    return True, "ok"


def _classify_link(body: str, payload: str) -> str:
    """Classify how the target appears in ``body``.

    ``"indexed"``  an anchor on the page points at the target itself
    ``"listed"``   the target appears on a search-result page only
    ``""``         nothing usable
    """
    if not body or not payload:
        return ""
    needle = payload.rstrip("/")
    bare = needle.split("://", 1)[-1].rstrip("/")
    if not bare or "." not in bare:
        return ""
    host = bare.split("/", 1)[0].lower()

    for m in re.finditer(r'<a\b[^>]*\bhref=["\']([^"\']+)["\']', body, re.I):
        href = m.group(1).strip()
        h = href.lower()
        if not h or h.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        if h.startswith("/") or h.startswith("?") or h.startswith("."):
            continue  # relative link, or the page echoing its own query string
        if "://" not in h:
            continue
        after = h.split("://", 1)[1]
        link_host = after.split("/", 1)[0].split("?", 1)[0]
        if link_host != host:
            continue
        link_path = after.split("/", 1)[1] if "/" in after else ""
        target_path = bare.split("/", 1)[1] if "/" in bare else ""
        if link_path.split("?", 1)[0].strip("/") != target_path.split("?", 1)[0].strip("/"):
            continue
        return "indexed"

    # The target text shows up but not as a direct anchor: on a search-result
    # page that still means the engine listed it.  The query string is
    # removed first, because a page that merely echoes the submitted query
    # back (in the search box, or in its own links) has not listed
    # anything - and counting that echo would hide the very URLs that need
    # a second attempt.
    stripped = _QUERY_ECHO_RE.sub(" ", body)
    if bare.lower() in stripped.lower() or payload.lower() in stripped.lower():
        return "listed"
    return ""


DEFAULT_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]


# --------------------------------------------------------------------------
# Site list handling
# --------------------------------------------------------------------------
def load_ping_sites(path: str | os.PathLike | None = None) -> list[str]:
    """Load the ping site list (the original's ``pr_cache.dat`` equivalent)."""
    path = Path(path) if path else DATA_DIR / "ping_sites.txt"
    if not path.exists():
        return []
    sites: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            sites.append(line)
    return sites


def load_modern_targets(path: str | os.PathLike | None = None) -> list[PingTarget]:
    """Load the modern (2024-2026) target list.

    The site list recovered from GSA v3.09 dates from 2019-2023 and most of
    those sites no longer store the submitted URL.  This list holds the
    endpoints that were verified to actually expose the target as a real
    link when checked on 2026-10-21.
    """
    path = Path(path) if path else DATA_DIR / "modern_targets.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[PingTarget] = []
    for item in data.get("targets", []):
        url = item.get("url")
        if not url:
            continue
        out.append(
            PingTarget(
                url=url,
                kind=item.get("kind", "modern"),
                mode=item.get("mode", "append"),
            )
        )
    return out


def save_ping_sites(sites: Iterable[str], path: str | os.PathLike | None = None) -> Path:
    path = Path(path) if path else DATA_DIR / "ping_sites.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sites) + "\n", encoding="utf-8")
    return path


def dedupe_by_host(targets: Sequence[PingTarget]) -> list[PingTarget]:
    """Keep one target per host.

    The recovered site list contributes two shapes for every site (the
    ``?url=`` query form and the path form) and the two live on the same
    host.  Sending both doubles the request count for the same number of
    hosts.  Which shape works differs per site, so the second one is kept
    as a *fallback*: it is only tried when the first shape did not put the
    target on the page.
    """
    best: dict[str, PingTarget] = {}
    for t in targets:
        h = host_of(t.url)
        if h not in best:
            best[h] = t
    return list(best.values())


def fallback_targets(targets: Sequence[PingTarget]) -> list[PingTarget]:
    """The alternate shape for every host, used when the first one failed."""
    seen: dict[str, int] = {}
    out: list[PingTarget] = []
    for t in targets:
        h = host_of(t.url)
        n = seen.get(h, 0)
        seen[h] = n + 1
        if n == 1:  # the second target for this host
            out.append(t)
    return out


def apply_custom_mode(sites: Sequence[str], settings: "Settings") -> list[str]:
    """Cap the ping-site list when the original's custom mode is on.

    The original's config stores ``custom mode`` plus ``custom mode
    number`` (default 800); with the mode on, only the first N sites of
    the ordered ping list are used.  Statistics-only takes precedence in
    practice because it empties the list before this cap could matter.
    """
    if settings.custom_mode and settings.custom_mode_number > 0:
        return list(sites[: settings.custom_mode_number])
    return list(sites)


def default_targets(
    sites: Sequence[str] | None = None, include_search_engines: bool = False
) -> list[PingTarget]:
    """Build the target list: statistics services plus the ping site list."""
    targets: list[PingTarget] = []
    for base, path in STATISTICS_TARGETS:
        targets.append(PingTarget(url=base + path, kind="statistics", mode="append"))
    for base in QUERY_STATISTICS_TARGETS:
        targets.append(PingTarget(url=base, kind="statistics", mode="query"))
    if include_search_engines:
        for base, path in SEARCH_ENGINE_TARGETS:
            targets.append(PingTarget(url=base + path, kind="search_engine", mode="append"))
    for site in sites or ():
        if site.startswith(("http://", "https://")):
            # The ping sites from the original list expose the submitted URL
            # both as a path element and as a query parameter.  The query form
            # is the one that reliably leaves the target as a real anchor, so
            # it is offered first; the path form is kept as a fallback because
            # some sites only honour one of the two.
            base = site.rstrip("/")
            targets.append(PingTarget(url=base + "/", kind="plain", mode="query"))
            targets.append(PingTarget(url=base + "/", kind="plain", mode="append"))
    return targets


def read_url_file(path: str | os.PathLike) -> list[str]:
    """Read URLs from a text/CSV file, one per line, ignoring blanks/comments."""
    urls: list[str] = []
    p = Path(path)
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().strip('"')
        if not line or line.startswith("#"):
            continue
        if "," in line and "://" not in line:
            line = line.split(",")[0].strip()
        if line:
            urls.append(line)
    return urls
