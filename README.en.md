# GSA SEO Indexer — reimplementation (from scratch)

This program is a **from-scratch reimplementation** of the mechanics of
*GSA SEO Indexer v3.09* installed on this machine
(`C:\Program Files (x86)\GSA SEO Indexer\SEO_Indexer.exe`).

No code, binary, or resource owned by GSA is used. Only **knowledge of how it
works** was taken, recovered through reverse engineering with Ghidra (via MCP
REA).

---

## 1. Mechanism discovered

GSA SEO Indexer works as follows:

> Send a target URL to thousands of third-party sites of the *statistics*,
> *whois*, and *network log* type. Those sites are crawled very often by
> Googlebot and Bingbot, so the target appears as a **backlink** on
> high-churn domains. The crawler following that backlink then discovers the
> target page.

This is not "submit to Google" — Google never receives a request from this
program. What happens is creating a **digital footprint** on sites already
indexed by search engines.

Recovered from the original binary:

| Finding | Value |
|---|---|
| Program version | GSA SEO Indexer v3.09 (DEMOVERSION) |
| Mechanism | submit URL to statistics/whois sites + paid index service |
| Submit format | `http://<host><path><url-target>` (target **without** URL-encoding, scheme kept) |
| Alternate format | `http://<host>/?url=<url-encoded>` |
| Ping site list | 6,671 sites, stored in `pr_cache.dat` (format: `URL\xFF<status>\r\n`) |
| Built-in stat hosts | `alexa.com`, `siteadvisor.com`, `traffic.alexa.com`, `grapher.compete.com`, `siteanalytics.compete.com`, `quantcast.com`, `api.thumbalizr.com`, `open.thumbshots.org`, `pagepeeker.com`, `majesticseo.com`, `yandex.ru/cycounter` |
| Thread count | 10 (default, `config.ini`) |
| Mode | *Statistics Only* / *Redirect Only* / *Custom* (custom mode number default 800) |
| Proxy | built-in proxy scraper, tested via `azenv.php` / `proxyjudge.us` |
| Index service | 48 INI definitions in `index_service/` (paid API) |
| Emulator | local HTTP server at `127.0.0.1:8787` simulating index service (class `TIndexServer` / `TIndexClient`) |

### How recovery was done

`SEO_Indexer.exe` is packed with a custom packer disguised as UPX (fake section
names `.UPX0/.UPX1/.UPX2`), so `upx -d` refuses to open it. The solution:

1. Dump 163 MB of the running process memory (598 regions) — fully unpacked
   package in memory.
2. Reconstruct a PE image from that memory, then extract **only section `CODE`**
   (3.6 MB).
3. Analyze `CODE` section with Ghidra using **BinaryLoader**
   (`-loader BinaryLoader -loader-baseAddr 0x401000 -processor x86:LE:32:default`).
   Ghidra's built-in PE loader maps sections with wrong offsets on the
   reconstructed PE; BinaryLoader maps 1:1, so decompilation is accurate
   (11,257 functions, strings at correct VAs).
4. From decompilation comes function `FUN_00723338` which loads *host + path*
   pairs (`/siteinfo/`, `/sites/`, `/charts/backlinks-discovery`, `/graph`)
   plus flag `[Statistics Only]`, plus HTTP client function `FUN_005e1dac`
   that builds the request.
5. The 6,671-site list was taken from GSA's own `pr_cache.dat`.

> Note: MCP REA config file `~/.zcode/cli/config.json` already has env
> `GHIDRA_INSTALL_DIR` and `JAVA_HOME` added so the Ghidra provider can be used
> (backup at `config.json.bak-gsa`).

---

## 2. Program structure

```
C:\tools\GSA INDEXER\
├── gsa_seo_indexer/
│   ├── __init__.py
│   ├── __main__.py          python -m gsa_seo_indexer  -> GUI
│   ├── engine.py             core: submit, check URL, thread pool, report
│   ├── cli.py                command-line interface
│   ├── gui.py                Tkinter interface
│   ├── services.py           reader of index service definitions (GSA INI format)
│   ├── emulator.py           local server simulating index service
│   ├── proxies.py            proxy scraper + tester
│   ├── crawler.py            crawl sites for URLs (padanan TForm11 "Crawl for URLs")
│   ├── watcher.py            file watcher for new URLs
│   ├── data/
│   │   ├── ping_sites.txt    6,671 recovered ping sites (2019-2023)
│   │   └── modern_targets.json  18 verified modern endpoints (2024-2026)
│   ├── index_service/        47 index service definitions (GSA INI format)
│   └── reports/              CSV/JSON output
├── reverse_engineering_evidence/   evidence: Ghidra scripts, decompile, dump
├── start_gui.bat / start_cli.bat / run_demo.bat
└── README.md
```

Requirements: **Python 3.10+**, no third-party dependencies.

---

## 3. Usage

### GUI

```
start_gui.bat
```

or

```
python -m gsa_seo_indexer
```

The window mirrors the original program layout: URL/Domain input field +
Start / Multiple / Stop buttons, thread options, site filter, and `Q:` /
`LpM:` counters at bottom right.

**Help → About…** shows app name, version, and developer credit
(RIZAL DRAG); CLI equivalent is `--version`:
```
GSA SEO Indexer Clone v1.0.0
Developed by RIZAL DRAG © 2026
```

Result table uses columns **Target URL | Site | Link | Code | ms**:

| Column | Content |
|---|---|
| Target URL | URL you submitted (`Result.target`) |
| Site | target host being pinged |
| Link | `✓ indexed` (real anchor to target), `● listed` (appears on result page), `○ echo` (HTTP 200 only, no link), or error message |
| Code | HTTP status code |
| ms | response time |

Row colors follow link type (green = indexed, dark yellow = listed,
grey = echo/skipped, red = failed). The **Backlinks:** counter at bottom
right counts `indexed` + `listed` rows — that's the real result metric,
not just HTTP 200.

Before a run starts every URL is validated (`validate_url`); incomplete URLs
(e.g. without `http://`) are rejected up front with a reason, not sent to
thousands of sites and silently failing. Button **Start** is disabled during
a run and **Stop** is active; shortcuts: `Ctrl+O` open URL list, `Ctrl+S`
save report, `Esc` stop.

Progress bar is computed from real engine totals (one request per host when
*Fast* mode is active, including repair pass additions), not from
`number of URLs × number of targets` which doubles legacy hosts and made
the bar stick at 50%.

#### Target list picker

Group **Sources** has radio **Targets: Legacy / Modern / Both**:

| Option | Target | Note |
|---|---|---|
| Legacy | 6,671 sites (13,356 URL forms) | recovered list from original program (2019-2023) |
| Modern | 18 verified endpoints | real backlink yield ~37× higher per request |
| Both | combination | equivalent to CLI `--modern` without `--no-legacy` |

#### Custom mode

Checkbox **Custom** + number box in Run group maps the original program's
*custom mode* (config keys `custom mode` + `custom mode number`, default
**800**): when active, only the **first N ping sites** of the list are used.
Cap is at *site* level — each site still produces two submit forms, so 800
sites ≈ 1,600 targets — unlike *max sites* in Advanced settings which cuts
the target list arbitrarily. *Statistics only* takes priority if both are
active. CLI equivalent: `submit --custom --custom-number 500`.

#### Deep link indexer only

Checkbox **Deep links only** in the Sources filter bar maps original config
key `deep link indexer only`: send **only to sites that index deep links**
(in-page URLs like `example.com/blog/post`), not just root domains — useful
for indexing SER backlinks which are almost always in-page URLs. The recovered
legacy ping list carries no per-site capability flag (that metadata is not in
the binary), so mapping is: when active, the legacy list is **skipped** and
only the verified set is used — 14 authority stat services (full-URL submit
forms from decompilation) plus verified modern list. CLI: `submit --deep-links`.

#### config.ini

All settings — threads, proxy, target choice, timeout, batch, filter — are
saved to `gsa_seo_indexer\config.ini` and re-read on next launch. The file is
**shared by GUI and CLI**: values not passed as CLI flags take the config
value, and passed flags always win. Key names mirror the original GSA config
(`threads`, `fast mode`, `check url`, `use proxy`, `report format csv`,
`site filter`, …) so the original program's `config.ini` can also be read
directly.

Deeper engine settings (timeout, connect/read timeout, delay, max sites,
preflight, redirect, skip-dead-host, random delay, connection pool, backlink
column in report) are in **Options → Advanced settings…** and also saved to
config.ini. Two extra keys: `persist host cache` (writes dead hosts to
`gsa_seo_indexer\host_cache.json`, default on) and `dns cache` (resolve DNS
once per host per run, default on).

#### Indexing services

**Options → Indexing services…** is now a full manager (formerly just a text
dump): list of 48 service definitions, **Toggle enabled** (rewrites a single
`enabled=` line in the INI file without destroying comments), **Set key…**
(saves service credentials), **Test** (sends URL from main window to selected
service), **Run all enabled** (submits to all enabled services; results enter
main table and report), and **Edit .ini…** (opens definition in default
editor).

The **Key** column shows which services have stored credentials. Without a
key, the request goes out with literal `%API-Key%` in the body — no real
service accepts it (so far only the emulator makes this path work). Keys are
stored in `gsa_seo_indexer\keys.json`, separate from `config.ini`, and never
written to reports:

```json
{ "Instant Link Indexer": { "API-Key": "your-key" } }
```

CLI equivalent: `services --set-key "Instant Link Indexer" your-key`
(option `--key-var` for variables other than `API-Key`).

#### Import & preview

**Multiple** or `Ctrl+O` opens a preview dialog: parsed URL list can be
edited before running, with duplicate and invalid counts, plus a **Dedupe**
button. **File → Import from clipboard** (`Ctrl+Shift+V`) pastes a URL list
directly from clipboard into the same preview.

#### Crawl Online

Button **Crawl online...** in the preview dialog (equivalent to original
TForm2 → TForm11 *Crawl Online* button) opens *Crawl for URLs* dialog: give a
seed URL, set **Levels deep**, then check *With anchor text* / *External
sites* / *Same folder* / *Use proxies* — controls recovered exactly from
TForm11. Results appear in URL / Anchor / DoFollow / Level table for review,
then **Add URLs to queue** adds them to the preview (seed URL itself is not
included; external links are collected but never crawled). CLI equivalent:

```bash
python -m gsa_seo_indexer.cli crawl https://example.com/blog/ --depth 2 --out urls.txt
python -m gsa_seo_indexer.cli crawl https://example.com/ --same-folder --tsv
```

#### Filter & search results

Bar above result table: **Show** dropdown (All / Backlinks / Listed / Echo /
Failed / Skipped) + free-text search box (matches Target, Site, or Link;
`Ctrl+F` focuses the search box). Non-matching rows are detached, not
deleted — change filter and everything comes back. Counter `n/m rows` at
right shows how many are visible.

#### Dark mode & font

**Options → Dark mode** switches the full palette (window, frame, table,
entry; link tag colors adjusted to stay readable), saved to config.ini, and
applies on next launch too. All text uses Segoe UI 10 with taller table
rows.

#### Minimize to tray

**Options → Minimize to tray** (original config key `min to tray`): when
active, minimizing the window hides it to the notification area instead of
the taskbar. Left-click tray icon restores the window; right-click gives
Restore/Quit menu. Tray icon is created directly via `Shell_NotifyIconW`
(ctypes/Win32) — still **no third-party dependencies** — and automatically
reappears if Explorer restarts. Outside Windows this option is safely ignored
(normal minimize).

#### Resume interrupted run

Each run writes its URL list to `gsa_seo_indexer\last_queue.txt` (same
mechanism as original program). A cleanly finished run deletes it; a run
Stopped or crashed leaves it — and on next launch an offer to reopen those
URLs in preview appears. Dead-host cache also persists **across processes**:
dead hosts are written to `gsa_seo_indexer\host_cache.json` at the end of
each run, and the next run skips them without paying the connect timeout
again (until TTL 30 minutes expires). In addition, DNS resolution is not
duplicated per host per run (`dns cache`), because both submit forms of one
site share the same host and are often in-flight simultaneously.

### CLI

All `submit` flags not passed take their value from
`gsa_seo_indexer\config.ini` (same file as GUI), then from defaults.

```bash
# submit one URL
python -m gsa_seo_indexer.cli submit --url https://example.com/

# submit URL list, 20 threads, save report
python -m gsa_seo_indexer.cli submit -f urls.txt --threads 20 --report run.csv

# limit number of sites, statistics sites only
python -m gsa_seo_indexer.cli submit -f urls.txt --sites 200 --stats-only

# original program custom mode: only first 500 ping sites
python -m gsa_seo_indexer.cli submit -f urls.txt --custom --custom-number 500

# send several URLs per request
python -m gsa_seo_indexer.cli submit -f urls.txt --batch 10

# via proxy
python -m gsa_seo_indexer.cli submit -f urls.txt --use-proxy --proxy 1.2.3.4:8080

# check whether URL can be indexed
python -m gsa_seo_indexer.cli check --url https://example.com/

# list ping sites
python -m gsa_seo_indexer.cli sites --count
python -m gsa_seo_indexer.cli sites --filter .ru

# modern list only (2024-2026, not old GSA list)
python -m gsa_seo_indexer.cli submit -f urls.txt --modern --no-legacy

# paid index service (GSA INI format)
python -m gsa_seo_indexer.cli services --list
python -m gsa_seo_indexer.cli services --test "BulkAddUrl.com" --url https://example.com/

# run local index-service emulator (like GSA itself)
python -m gsa_seo_indexer.cli emulate --port 8787
python -m gsa_seo_indexer.cli emulate --with-auth --login me --password secret

# proxy scraper
python -m gsa_seo_indexer.cli proxies --scrape --limit 10 --save proxies.txt

# watch a log file, submit new URLs instantly
python -m gsa_seo_indexer.cli watch -f backlinks.log --sites 50
```

---

## 4. How the engine works

`engine.Indexer` receives a URL list and target list, then:

1. **Check URL** (can be disabled with `--no-check`): `GET` request, read
   response; URL rejected if HTTP ≥ 400 or page contains `noindex`.
2. **Submit** to each target according to `mode`:
   - `append` → `http://host/path` + `http://target/` (no encoding)
   - `query` → `http://host/?url=` + URL-encoded target
   - `post` → target sent as body
3. **Backlink detection**: response scanned for `<a href="...target...">`.
   Plain echo parameters are **not** counted as backlinks.
4. **Report** CSV or JSON, optionally with `Indexable` column.

Threads are created via `ThreadPoolExecutor`; thread count, timeout, delay,
proxy, and site filter are all set via `Settings` (can be saved to
`config.ini` in the same format as GSA).

---

## 5. Measurement results

Tested against `http://example.com/` (standard test URL). Both lists tested
with strict backlink detection.

### A. Legacy list (6,671 GSA sites, 500-site sample)

| Metric | Value |
|---|---|
| Attempts | 1,014 requests |
| HTTP 2xx/3xx responses | 453 (45%) |
| **Verified backlinks** | **3 (0.30%)** |
| Only "listed" (appears on page, not a link) | 206 |

Three verified backlinks, each manually re-checked:

```
https://anolink.com/http://example.com/
http://jump.x0.to/http://example.com/
http://m-husc.a17.csinet.es/?url=http%3A%2F%2Fexample.com%2F
```

### B. Modern list (18 endpoints, verified 2024-2026)

| Metric | Value |
|---|---|
| Attempts | 18 requests |
| HTTP 2xx/3xx responses | 18 (100%) |
| **Verified backlinks** | **2 (11%)** |
| Only "listed" | 12 |

```
https://search.naver.com/search.naver?query=...   (link to target)
https://search.seznam.cz/?q=...                    (link to target)
```

**Comparison: 0.30% vs 11% backlinks per request — modern list ~37× higher
yield**, and no failed requests.

### Why legacy yield is so small

Most sites on the GSA list just echo the `?url=` parameter on the front page
without ever storing the link. Only a small minority actually write the URL
to their statistics page. The list is also 5+ years old (2019-2023), so many
are dead or redirected.

The program's detector is intentionally strict — it only counts anchors that
really point to the target host, and ignores:

* echo parameters (`/?url=...` pointing to the page itself)
* `mailto:` and relative links
* Google/Bing search result pages (not backlink sources)

Results that only "appear on the page" are reported separately as **Listed**,
so they don't get mixed with genuine backlinks.

---

## 6. Index service emulator (port 8787)

GSA can run a local HTTP server that **simulates** paid index services, so
other GSA tools can submit without paying. This behavior was recovered from
decompilation of `TIndexServer` / `TIndexClient`:

* Server listens on *index emulation host* / *own port* (default
  `127.0.0.1:8787`).
* Original program writes to `drivers\etc\hosts` so real service domains
  point to the local server — requests arrive with the original domain `Host`
  header, which is used to match the service.
* Each service is answered per its INI: `simulate_result` for success,
  `simulate_error` for failure, `%url_count%` replaced with URL count.
* `[error_message] front=` / `back=` frame the error message.
* With `with auth on` the server requires `Authorization: Basic <base64>`
  or `Authorization: Token <token>`, and answers 401 on mismatch.
* Root response: `<html><head><title>Indexer Server here</title></head>...`

Clone implementation: `gsa_seo_indexer/emulator.py` + `emulate` subcommand.

```bash
python -m gsa_seo_indexer.cli emulate --port 8787
python -m gsa_seo_indexer.cli emulate --with-auth --login me --password secret
```

In GUI: **Options → Start index-service emulator...**

Live testing against 3 services produced correct responses per each
`simulate_result`:

```
Honest Indexer  -> {"success": true,"inserted":2,"remaining_credits":999}
2Index.Ninja    -> {"success": true,"errors": ["You sent invalid links"],...}
BulkAddUrl      -> {"taskId":925856,"taskCountUrls":999,...}
```

---

## 7. Optimization

### Where time is spent

A profile run was done first (300 sites, 25 threads, 10-second timeout)
before writing any optimizations. Time within a single request breaks down:

| Phase | Share | Note |
|---|---|---|
| read (waiting for server response) | **55%** | server latency, not transfer |
| TLS handshake | 20% | repeated per request |
| TCP connect | 14% | repeated per request |
| DNS resolve | 11% | cached by OS after first lookup |

Three consequences:

1. The most expensive thing is not the request, but **dead hosts** — one dead
   host burns one thread slot for the full timeout.
2. TCP + TLS = 34% of time, and both are repeated for every request.
3. Page content regex scan is **not** a problem (0 ms per 300 KB page).

DNS is also expensive when cold: 223 ms per host, 0.01 ms after OS cache
warms. Parallel probe of 300 hosts: **6 seconds** (19 ms/host effective).

### Implemented optimizations

| Optimization | How it works | Measured effect | Default |
|---|---|---|---|
| **5-second timeout** | 42% of successful requests need >3s and consume 62% of time; capping at 10s only adds timeout | **1.5× faster**, HTTP-ok/backlink/listed identical | yes |
| **50 threads** | Throughput measured: peak at 50 threads, drops at 100 | 4.5 → 14.5 req/s | yes |
| **Dead-host cache** | Failed hosts are recorded; subsequent requests to that host are skipped without a request | 1.26× faster | yes |
| **Separate timeouts** | connect timeout (4s) and read timeout (5s) separate | dead host doesn't hold thread | yes |
| **URL validation** | malformed URLs rejected before run, not after 13,000 requests | full savings | yes |
| **Strict backlink detection** | echo query strings no longer counted as "listed" | honest numbers | yes |
| **Host dedupe** | old list has 2 URL forms per site on same host | 25% fewer requests | optional (`--fast`) |
| **Pre-flight liveness** | parallel TCP probe to all hosts, submit only to live ones | only effective if most hosts are dead | optional (`--preflight`) |
| **Batch multi-URL** | 10 URLs per request per site, not 1 | **4.5× faster, but backlinks lost** | optional (`--batch N`) |
| **Connection pooling** | TCP/TLS reused across requests to same host | 2.6× faster **per host**, ~10% slower on wide runs | optional (`--pool`) |

### Batch: fast but loses links

Batch multi-URL is the biggest optimization and at the same time the most
dangerous. Tested with 10 URLs × 414 targets:

Final benchmark — 150 sites → 314 targets, 5 URLs, identical settings:

| Strategy | Time | HTTP-ok | Backlink | Listed |
|---|---|---|---|---|
| **A initial settings** (10 threads, 12s timeout, no cache) | 337.6 s | 751 | **5** | 50 |
| **B current settings** (50 threads, 5s timeout, dead-host cache) | **91.5 s** | 745 | **5** | 50 |
| C + batch (opt-in) | 20.6 s | 740 | 0 | 55 |

**B gives 3.7× speedup with identical results** — HTTP-ok, backlink, and
listed exactly the same as A. C is even faster but loses all backlinks, so
not made default.

Batch clearly wins: **2.2× faster** without sacrificing listed (18 vs 15 —
just went up, because one request carries multiple URLs).

But batch has a risk: a site usually only stores the first URL from a batch,
so **backlinks can be lost**. In a run with 10 URLs, backlinks dropped from
10 to 0. That's why `run_batched()` adds a "repair" pass — URLs not
captured are re-sent one by one.

Batch itself once fooled detection: sites echoing the query string on the
front page were counted "listed", so repair never ran. After query-echo was
no longer counted as listing, numbers became consistent — but **batch still
loses backlinks**, so **batch is not default**; used via `--batch N --repair`.

### Optimization commands

```bash
# one request per host, retry second URL form only if needed (default GUI)
python -m gsa_seo_indexer.cli submit -f urls.txt --fast

# batch 10 URLs per request + repair
python -m gsa_seo_indexer.cli submit -f urls.txt --batch 10 --repair

# disable pre-flight (starts faster, more requests wasted)
python -m gsa_seo_indexer.cli submit -f urls.txt --no-preflight

# separate timeouts
python -m gsa_seo_indexer.cli submit -f urls.txt --connect-timeout 4 --read-timeout 8
```

GUI has a **Batch** column, check **Fast (1 req/host)** and **Repair misses**.

---

## 8. Proxy

Original GSA program has a built-in proxy scraper and uses proxies to hide
traffic origin. This clone has the same implementation
(`gsa_seo_indexer/proxies.py`):

```bash
# scrape public proxies, validate against real targets, then use
python -m gsa_seo_indexer.cli submit -f urls.txt --use-proxy --scrape-proxies 20

# use your own proxy list
python -m gsa_seo_indexer.cli submit -f urls.txt --use-proxy --proxy-file proxies.txt
```

Four public proxy list sources are used (~80,000 candidates per scrape), and
proxies that pass are tested **twice**: once for general connectivity, then
against the actual target URL.

### Measurement results: free public proxies are not worth it

| Metric | Direct | Via proxy |
|---|---|---|
| Time (94 requests) | 20.1 s | 57.3 s |
| HTTP-ok | 44 | 24 |
| Listed | 7 | 2 |

**2.9× slower and produces fewer backlinks**, even after proxies were
validated against real targets.

Root causes:

* Only **6–14%** of public proxies can actually reach target HTTPS (of 250
  candidates: 35 to gstatic, 15 to seznam).
* Public proxies die within **1–2 requests** — one 5-second timeout is enough
  to kill one.
* Latency via proxy is much higher: 6–10 seconds per request vs 276 ms direct.

So the proxy path is **not default** — it exists for when your own IP is
blocked, not for speed. Proxy rotation is built: failed proxies are dropped,
and the pool is reset once all fail (most failures are temporary).

Two traps found and tested:

* **Proxy failure ≠ target host dead.** The first version marked the target
  host dead on proxy timeout, then skipped it for the rest of the run. Now
  only failures coming from the target itself mark the host dead.
* SSL context must be installed on the **handler**, not via kwarg
  `context=` in `open()` — otherwise the proxy tunnel is never used and every
  proxy is considered failed.

---

## 9. Ethics notes and limitations

* This program sends automatic requests to thousands of sites. Default rate
  is intentionally conservative; use `--delay` if needed.
* Most sites on the list are dead or redirected — this is a list recovered
  from GSA v3.09 (circa 2019–2023), not a fresh list.
* Target should be `example.com` / your own URL. Indexing someone else's URL
  without permission is abuse.
* There are no submission requests to Google/Bing — this mechanism purely
  creates a digital footprint, just like the original program. Option
  `--with-search-engines` only adds search queries, not backlink submission.

---

## 10. Helper commands used

```bash
# Ghidra headless decompile
analyzeHeadless <proj> <name> -import code_section.bin \
  -loader BinaryLoader -loader-baseAddr 0x401000 -processor x86:LE:32:default \
  -scriptPath ./ghidra_scripts -postScript Final.java

# REA CLI
rea analyze <path> --provider ghidra
rea decompile <path> --procedure=0x723338 --provider ghidra
```
