<div align="center">

# 🧲 GSA SEO Indexer — Clone

**Reimplementasi dari nol — bukan copy-paste, tapi rekayasa balik yang dihormati.**

Klon Python murni (standard library, **zero dependency**) dari mekanisme *GSA SEO Indexer v3.09*.
Program ini mengirim URL target ke ribuan situs statistik/whois/network-log yang rajin di-crawler
Googlebot & Bingbot, sehingga target muncul sebagai backlink di domain ber-otoritas tinggi.

Mekanisme dipulihkan dengan **Ghidra + REA MCP** dari binary asli. Tidak ada kode, binary, atau
resource milik GSA yang dipakai — hanya pengetahuan tentang cara kerjanya.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Dependencies](https://img.shields.io/badge/Dependencies-0-success?style=flat-square)
![Status](https://img.shields.io/badge/Status-active-success?style=flat-square)
![Version](https://img.shields.io/badge/Version-1.0.0-blue?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey?style=flat-square)

[🚀 Mulai](#-mulai) · [📡 Cara Kerja](#-cara-kerja) · [✨ Fitur](#-fitur) · [📊 Hasil Ukur](#-hasil-ukur) · [⚖️ Etika](#%EF%B8%8F-etika--batasan)

</div>

---

## 🎯 TL;DR

| | |
|---|---|
| **Apa ini** | Klon GSA SEO Indexer yang men-submit URL ke situs pihak ketiga agar diindeks |
| **Cara kerja** | Membuat "jejak digital" di situs ber-frequency crawl tinggi → crawler menemukan backlink → target terindeks |
| **Bukan** | Bukan kirim ke Google. Google tidak pernah menerima request dari program ini |
| **Diverifikasi via** | Ghidra reverse engineering (11.257 fungsi dianalisa, dari binary asli) |
| **Diuji dengan** | 1.032 request nyata ke target `http://example.com/` |

---

## 🚀 Mulai

### Prasyarat

- Python 3.10+
- **Tidak ada** `pip install` — semua memakai standard library

### Menjalankan GUI

```bash
# Windows
start_gui.bat

# Atau manual
python -m gsa_seo_indexer
```

Jendela meniru layout asli: input URL/Domain, tombol Start/Multiple/Stop, counter `Q:` / `LpM:`,
tabel hasil, dan dark mode.

### Menjalankan CLI

```bash
# Submit satu URL
python -m gsa_seo_indexer.cli submit --url https://example.com/

# Submit daftar URL dengan 20 thread, simpan laporan
python -m gsa_seo_indexer.cli submit -f urls.txt --threads 20 --report run.csv

# Cek apakah URL bisa diindeks
python -m gsa_seo_indexer.cli check --url https://example.com/

# Jalankan emulator index service di port 8787
python -m gsa_seo_indexer.cli emulate --port 8787
```

> 📖 **[Dokumentasi lengkap CLI →](README-LANJUTAN.md)** — semua command, flag, config.ini, dan panduan GUI

---

## 📡 Cara Kerja

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  URL target │ ──► │  ribuan situs        │ ──► │  Googlebot/Bingbot  │
│  Anda       │     │  statistik & whois   │     │  menemukan backlink │
└─────────────┘     └──────────────────────┘     └─────────────────────┘
                            ▲                              │
                            │                              ▼
                     halaman dinamis          ┌─────────────────────┐
                     yang menampilkan        │  target terindeks   │
                     link target             │  di search engine   │
                                              └─────────────────────┘
```

GSA SEO Indexer **bukan** search engine submission. Ia bekerja dengan membuat backlink pada
situs-situs yang sudah terindeks dan sering dirayapi — **digital footprint** dalam bentuk nyata.

**Tiga jalur submission** (dipulihkan dari binary):

1. **Ping ke situs statistik** (gratis) — 6.671 situs hasil recovery dari `pr_cache.dat`
2. **Index service berbayar** — 48 definisi `.ini` (format GSA asli) dengan API key
3. **Emulator** — server HTTP lokal di `127.0.0.1:8787` yang menyimulasikan service berbayar

---

## ✨ Fitur

<details>
<summary><b>🖱️ Klik untuk melihat semua fitur GUI</b></summary>

- **Layout asli** — input URL, toolbar, counter `Q:` (queue) & `LpM:` (links per minute)
- **Dark mode** — palet lengkap, tersimpan di `config.ini`
- **Filter bar** — dropdown (All/Backlinks/Listed/Echo/Failed/Skipped) + pencarian `Ctrl+F`
- **Minimize to tray** — via `Shell_NotifyIconW` (tidak butuh dependensi), restore otomatis
- **Resume run terputus** — antrian disimpan ke `last_queue.txt`, ditawarkan saat launch
- **Pemilih target** — radio Legacy (6.671 situs) / Modern (18 endpoint) / Both
- **Custom mode** — batasi ke N situs pertama (mapping `custom mode` config asli)
- **Deep links only** — hanya kirim ke situs yang menerima URL halaman dalam
- **Crawl online** — seed URL → crawl → kumpulkan link ke antrian (padanan TForm11)
- **Import & preview** — edit daftar URL sebelum run, dedupe, hitung duplikat/invalid
- **Manajer index service** — 48 layanan, toggle enabled, set API key, test, run all

</details>

<details>
<summary><b>⌨️ Klik untuk melihat semua command CLI</b></summary>

```bash
submit      # submit URL ke situs target
check       # cek apakah URL bisa diindeks
sites       # daftar situs ping (count/filter)
services    # kelola index service berbayar
proxies     # scrape & uji proxy publik
emulate     # jalankan emulator index service
watch       # pantau file & submit URL baru otomatis
crawl       # crawl situs untuk URL baru
```

Flag penting: `--threads`, `--proxy`, `--report`, `--modern`, `--batch`, `--fast`,
`--preflight`, `--delay`, `--use-proxy`, `--scrape-proxies`, `--depth`

</details>

**Fitur utama:**

- ⚡ **Multithreading** — 50 thread default (dipukul habis benchmark: 4.5 → 14.5 req/s)
- 🧠 **Deteksi backlink ketat** — echo query string, `mailto:`, link relatif tidak dihitung
- 💀 **Cache host mati** — host gagal dicatat, request berikutnya dilewatkan (1.26× lebih cepat)
- 🌐 **DNS cache** — resolve sekali per host per run
- 🔌 **Proxy rotation** — 4 sumber publik (~80.000 kandidat), validasi 2 tahap
- 📊 **Laporan** — CSV & JSON, opsional dengan kolom `Indexable`
- 🎛️ **config.ini** — key meniru config asli GSA, bisa baca `config.ini` GSA langsung
- 🔐 **API key terpisah** — `keys.json`, tidak pernah ditulis ke laporan

---

## 📊 Hasil Ukur

Diuji nyata terhadap `http://example.com/` dengan deteksi backlink ketat.

### Daftar Legacy (6.671 situs GSA, sampel 500)

| Metrik | Nilai |
|---|---|
| Request dikirim | 1.014 |
| HTTP 2xx/3xx | 453 (45%) |
| **Backlink terverifikasi** | **3 (0,30%)** |
| Hanya "listed" | 206 |

### Daftar Modern (18 endpoint, terverifikasi 2024–2026)

| Metrik | Nilai |
|---|---|
| Request dikirim | 18 |
| HTTP 2xx/3xx | 18 (100%) |
| **Backlink terverifikasi** | **2 (11%)** |
| Hanya "listed" | 12 |

> 💡 **0,30% vs 11% — daftar modern ~37× lebih efisien per request**, dan tidak ada request gagal.
> Kenapa? Mayoritas situs legacy hanya meng-echo parameter `?url=` tanpa menyimpan link, dan
> daftar itu berumur 5+ tahun (2019–2023) — banyak yang mati atau redirect.

---

## ⚖️ Etika & Batasan

- ✅ Target **harus** milik Anda sendiri. Mengindeks URL orang lain tanpa izin adalah penyalahgunaan.
- ✅ Rate default konservatif — gunakan `--delay` bila perlu.
- ✅ Tidak ada request ke Google/Bing — murni digital footprint, sama seperti program aslinya.
- ⚠️ Daftar legacy sebagian besar sudah mati — ini snapshot 2019–2023, bukan daftar fresh.
- ⚠️ Proxy publik gratis terbukti **tidak layak** (2,9× lebih lambat, HTTP-ok turun 44→24).

---

## 🏗️ Struktur Proyek

```
gsa_seo_indexer/
├── engine.py             inti: submit, check URL, thread pool, report
├── cli.py                antarmuka command line
├── gui.py                antarmuka Tkinter
├── services.py           pembaca index service (format INI GSA)
├── emulator.py           simulator index service (port 8787)
├── proxies.py            proxy scraper + tester
├── crawler.py            crawl situs untuk URL
├── watcher.py            pemantau file untuk URL baru
├── perf.py               DNS cache, host cache, connection pooling
├── data/
│   ├── ping_sites.txt    6.671 situs ping (2019–2023)
│   └── modern_targets.json  18 endpoint (2024–2026)
├── index_service/        48 definisi INI (format GSA asli)
└── reports/              output CSV/JSON
```

---

## 🔬 Reverse Engineering

Binary asli dipaket dengan packer kustom yang **menyamar sebagai UPX** (section `.UPX0/1/2`
dipalsukan), sehingga `upx -d` menolak membukanya. Recovery dilakukan dengan:

1. **Dump 163 MB memory** proses yang sedang berjalan (598 region) — package ter-unpack di memory
2. **Rekonstruksi image PE**, ekstrak section `CODE` (3,6 MB)
3. **Ghidra + BinaryLoader** (`-loader-baseAddr 0x401000`) — 11.257 fungsi, string pada VA benar
4. Fungsi kunci `FUN_00723338` (host+path pairs) & `FUN_005e1dac` (HTTP client)
5. Daftar 6.671 situs diambil dari `pr_cache.dat` milik GSA sendiri

> 📁 Bukti script Ghidra & dump ada di folder `reverse_engineering_evidence/`

---

## 🤝 Kontribusi

Pull request tidak diterima untuk sementara — ini project personal untuk eksperimen
reverse engineering & pembelajaran arsitektur tool SEO. Issues dapat digunakan untuk
diskusi atau pelaporan bug.

---

## 📜 Lisensi

MIT — bebas dipakai, dimodifikasi, dan dipelajari. **Tapi tolong baca bagian Etika dulu.**

Program ini tidak berafiliasi dengan GSA Online. "GSA SEO Indexer" adalah merek dagang
pemiliknya. Yang dipakai di sini hanyalah pengetahuan meknisme (yang dipulihkan secara legal
dari binary yang sudah dibeli), bukan kode/binary/resource milik GSA.

---

<div align="center">

**Dibuat oleh RIZAL DRAG © 2026**

Mekanisme dipulihkan dengan Ghidra + [REA MCP](https://github.com/zcode-chat/rea-mcp) —
11.257 fungsi dianalisa, 598 region memory di-dump, 1.032 request nyata diuji.

</div>
