<div align="center">

# 🧲 GSA SEO Indexer — Clone

**A from-scratch reimplementation — not a copy, but respected reverse engineering.**

A pure-Python clone (standard library, **zero dependencies**) of the mechanism behind
*GSA SEO Indexer v3.09*. It submits target URLs to thousands of statistics/whois/network-log
sites that are constantly crawled by Googlebot & Bingbot, so the target surfaces as a backlink
on high-authority domains.

The mechanism was recovered with **Ghidra + REA MCP** from the original binary. No GSA code,
binary, or resource is used — only knowledge of how it works.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Dependencies](https://img.shields.io/badge/Dependencies-0-success?style=flat-square)
![Status](https://img.shields.io/badge/Status-active-success?style=flat-square)
![Version](https://img.shields.io/badge/Version-1.0.0-blue?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey?style=flat-square)

[🚀 Getting Started](#-getting-started) · [📡 How It Works](#-how-it-works) · [✨ Features](#-features) · [📊 Measured Results](#-measured-results) · [⚖️ Ethics & Limits](#%EF%B8%8F-ethics--limits)

</div>

---

## 🎯 TL;DR

| | |
|---|---|
| **What it is** | A GSA SEO Indexer clone that submits URLs to third-party sites for indexing |
| **How it works** | Creates a "digital footprint" on frequently-crawled sites → crawlers follow the backlink → target gets indexed |
| **What it is not** | Not Google submission. Google never receives a request from this program |
| **Verified via** | Ghidra reverse engineering (11,257 functions analyzed, from the original binary) |
| **Tested with** | 1,032 real requests against target `http://example.com/` |

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- **No** `pip install` — everything uses the standard library

### Running the GUI

```bash
# Windows
start_gui.bat

# Or manually
python -m gsa_seo_indexer
```

The window mirrors the original layout: URL input, Start/Multiple/Stop buttons, `Q:` / `LpM:`
counters, results table, and dark mode.

### Running the CLI

```bash
# Submit a single URL
python -m gsa_seo_indexer.cli submit --url https://example.com/

# Submit a URL list with 20 threads, save a report
python -m gsa_seo_indexer.cli submit -f urls.txt --threads 20 --report run.csv

# Check whether a URL can be indexed
python -m gsa_seo_indexer.cli check --url https://example.com/

# Run the index-service emulator on port 8787
python -m gsa_seo_indexer.cli emulate --port 8787
```

> 📖 **[Full documentation (Indonesian) →](README-LANJUTAN.md)** — every command, flag, config.ini key, and GUI guide

---

## 📡 How It Works

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  target URL │ ──► │  thousands of stat   │ ──► │  Googlebot/Bingbot  │
│             │     │  & whois sites       │     │  finds the backlink │
└─────────────┘     └──────────────────────┘     └─────────────────────┘
                            ▲                              │
                            │                              ▼
                     dynamic pages               ┌─────────────────────┐
                     displaying the              │  target gets        │
                     target link                 │  indexed            │
                                                 └─────────────────────┘
```

GSA SEO Indexer is **not** search-engine submission. It works by creating backlinks on sites
that are already indexed and frequently crawled — a real **digital footprint**.

**Three submission paths** (recovered from the binary):

1. **Ping to statistics sites** (free) — 6,671 sites recovered from `pr_cache.dat`
2. **Paid index services** — 48 `.ini` definitions (original GSA format) with API keys
3. **Emulator** — local HTTP server at `127.0.0.1:8787` simulating paid services

---

## ✨ Features

<details>
<summary><b>🖱️ Click to see all GUI features</b></summary>

- **Original layout** — URL input, toolbar, `Q:` (queue) & `LpM:` (links per minute) counters
- **Dark mode** — full palette, persisted to `config.ini`
- **Filter bar** — dropdown (All/Backlinks/Listed/Echo/Failed/Skipped) + `Ctrl+F` search
- **Minimize to tray** — via `Shell_NotifyIconW` (no dependencies needed), auto-restore
- **Resume interrupted runs** — queue saved to `last_queue.txt`, offered on next launch
- **Target picker** — radio Legacy (6,671 sites) / Modern (18 endpoints) / Both
- **Custom mode** — limit to first N sites (maps the original `custom mode` config)
- **Deep links only** — only send to sites that accept in-page URLs
- **Crawl online** — seed URL → crawl → collect links into queue (TForm11 equivalent)
- **Import & preview** — edit URL list before running, dedupe, duplicate/invalid counts
- **Index-service manager** — 48 services, toggle enabled, set API keys, test, run all

</details>

<details>
<summary><b>⌨️ Click to see all CLI commands</b></summary>

```bash
submit      # submit URLs to target sites
check       # check whether a URL can be indexed
sites       # list ping sites (count/filter)
services    # manage paid index services
proxies     # scrape & test public proxies
emulate     # run the index-service emulator
watch       # watch a file & submit new URLs automatically
crawl       # crawl a site for new URLs
```

Key flags: `--threads`, `--proxy`, `--report`, `--modern`, `--batch`, `--fast`,
`--preflight`, `--delay`, `--use-proxy`, `--scrape-proxies`, `--depth`

</details>

**Core features:**

- ⚡ **Multithreading** — 50 threads by default (benchmark-driven: 4.5 → 14.5 req/s)
- 🧠 **Strict backlink detection** — echoed query strings, `mailto:`, relative links don't count
- 💀 **Dead-host cache** — failed hosts recorded, subsequent requests skipped (1.26× faster)
- 🌐 **DNS cache** — resolve once per host per run
- 🔌 **Proxy rotation** — 4 public sources (~80,000 candidates), 2-stage validation
- 📊 **Reports** — CSV & JSON, optional `Indexable` column
- 🎛️ **config.ini** — keys mirror the original GSA config; can read GSA's own `config.ini`
- 🔐 **Separate API keys** — `keys.json`, never written to reports

---

## 📊 Measured Results

Real-world tested against `http://example.com/` with strict backlink detection.

### Legacy List (6,671 GSA sites, 500-site sample)

| Metric | Value |
|---|---|
| Requests sent | 1,014 |
| HTTP 2xx/3xx | 453 (45%) |
| **Verified backlinks** | **3 (0.30%)** |
| Only "listed" | 206 |

### Modern List (18 endpoints, verified 2024–2026)

| Metric | Value |
|---|---|
| Requests sent | 18 |
| HTTP 2xx/3xx | 18 (100%) |
| **Verified backlinks** | **2 (11%)** |
| Only "listed" | 12 |

> 💡 **0.30% vs 11% — the modern list is ~37× more efficient per request**, with zero failures.
> Why? Most legacy sites just echo the `?url=` parameter without storing the link, and that list
> is 5+ years old (2019–2023) — many are dead or redirected.

---

## ⚖️ Ethics & Limits

- ✅ Targets **must** be your own. Indexing someone else's URL without permission is abuse.
- ✅ Default rate is conservative — use `--delay` if needed.
- ✅ No requests to Google/Bing — purely digital footprints, like the original program.
- ⚠️ Most of the legacy list is dead — it's a 2019–2023 snapshot, not a fresh list.
- ⚠️ Free public proxies proved **not viable** (2.9× slower, HTTP-ok drops 44 → 24).

---

## 🏗️ Project Structure

```
gsa_seo_indexer/
├── engine.py             core: submit, URL check, thread pool, reports
├── cli.py                command-line interface
├── gui.py                Tkinter interface
├── services.py           index-service reader (GSA INI format)
├── emulator.py           index-service simulator (port 8787)
├── proxies.py            proxy scraper + tester
├── crawler.py            site crawler for URLs
├── watcher.py            file watcher for new URLs
├── perf.py               DNS cache, host cache, connection pooling
├── data/
│   ├── ping_sites.txt    6,671 ping sites (2019–2023)
│   └── modern_targets.json  18 endpoints (2024–2026)
├── index_service/        48 INI definitions (original GSA format)
└── reports/              CSV/JSON output
```

---

## 🔬 Reverse Engineering

The original binary is packed with a custom packer that **masquerades as UPX** (fake `.UPX0/1/2`
section names), so `upx -d` refuses it. Recovery was done by:

1. **Dumping 163 MB of memory** from the running process (598 regions) — the package is unpacked in memory
2. **Reconstructing the PE image**, extracting the `CODE` section (3.6 MB)
3. **Ghidra + BinaryLoader** (`-loader-baseAddr 0x401000`) — 11,257 functions, strings at correct VAs
4. Key functions `FUN_00723338` (host+path pairs) & `FUN_005e1dac` (HTTP client)
5. The 6,671-site list was taken from GSA's own `pr_cache.dat`

> 📁 Ghidra scripts & dump evidence are in `reverse_engineering_evidence/`

---

## 🤝 Contributing

Pull requests are not accepted for now — this is a personal project for reverse-engineering
experimentation and learning SEO-tool architecture. Issues can be used for discussion or
bug reports.

---

## 📜 License

MIT — free to use, modify, and learn from. **But please read the Ethics section first.**

This program is not affiliated with GSA Online. "GSA SEO Indexer" is a trademark of its owner.
What is used here is only knowledge of the mechanism (recovered legally from a purchased binary),
not GSA's code, binaries, or resources.

---

<div align="center">

**Built by RIZAL DRAG © 2026**

Mechanism recovered with Ghidra + [REA MCP](https://github.com/zcode-chat/rea-mcp) —
11,257 functions analyzed, 598 memory regions dumped, 1,032 real requests tested.

</div>
