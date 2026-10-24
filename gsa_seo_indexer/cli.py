"""Command line front-end for the GSA SEO Indexer clone."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import engine, emulator as emu_mod, proxies as proxy_mod, services as service_mod
from .engine import Indexer, PingTarget, Settings, default_targets, load_ping_sites, read_url_file
from .watcher import UrlWatcher

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.ini"
REPORT_DIR = HERE / "reports"
SERVICE_DIR = HERE / "index_service"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gsa-seo-indexer",
        description="Submit URLs to thousands of statistics/whois sites so search engines "
        "discover them, using the mechanism recovered from GSA SEO Indexer v3.09.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  gsa-seo-indexer submit --url https://example.com/
  gsa-seo-indexer submit -f urls.txt --threads 20 --report reports/run.csv
  gsa-seo-indexer submit -f urls.txt --sites 200 --stats-only
  gsa-seo-indexer submit -f urls.txt --use-proxy --proxy 1.2.3.4:8080
  gsa-seo-indexer check --url https://example.com/
  gsa-seo-indexer sites --count
  gsa-seo-indexer services --list
  gsa-seo-indexer proxies --scrape --limit 10
  gsa-seo-indexer watch -f backlinks.log --sites 50
  gsa-seo-indexer crawl https://example.com/blog/ --depth 2 --out urls.txt
""",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"{engine.APP_NAME} v{engine.APP_VERSION}\n"
        f"Developed by {engine.APP_DEVELOPER} © {engine.APP_YEAR}",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("submit", help="submit URLs to the indexing sites")
    s.add_argument("--url", action="append", default=[], help="a target URL (repeatable)")
    s.add_argument("-f", "--file", help="text/CSV file with one URL per line")
    # Defaults are None so config.ini can supply them; a flag passed on
    # the command line always wins over the config file.
    s.add_argument("--threads", type=int, default=None,
                    help="parallel requests (measured peak: 50; default: config.ini or 50)")
    s.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="per-request timeout (5s is the measured sweet spot; default: config.ini)",
    )
    s.add_argument("--delay", type=float, default=None, help="fixed delay after each request")
    s.add_argument("--sites", type=int, default=None, help="limit number of sites (0 = all)")
    s.add_argument(
        "--batch",
        type=int,
        default=None,
        help="submit this many URLs per request (0 = one URL per request). "
        "Batching is 4.5x faster but a site usually stores only the first "
        "URL of a batch, so it costs the backlinks - opt in deliberately",
    )
    s.add_argument(
        "--repair",
        action="store_true",
        help="with --batch: re-submit the URLs a site did not keep, one by one "
        "(keeps the speed-up without losing links)",
    )
    s.add_argument("--report", help="write a report file (csv or json)")
    s.add_argument("--format", choices=["csv", "json"], default=None,
                    help="report format (default: config.ini or csv)")
    s.add_argument(
        "--with-link-type",
        action="store_true",
        default=None,
        help="add a Backlink column to the report (verified link vs. plain HTTP reply)",
    )
    s.add_argument("--proxy", action="append", default=[], help="proxy to use (repeatable)")
    s.add_argument("--use-proxy", action="store_true", help="route requests through proxies")
    s.add_argument(
        "--proxy-file",
        help="read proxies from a file (one per line) instead of --proxy",
    )
    s.add_argument(
        "--scrape-proxies",
        type=int,
        default=0,
        metavar="N",
        help="scrape N public proxies first and use them; they are validated "
        "against a real target before use",
    )
    s.add_argument("--site-filter", default=None, help="only use sites whose host contains this text")
    s.add_argument("--no-check", action="store_true", default=None, help="skip the pre-submission URL check")
    s.add_argument("--stats-only", action="store_true", default=None, help="only the statistics services, no site list")
    s.add_argument(
        "--custom",
        action="store_true",
        default=None,
        help="custom mode (the original's 'custom mode'): use only the first N ping sites",
    )
    s.add_argument(
        "--custom-number",
        type=int,
        default=None,
        metavar="N",
        help="N for --custom (default: config.ini or the original's 800)",
    )
    s.add_argument(
        "--deep-links",
        action="store_true",
        default=None,
        help="only sites that index deep links (the original's 'deep link indexer only'): "
        "skips the legacy list whose per-site capability is unknown, keeps the "
        "statistics services and the modern verified endpoints",
    )
    s.add_argument(
        "--modern",
        action="store_true",
        default=None,
        help="use the modern (2024-2026) verified target list instead of the legacy site list",
    )
    s.add_argument(
        "--no-legacy",
        action="store_true",
        default=None,
        help="skip the legacy 2019-2023 site list entirely",
    )
    s.add_argument(
        "--with-search-engines",
        action="store_true",
        help="also query search engines (they are not backlink sources, so off by default)",
    )
    s.add_argument("--no-redirects", action="store_true", default=None, help="do not follow redirects")
    s.add_argument(
        "--fast",
        action="store_true",
        default=None,
        help="one request per host, retry the alternate shape only where it failed "
        "(halves the request count)",
    )
    s.add_argument(
        "--pool",
        action="store_true",
        default=None,
        help="reuse the TCP/TLS connection between requests to the same host "
        "(faster when hammering one host, slower in a wide run)",
    )
    s.add_argument(
        "--no-skip-dead",
        action="store_true",
        default=None,
        help="do not skip hosts that already failed in this run",
    )
    s.add_argument(
        "--preflight",
        action="store_true",
        default=None,
        help="probe hosts first and skip the dead ones (pays off when most "
        "sites in the list are dead, costs a round of connections otherwise)",
    )
    s.add_argument(
        "--connect-timeout",
        type=float,
        default=None,
        help="separate connect timeout (default: config.ini or 4s)",
    )
    s.add_argument(
        "--read-timeout",
        type=float,
        default=None,
        help="read timeout (default: config.ini or 5s)",
    )
    s.add_argument("--quiet", action="store_true")

    c = sub.add_parser("check", help="check whether a URL is indexable")
    c.add_argument("--url", action="append", default=[])
    c.add_argument("-f", "--file")

    ls = sub.add_parser("sites", help="manage the ping site list")
    ls.add_argument("--list", action="store_true", help="print the site list")
    ls.add_argument("--count", action="store_true", help="print how many sites are loaded")
    ls.add_argument("--filter", default="", help="only show sites matching this text")
    ls.add_argument("--stats", action="store_true", help="print the built-in statistics services")

    sv = sub.add_parser("services", help="paid third-party indexing services")
    sv.add_argument("--list", action="store_true", help="list the service definitions")
    sv.add_argument("--test", metavar="NAME", help="submit to one service with --url")
    sv.add_argument("--url", action="append", default=[], help="target URL(s) for --test")
    sv.add_argument("--folder", default=str(SERVICE_DIR), help="folder holding the service INI files")
    sv.add_argument(
        "--set-key",
        nargs=2,
        metavar=("SERVICE", "VALUE"),
        help="store an API key for SERVICE (kept in gsa_seo_indexer\\keys.json)",
    )
    sv.add_argument(
        "--key-var",
        default="API-Key",
        help="name of the variable the --set-key value fills (default: API-Key)",
    )

    px = sub.add_parser("proxies", help="scrape and test public proxies")
    px.add_argument("--scrape", action="store_true", help="download the public proxy lists")
    px.add_argument("--limit", type=int, default=20, help="how many working proxies to keep")
    px.add_argument("--threads", type=int, default=30)
    px.add_argument("--timeout", type=float, default=8.0)
    px.add_argument("--save", help="write the working proxies to this file")
    px.add_argument("--test-file", help="test proxies listed in this file instead of downloading")

    em = sub.add_parser(
        "emulate",
        help="run a local server that simulates the index services (like GSA's own emulator)",
    )
    em.add_argument("--host", default=emu_mod.DEFAULT_HOST)
    em.add_argument("--port", type=int, default=emu_mod.DEFAULT_PORT)
    em.add_argument("--bind-all", action="store_true", help="listen on every interface")
    em.add_argument("--with-auth", action="store_true", help="require Authorization")
    em.add_argument("--login", default="")
    em.add_argument("--password", default="")
    em.add_argument("--api-key", default="")
    em.add_argument("--folder", default=str(SERVICE_DIR), help="folder with the service INI files")
    em.add_argument("--verbose", action="store_true")

    w = sub.add_parser("watch", help="watch files and submit new URLs as they appear")
    w.add_argument("-f", "--file", action="append", default=[], help="file or directory to watch")
    w.add_argument("--interval", type=float, default=5.0)
    w.add_argument("--sites", type=int, default=0)
    w.add_argument("--threads", type=int, default=Settings().threads)
    w.add_argument("--polls", type=int, default=0, help="stop after this many polls (0 = run forever)")

    cr = sub.add_parser("crawl", help="crawl a website for URLs (TForm11 'Crawl for URLs')")
    cr.add_argument("seed", help="seed URL to start from, e.g. https://example.com/blog/")
    cr.add_argument("--depth", type=int, default=2, help="levels deep (default 2)")
    cr.add_argument("--external", action="store_true", help="also collect links to other sites")
    cr.add_argument("--same-folder", action="store_true", help="only URLs in the seed's own folder")
    cr.add_argument("--no-anchors", action="store_true", help="skip anchor text / DoFollow columns")
    cr.add_argument("--max-pages", type=int, default=300, help="stop after fetching this many pages")
    cr.add_argument("--proxy", help="fetch through this proxy (host:port)")
    cr.add_argument("--out", help="write the plain URL list (one per line) to this file")
    cr.add_argument("--tsv", action="store_true", help="print url/anchor/dofollow/depth as TSV")

    return p


def read_proxy_file(path: str) -> list[str]:
    """Read a proxy list file (one proxy per line)."""
    out: list[str] = []
    p = Path(path)
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def collect_urls(args) -> list[str]:
    urls = list(getattr(args, "url", []) or [])
    if getattr(args, "file", None):
        urls.extend(read_url_file(args.file))
    seen, out = set(), []
    for u in urls:
        u = u.strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def cmd_submit(args) -> int:
    urls = collect_urls(args)
    if not urls:
        print("No URLs given. Use --url or --file.", file=sys.stderr)
        return 2

    # config.ini (written by the GUI or a previous run) supplies every
    # value the command line did not pass explicitly.
    cfg = Settings.load(CONFIG_PATH)

    def opt(cli_val, cfg_val):
        return cli_val if cli_val is not None else cfg_val

    no_legacy = opt(args.no_legacy, cfg.target_list == "modern")
    modern = opt(args.modern, cfg.target_list in ("modern", "both"))
    stats_only = opt(args.stats_only, cfg.statistics_only)
    cfg.custom_mode = opt(args.custom, cfg.custom_mode)
    cfg.custom_mode_number = opt(args.custom_number, cfg.custom_mode_number) or 0
    site_filter = opt(args.site_filter, cfg.site_filter) or ""
    max_sites = opt(args.sites, cfg.max_sites) or 0
    batch_size = opt(args.batch, cfg.batch_size) or 0
    fast = opt(args.fast, cfg.fast_mode)
    # --repair stays opt-in on the CLI even when the GUI saved it on.
    repair = bool(args.repair)

    sites = load_ping_sites()
    if site_filter:
        needle = site_filter.lower()
        sites = [s for s in sites if needle in s.lower()]
    # Custom mode caps SITES before default_targets doubles them into
    # query+append forms (the original's "custom mode number" = 800).
    sites = engine.apply_custom_mode(sites, cfg)

    targets: list[PingTarget] = []
    deep_links = opt(args.deep_links, cfg.deep_link_only)
    if not no_legacy:
        # "deep link indexer only": the legacy list carries no per-site
        # capability flags, so it is dropped; the statistics services (and
        # the modern list below) are the sets proven to store full URLs.
        targets.extend(
            default_targets(
                [] if (stats_only or deep_links) else sites,
                include_search_engines=args.with_search_engines,
            )
        )
    if modern:
        targets.extend(engine.load_modern_targets())
    if max_sites:
        targets = targets[:max_sites]

    threads = opt(args.threads, cfg.threads)
    if not args.quiet:
        print(f"{engine.APP_NAME} v{engine.APP_VERSION}")
        print(f"  URLs    : {len(urls)}")
        print(f"  Sites   : {len(targets)}")
        print(f"  Threads : {threads}")
        print(f"  Requests: {len(urls) * len(targets)}")
        print()

    proxy_list = list(args.proxy)
    proxy_from_flags = bool(args.proxy or args.proxy_file or args.scrape_proxies)
    if args.proxy_file:
        proxy_list.extend(read_proxy_file(args.proxy_file))
    if args.scrape_proxies:
        if not args.quiet:
            print(f"Scraping {args.scrape_proxies} public proxies ...")
        scraped = proxy_mod.scrape_proxies(limit=args.scrape_proxies)
        proxy_list.extend(p.url for p in scraped)
        if not args.quiet:
            print(f"  {len(scraped)} working proxy/proxies")
    # No proxy flag at all: fall back to the proxies saved in config.ini
    # (their use_proxy switch carries over with them).
    if not proxy_from_flags and cfg.use_proxy and cfg.proxy_list:
        proxy_list = list(cfg.proxy_list)
        use_proxy = True
    else:
        use_proxy = args.use_proxy

    settings = Settings(
        threads=threads,
        timeout=opt(args.timeout, cfg.timeout),
        delay=opt(args.delay, cfg.delay),
        use_proxy=use_proxy,
        proxy_list=proxy_list,
        check_url=not opt(args.no_check, not cfg.check_url),
        site_filter=site_filter,
        report_format=opt(args.format, cfg.report_format),
        report_with_link_type=bool(opt(args.with_link_type, cfg.report_with_link_type)),
        follow_redirects=not opt(args.no_redirects, not cfg.follow_redirects),
        max_sites=max_sites,
        connect_timeout=opt(args.connect_timeout, cfg.connect_timeout),
        read_timeout=opt(args.read_timeout, cfg.read_timeout),
        skip_dead_hosts=not opt(args.no_skip_dead, not cfg.skip_dead_hosts),
        preflight=bool(opt(args.preflight, cfg.preflight)),
        reuse_connections=bool(opt(args.pool, cfg.reuse_connections)),
        batch_size=batch_size,
        fast_mode=fast,
        repair_misses=repair,
    )
    indexer = Indexer(settings)

    # Reject obviously broken URLs before spending thousands of requests
    # on them.
    good: list[str] = []
    for u in urls:
        ok, reason = engine.validate_url(u)
        if ok:
            good.append(u)
        elif not args.quiet:
            print(f"  INVALID {u}  ({reason})")
    if not good:
        print("No valid URLs left.", file=sys.stderr)
        return 1
    urls = good

    if settings.check_url:
        if not args.quiet:
            print("Checking target URLs ...")
        ok_urls = []
        for u in urls:
            ok, reason = indexer.check_url(u)
            if ok:
                ok_urls.append(u)
            elif not args.quiet:
                print(f"  SKIP {u}  ({reason})")
        urls = ok_urls
        if not urls:
            print("No indexable URLs left.", file=sys.stderr)
            return 1

    last_line = 0.0
    start = time.time()

    def on_result(res) -> None:
        nonlocal last_line
        if args.quiet:
            return
        now = time.time()
        if now - last_line < 0.5 and res.status != "failed":
            return
        last_line = now
        s = indexer.stats
        pct = (s.submitted + s.failed) / max(1, s.total) * 100
        mark = "+" if res.status == "submitted" else "!"
        print(
            f"\r[{pct:5.1f}%] {s.submitted:6d} ok  {s.failed:5d} fail  "
            f"{s.links_per_minute:6.1f} LpM  {mark} {res.url[:70]:<70}",
            end="",
            flush=True,
        )

    if repair and batch_size > 1:
        indexer.run_batched(urls, targets, on_result=on_result, batch_size=batch_size)
    elif fast:
        indexer.run_adaptive(urls, targets, on_result=on_result, batch_size=batch_size)
    else:
        indexer.run(urls, targets, on_result=on_result, batch_size=batch_size)
    if not args.quiet:
        print()
        print()

    s = indexer.stats
    links = sum(1 for r in indexer.results if r.indexable)
    listed = sum(1 for r in indexer.results if r.listed and not r.indexable)
    print(f"Submitted  : {s.submitted}")
    print(f"Failed     : {s.failed}")
    print(f"Skipped    : {s.skipped}")
    print(f"Backlinks  : {links}  (target is a real link on the page)")
    print(f"Listed     : {listed}  (target appears as a search-result hit)")
    print(f"Links/min  : {s.links_per_minute:.1f}")
    print(f"Duration   : {time.time() - start:.1f}s")
    if indexer.skipped_dead:
        print(f"Skipped dead hosts: {indexer.skipped_dead} (no request sent)")
    cache = indexer.host_cache.stats()
    print(f"Host cache : {cache.get('alive', 0)} alive / {cache.get('dead', 0)} dead of {cache.get('hosts', 0)}")
    if indexer.restored_dead:
        print(f"  (restored {indexer.restored_dead} dead hosts from disk)")
    if indexer.settings.dns_cache:
        dns = indexer.dns.stats()
        if dns.get("hits", 0) or dns.get("misses", 0):
            print(f"DNS cache  : {dns.get('hits', 0)} hits / {dns.get('misses', 0)} misses of {dns.get('entries', 0)} hosts")

    if args.report:
        path = Path(args.report)
        if not path.is_absolute() and path.parent == Path("."):
            # a bare filename lands in the reports dir; a path that already
            # names a directory is used as given (relative to the working
            # directory) so reports do not end up nested inside it
            path = REPORT_DIR / path
        written = indexer.write_report(path)
        print(f"Report     : {written}")
    return 0


def cmd_check(args) -> int:
    urls = collect_urls(args)
    if not urls:
        print("No URLs given.", file=sys.stderr)
        return 2
    indexer = Indexer(Settings())
    bad = 0
    for u in urls:
        ok, reason = indexer.check_url(u)
        print(f"{'OK  ' if ok else 'FAIL'}  {u}  ({reason})")
        if not ok:
            bad += 1
    return 1 if bad == len(urls) else 0


def cmd_sites(args) -> int:
    if args.stats:
        for base, path in engine.STATISTICS_TARGETS:
            print(base + path)
        return 0
    sites = load_ping_sites()
    if args.filter:
        needle = args.filter.lower()
        sites = [s for s in sites if needle in s.lower()]
    if args.count:
        print(len(sites))
        return 0
    for s in sites:
        print(s)
    return 0


def cmd_services(args) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"No service folder at {folder}", file=sys.stderr)
        return 1
    svcs = service_mod.load_services(folder)
    if args.set_key:
        name, value = args.set_key
        names = {s.name for s in svcs}
        if names and name not in names:
            print(f"No service named {name!r}", file=sys.stderr)
            return 1
        service_mod.set_key(name, args.key_var, value)
        print(f"Key stored for {name} ({args.key_var}) in {service_mod.KEYS_PATH}")
        return 0
    keys = service_mod.load_keys()
    if args.test:
        match = [s for s in svcs if s.name.lower() == args.test.lower()]
        if not match:
            print(f"No service named {args.test!r}", file=sys.stderr)
            return 1
        svc = match[0]
        urls = collect_urls(args)
        if not urls:
            print("No URLs given.", file=sys.stderr)
            return 2
        ok, msg = svc.submit(urls, service_mod.service_arguments_with_keys(svc, keys))
        print(f"{svc.name}: {'OK' if ok else 'FAILED'} - {msg}")
        return 0 if ok else 1
    print(f"{len(svcs)} service definition(s) in {folder}\n")
    for s in svcs:
        flag = "on " if s.enabled else "off"
        print(f"  [{flag}] {s.name}")
        if s.desc:
            print(f"         {s.desc}")
        if s.costs:
            print(f"         cost: {s.costs}")
        if s.name in keys:
            stored = ", ".join(sorted(keys[s.name]))
            print(f"         key: {stored}")
    return 0


def cmd_proxies(args) -> int:
    if args.test_file:
        text = Path(args.test_file).read_text(encoding="utf-8", errors="replace")
        candidates = proxy_mod.parse_proxy_list(text)
    elif args.scrape:
        candidates = proxy_mod.download_proxy_lists()
    else:
        print("Use --scrape or --test-file.", file=sys.stderr)
        return 2
    print(f"Testing {len(candidates)} candidate proxies ...", flush=True)
    working = proxy_mod.find_working_proxies(
        candidates, limit=args.limit, threads=args.threads, timeout=args.timeout
    )
    print(f"Working proxies: {len(working)}")
    for p in working:
        print("  " + p.url)
    if args.save:
        path = Path(args.save)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(p.url for p in working) + "\n", encoding="utf-8")
        print(f"Saved to {path}")
    return 0 if working else 1


def cmd_emulate(args) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"No service folder at {folder}", file=sys.stderr)
        return 1
    cfg = emu_mod.build_config(
        folder,
        host=args.host,
        port=args.port,
        bind_all_interfaces=args.bind_all,
        with_auth=args.with_auth,
        login=args.login,
        password=args.password,
        api_key=args.api_key,
        verbose=args.verbose,
    )
    server = emu_mod.IndexServiceEmulator(cfg)
    try:
        host, port = server.start()
    except OSError as exc:
        print(f"Could not bind {args.host}:{args.port} - {exc}", file=sys.stderr)
        return 1
    print(f"Indexer Server here - listening on http://{host}:{port}")
    print(f"  simulating {len(cfg.services)} service definition(s) from {folder}")
    if cfg.with_auth:
        print("  authentication: ON (Authorization: Basic <base64> or Token <token>)")
    print("  press Ctrl+C to stop")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(chr(10) + "Stopping ...")
    finally:
        server.stop()
    return 0


def cmd_watch(args) -> int:
    if not args.file:
        print("Use -f/--file to say what to watch.", file=sys.stderr)
        return 2
    targets = default_targets(load_ping_sites())
    if args.sites:
        targets = targets[: args.sites]
    indexer = Indexer(Settings(threads=args.threads))
    watcher = UrlWatcher(args.file, poll_interval=args.interval)
    print(f"Watching {len(args.file)} path(s); {len(targets)} sites loaded.")
    watcher.watch(
        indexer,
        targets,
        on_urls=lambda urls: print(f"\n[{time.strftime('%H:%M:%S')}] {len(urls)} new URL(s) found"),
        max_iterations=args.polls,
    )
    return 0


def cmd_crawl(args) -> int:
    from .crawler import Crawler

    try:
        crawler = Crawler(
            args.seed,
            depth=args.depth,
            external=args.external,
            same_folder=args.same_folder,
            with_anchor_text=not args.no_anchors,
            max_pages=args.max_pages,
            proxy=args.proxy,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        stats = crawler.run()
    except KeyboardInterrupt:
        crawler.stop()
        print("\nInterrupted - keeping what was found so far.", file=sys.stderr)
        return 130

    if args.tsv:
        for u in stats.urls:
            print(f"{u.url}\t{u.anchor}\t{'dofollow' if u.dofollow else 'nofollow'}\tL{u.level}")
    else:
        for u in stats.urls:
            print(u.url)
    if args.out:
        Path(args.out).write_text(
            "\n".join(u.url for u in stats.urls) + ("\n" if stats.urls else ""),
            encoding="utf-8",
        )
    print(
        f"[crawl] {stats.pages_fetched} page(s), {len(stats.urls)} URL(s), "
        f"{len(stats.errors)} error(s)"
        + (f" - stopped early" if stats.stopped else "")
        + (f" - written to {args.out}" if args.out else ""),
        file=sys.stderr,
    )
    for err in stats.errors[:10]:
        print("  " + err, file=sys.stderr)
    return 0 if stats.urls or not stats.errors else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "submit": cmd_submit,
        "check": cmd_check,
        "sites": cmd_sites,
        "services": cmd_services,
        "proxies": cmd_proxies,
        "emulate": cmd_emulate,
        "watch": cmd_watch,
        "crawl": cmd_crawl,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
