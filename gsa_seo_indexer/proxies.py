"""Proxy scraping and testing.

GSA SEO Indexer hides the submission traffic behind scraped public proxies.
This module reproduces that: it downloads free proxy lists, tests them and
keeps the ones that actually answer.
"""

from __future__ import annotations

import concurrent.futures
import random
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable, Sequence

# Free proxy list sources, same spirit as the ones GSA ships in
# proxyfinder.ini.
PROXY_SOURCES: tuple[str, ...] = (
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
)

PROXY_RE = re.compile(
    r"^(?:(?P<scheme>https?|socks4|socks5)://)?"
    r"(?:(?P<user>[^:@/\s]+):(?P<pw>[^@/\s]+)@)?"
    r"(?P<host>[a-zA-Z0-9.\-_]+):(?P<port>\d{2,5})$"
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class Proxy:
    host: str
    port: int
    scheme: str = "http"
    user: str = ""
    password: str = ""

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def url(self) -> str:
        auth = f"{self.user}:{self.password}@" if self.user else ""
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    def __str__(self) -> str:
        return self.url


def is_supported_proxy(proxy: str | Proxy) -> bool:
    """True for plain HTTP(S) proxies.

    SOCKS4/SOCKS5 lines are parsed (they appear in public lists) but the
    client only speaks HTTP proxies, so they are filtered out before a
    run instead of failing one request at a time.
    """
    if isinstance(proxy, Proxy):
        return proxy.scheme in ("http", "https")
    raw = (proxy or "").strip()
    scheme = raw.split("://", 1)[0].lower() if "://" in raw else "http"
    return scheme in ("http", "https")


def parse_proxy_line(line: str) -> Proxy | None:
    """Parse one line of a proxy list."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = PROXY_RE.match(line)
    if not m:
        return None
    return Proxy(
        host=m.group("host"),
        port=int(m.group("port")),
        scheme=(m.group("scheme") or "http").lower(),
        user=m.group("user") or "",
        password=m.group("pw") or "",
    )


def parse_proxy_list(text: str) -> list[Proxy]:
    out: list[Proxy] = []
    seen: set[str] = set()
    for line in text.splitlines():
        p = parse_proxy_line(line)
        if p is None or p.address in seen:
            continue
        seen.add(p.address)
        out.append(p)
    return out


def download_proxy_lists(
    sources: Sequence[str] = PROXY_SOURCES, timeout: float = 20.0
) -> list[Proxy]:
    """Download and parse the free proxy lists."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    proxies: list[Proxy] = []
    seen: set[str] = set()
    for src in sources:
        try:
            req = urllib.request.Request(src, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                text = resp.read(4_000_000).decode("utf-8", "replace")
        except Exception:
            continue
        for p in parse_proxy_list(text):
            if p.address in seen:
                continue
            seen.add(p.address)
            proxies.append(p)
    return proxies


# The SSL context has to be attached to the handler, not passed to
# open(), or the proxy tunnel cannot be used.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def test_proxy(
    proxy: Proxy,
    timeout: float = 8.0,
    test_url: str = "https://www.gstatic.com/generate_204",
) -> bool:
    """Check whether a proxy can actually reach the internet.

    The default test URL is an **https** one on purpose.  The indexer only
    ever talks to https targets, and a proxy that cannot establish a
    CONNECT tunnel to port 443 is useless here even though it handles
    plain http perfectly well.  Testing http instead accepts a large
    number of proxies that fail on the first real request.
    """
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy.url, "https": proxy.url}),
            urllib.request.HTTPSHandler(context=_SSL_CTX),
        )
        req = urllib.request.Request(test_url, headers={"User-Agent": UA})
        with opener.open(req, timeout=timeout) as resp:
            return resp.status in (200, 204, 301, 302)
    except urllib.error.HTTPError as exc:
        # 407 means the proxy wants credentials; 403 from some proxies just
        # means they refuse this particular host.  Neither is a working
        # proxy for our purposes.
        return False
    except Exception:
        return False


def find_working_proxies(
    candidates: Iterable[Proxy],
    limit: int = 20,
    threads: int = 30,
    timeout: float = 8.0,
    test_url: str = "https://www.gstatic.com/generate_204",
) -> list[Proxy]:
    """Test proxies concurrently and return the working ones."""
    cand = list(candidates)
    random.shuffle(cand)
    found: list[Proxy] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        futures = {pool.submit(test_proxy, p, timeout, test_url): p for p in cand}
        for fut in concurrent.futures.as_completed(futures):
            p = futures[fut]
            try:
                if fut.result():
                    found.append(p)
                    if len(found) >= limit:
                        for f in futures:
                            f.cancel()
                        break
            except Exception:
                continue
    return found


def scrape_proxies(limit: int = 20, **kwargs) -> list[Proxy]:
    """Download the public lists, test them and return working proxies."""
    candidates = download_proxy_lists()
    return find_working_proxies(candidates, limit=limit, **kwargs)
