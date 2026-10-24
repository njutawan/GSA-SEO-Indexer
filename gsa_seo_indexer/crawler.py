"""Crawl a website for URLs - the clone of the original's TForm11.

GSA SEO Indexer's Multiple-URLs form (TForm2) has a *Crawl Online* button
that opens TForm11 "Crawl for URLs": give it one seed URL and it walks the
site collecting links to submit.  The recovered control set is

  * levels deep          how many link levels to follow from the seed page
  * with anchor text     also capture the <a> label and DoFollow state
  * external sites       include links that point off the seed's site
  * same folder          only links in the seed URL's own directory
  * use proxies          fetch through the configured proxy

The exact per-field captions were not preserved in the DFM dump, so the
control *semantics* above are the fidelity target.

Behaviour decisions where the original is unknowable:

  * The seed URL itself is NOT part of the results - the user typed it, the
    crawl is meant to discover *new* URLs.
  * "levels deep = N" collects links found on pages up to N levels away;
    N=1 reads only the seed page.  External links are collected but never
    crawled, whatever "external sites" says - collecting a link is not
    permission to hammer a third-party site.
  * Two pages are "the same site" when their hostnames share the last two
    labels (www.example.com == example.com; a crude registrable-domain
    approximation, like GSA's own tools use).
  * Common asset extensions (images, css, js, ...) and non-http schemes
    (mailto:, javascript:, tel:, data:) are skipped.
  * robots.txt is not consulted - same as the original - so ``max_pages``
    and a small per-request delay are the guards that keep the walk polite.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from .httpclient import PooledHttpClient

SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".css", ".js", ".mjs", ".json", ".xml", ".zip", ".rar", ".7z", ".gz",
    ".exe", ".msi", ".dmg", ".mp3", ".mp4", ".avi", ".mkv", ".woff",
    ".woff2", ".ttf", ".eot",
}
SKIP_SCHEMES = ("mailto:", "javascript:", "tel:", "data:", "ftp:", "file:")


@dataclass
class CrawledURL:
    url: str
    anchor: str = ""
    dofollow: bool = True
    level: int = 0


@dataclass
class CrawlStats:
    urls: list[CrawledURL] = field(default_factory=list)
    pages_fetched: int = 0
    errors: list[str] = field(default_factory=list)
    stopped: bool = False

    @property
    def ok(self) -> bool:
        return not self.stopped and not self.errors


def _normalise(url: str) -> str:
    """Absolute http(s) URL without fragment, or "" when unusable."""
    u = urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        return ""
    return urlunsplit((u.scheme.lower(), u.netloc, u.path or "/", u.query, ""))


def _site_key(host: str) -> str:
    """www.example.com -> example.com (last two labels, lowercased)."""
    host = (host or "").lower().removeprefix("www.")
    labels = host.rstrip(".").split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def _folder(path: str) -> str:
    return path[: path.rfind("/") + 1] if "/" in path else "/"


def _is_asset(path: str) -> bool:
    dot = path.rfind(".")
    slash = path.rfind("/")
    # only a real extension (a dot after the last slash), not /file.name/
    return dot > slash and path[dot:].lower() in SKIP_EXTENSIONS


class _LinkParser(HTMLParser):
    """Collect (href, anchor text, dofollow) plus <base href>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base = ""
        self.links: list[tuple[str, str, bool]] = []
        self._href: str | None = None
        self._nofollow = False
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._href = a["href"]
            self._nofollow = "nofollow" in (a.get("rel") or "").lower()
            self._text = []
        elif tag == "base" and not self.base:
            self.base = a.get("href") or ""

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            self.links.append((self._href, text, not self._nofollow))
            self._href = None

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)


class Crawler:
    """BFS walk from one seed URL, level-limited and filterable."""

    def __init__(
        self,
        seed: str,
        depth: int = 2,
        external: bool = False,
        same_folder: bool = False,
        with_anchor_text: bool = True,
        max_pages: int = 300,
        delay: float = 0.2,
        proxy: str | None = None,
        timeout: float = 10.0,
        client: PooledHttpClient | None = None,
    ) -> None:
        self.seed = _normalise(seed)
        if not self.seed:
            raise ValueError(f"not a usable http(s) URL: {seed!r}")
        self.depth = max(1, int(depth))
        self.external = external
        self.same_folder = same_folder
        self.with_anchor_text = with_anchor_text
        self.max_pages = max_pages
        self.delay = delay
        self._stop = threading.Event()
        # one client for the whole walk: keep-alive pays off because the
        # crawl talks to one host over and over (unlike the submit engine)
        self.client = client or PooledHttpClient(timeout=timeout, proxy=proxy)
        seed_parts = urlsplit(self.seed)
        self._seed_site = _site_key(seed_parts.hostname or "")
        self._seed_folder = _folder(seed_parts.path)

    def stop(self) -> None:
        self._stop.set()

    def stopped(self) -> bool:
        return self._stop.is_set()

    # -- walk ---------------------------------------------------------------
    def run(self, on_page=None) -> CrawlStats:
        """Crawl and return what was found.

        ``on_page(stats, just_found)`` fires after every fetched page on the
        thread that runs the crawl; the GUI uses it to drain results.
        """
        stats = CrawlStats()
        seen: set[str] = {self.seed}
        queue: deque[tuple[str, int]] = deque([(self.seed, 0)])

        while queue and stats.pages_fetched < self.max_pages and not self._stop.is_set():
            url, level = queue.popleft()
            resp = self.client.get(url)
            stats.pages_fetched += 1
            ctype = (resp.headers.get("content-type") or "").lower()
            if resp.error:
                stats.errors.append(f"{url}: {resp.error}")
            elif not resp.ok or "html" not in ctype:
                stats.errors.append(f"{url}: HTTP {resp.status or '??'} {ctype or 'non-HTML'}")
            else:
                parser = _LinkParser()
                try:
                    parser.feed(resp.body)
                    parser.close()
                except Exception as exc:  # malformed HTML still yields partial links
                    stats.errors.append(f"{url}: parse {type(exc).__name__}")
                base = urljoin(resp.final_url or url, parser.base) if parser.base else (resp.final_url or url)
                found_now: list[CrawledURL] = []
                for href, anchor, dofollow in parser.links:
                    target = _normalise(urljoin(base, href))
                    if not target or target in seen or _is_asset(urlsplit(target).path):
                        continue
                    t_level = level + 1
                    if t_level > self.depth:
                        continue
                    seen.add(target)
                    t_parts = urlsplit(target)
                    if not self.external and _site_key(t_parts.hostname or "") != self._seed_site:
                        continue
                    if self.same_folder and _folder(t_parts.path) != self._seed_folder:
                        continue
                    entry = CrawledURL(
                        url=target,
                        anchor=anchor if self.with_anchor_text else "",
                        dofollow=dofollow,
                        level=t_level,
                    )
                    stats.urls.append(entry)
                    found_now.append(entry)
                    # collect external links, but never crawl them
                    internal = _site_key(t_parts.hostname or "") == self._seed_site
                    if internal and t_level < self.depth:
                        queue.append((target, t_level))
                if on_page:
                    on_page(stats, found_now)
            if self.delay and queue:
                time.sleep(self.delay + random.random() * 0.15)

        stats.stopped = self._stop.is_set()
        return stats
