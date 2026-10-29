# 🇮🇩 GSA SEO Indexer — Dokumentasi Lengkap

> Halaman ini adalah dokumentasi lengkap (Bahasa Indonesia). Untuk ringkasan, lihat [README](README.md).

---

## Daftar Isi

- [Install](#install)
- [GUI](#gui)
  - [Pemilih daftar target](#pemilih-daftar-target)
  - [Custom mode](#custom-mode)
  - [Deep link indexer only](#deep-link-indexer-only)
  - [config.ini](#configini)
  - [Indexing services](#indexing-services)
  - [Import & preview](#import--preview)
  - [Crawl Online](#crawl-online)
  - [Filter & pencarian hasil](#filter--pencarian-hasil)
  - [Dark mode & font](#dark-mode--font)
  - [Minimize to tray](#minimize-to-tray)
  - [Lanjutkan run yang terputus](#lanjutkan-run-yang-terputus)
- [CLI](#cli)
- [Cara kerja engine](#cara-kerja-engine)
- [Emulator index service](#emulator-index-service-port-8787)
- [Optimasi](#optimasi)
- [Proxy](#proxy)
- [Mekanisme & recovery binary asli](#mekanisme--recovery-binary-asli)

---

## Install

 Kebutuhan: **Python 3.10+**, tanpa dependensi pihak ketiga.

**Instalasi via pip (CLI global) — langsung dari GitHub:**

```bash
pip install git+https://github.com/njutawan/GSA-SEO-Indexer.git
gsa-seo-indexer --version
gsa-seo-indexer submit --url https://example.com/
```

**Atau jalan dari source:**

```bash
git clone https://github.com/njutawan/GSA-SEO-Indexer.git
cd GSA-SEO-Indexer
python -m gsa_seo_indexer        # GUI
```

Launcher Windows: `start_gui.bat` (GUI), `start_cli.bat` (CLI), `run_demo.bat` (demo).

> 💾 Saat di-install via pip, state (`config.ini`, `keys.json`, `host_cache.json`, `reports/`)
> disimpan di folder user (Windows: `%APPDATA%\GSA-SEO-Indexer`) — bukan di site-packages,
> jadi aman saat upgrade. Data situs ping & index service tetap dibaca dari dalam package.

---

## GUI

### Pemilih daftar target

Grup **Sources** punya radio **Targets: Legacy / Modern / Both**:

| Pilihan | Target | Catatan |
|---|---|---|
| Legacy | 11.661 situs (23.336 bentuk URL) | recovery `pr_cache.dat` + harvest list publik (2026-10-25), 100% HTTP-live |
| Modern | 107 endpoint (18 terverifikasi ketat + 89 echo) | yield backlink asli ~37× lebih tinggi per request |
| Both | gabungan keduanya | setara CLI `--modern` tanpa `--no-legacy` |

### Custom mode

Checkbox **Custom** + kotak angka di grup Run memetakan *custom mode* program asli (key config
`custom mode` + `custom mode number`, bawaan **800**): saat aktif, hanya **N situs pertama**
daftar ping yang dipakai. Cap-nya di level *situs* — tiap situs tetap menghasilkan dua bentuk
submit, jadi 800 situs ≈ 1.600 target. *Statistics only* tetap diutamakan bila keduanya aktif.
Versi CLI: `submit --custom --custom-number 500`.

### Deep link indexer only

Checkbox **Deep links only** memetakan key config asli `deep link indexer only`: kirim **hanya
ke situs yang mengindeks deep link** (URL halaman dalam seperti `example.com/blog/post`). Saat
aktif, daftar legacy **dilewati** dan yang dipakai hanya 14 layanan statistik otoritas plus
daftar modern terverifikasi. Versi CLI: `submit --deep-links`.

### config.ini

Semua pengaturan — threads, proxy, pilihan target, timeout, batch, filter — disimpan ke
`gsa_seo_indexer\config.ini` dan dibaca kembali saat launch berikutnya. File ini **dipakai
bersama GUI dan CLI**: nilai yang tidak dilewatkan sebagai flag di CLI memakai nilai config, dan
flag yang dilewatkan selalu menang. Nama kunci meniru config asli GSA (`threads`, `fast mode`,
`check url`, `use proxy`, `report format csv`, `site filter`, …) sehingga `config.ini` milik
program asli juga bisa dibaca langsung.

Setting engine yang lebih dalam ada di **Options → Advanced settings…**. Dua kunci tambahan:
`persist host cache` (default aktif) dan `dns cache` (default aktif).

### Proxy (Scrape / Load / Test)

Frame **Proxies** di jendela utama punya tiga tombol:

* **Scrape** — unduh daftar proxy publik, uji semuanya, isi kolom dengan yang hidup.
* **Load...** — muat file `.txt` berisi proxy (satu per baris; `#` = komentar) ke kolom.
* **Test** — uji isi kolom saat ini: proxy mati dibuang dari kolom, dan saklar *Use*
  mati otomatis kalau tak ada yang lolos.

Alur yang disarankan: **Load...** → **Test** → Start.

### Indexing services

**Options → Indexing services…** adalah manajer sungguhan: daftar 48 definisi service,
**Toggle enabled**, **Set key…**, **Test**, **Run all enabled**, dan **Edit .ini…**. Kolom
**Key** menunjukkan layanan yang sudah punya kredensial. Key disimpan di
`gsa_seo_indexer\keys.json`, terpisah dari `config.ini`, dan tidak pernah ditulis ke laporan:

```json
{ "Instant Link Indexer": { "API-Key": "kunci-anda" } }
```

CLI setara: `services --set-key "Instant Link Indexer" kunci-anda` (opsi `--key-var` untuk
variabel selain `API-Key`).

### Import & preview

**Multiple** atau `Ctrl+O` membuka dialog preview: daftar URL hasil parse bisa diedit sebelum
run, lengkap dengan hitungan duplikat dan invalid, plus tombol **Dedupe**.
**File → Import from clipboard** (`Ctrl+Shift+V`) mem-paste daftar URL langsung dari clipboard.

### Crawl Online

Tombol **Crawl online...** membuka dialog *Crawl for URLs*: beri satu URL seed, atur **Levels
deep**, lalu centang *With anchor text* / *External sites* / *Same folder* / *Use proxies*.
Mendapat URL dengan CLI:

```bash
python -m gsa_seo_indexer.cli crawl https://example.com/blog/ --depth 2 --out urls.txt
python -m gsa_seo_indexer.cli crawl https://example.com/ --same-folder --tsv
```

### Filter & pencarian hasil

Bar di atas tabel hasil: dropdown **Show** (All / Backlinks / Listed / Echo / Failed / Skipped)
+ kolom pencarian bebas (`Ctrl+F`). Baris yang tidak cocok di-detach, bukan dihapus. Counter
`n/m rows` di kanan menunjukkan berapa yang terlihat.

### Dark mode & font

**Options → Dark mode** mengganti palet lengkap, tersimpan di config.ini. Semua teks memakai
Segoe UI 10 dengan baris tabel lebih tinggi.

### Minimize to tray

**Options → Minimize to tray**: meminimalkan jendela menyembunyikannya ke area notifikasi.
Klik kiri ikon tray membuka kembali; klik kanan memberi menu Restore/Quit. Di luar Windows opsi
ini diabaikan dengan aman.

### Lanjutkan run yang terputus

Setiap run menulis daftar URL-nya ke `gsa_seo_indexer\last_queue.txt`. Run yang selesai bersih
menghapus file-nya; run yang di-Stop atau crash meninggalkannya — saat launch berikutnya muncul
tawaran untuk membuka URL tersebut kembali di preview. Dead-host cache juga bertahan
**antar-proses** (`gsa_seo_indexer\host_cache.json`, TTL 30 menit).

---

## CLI

Semua flag `submit` yang tidak dilewatkan mengambil nilainya dari `config.ini`, lalu dari default.

```bash
# submit satu URL
python -m gsa_seo_indexer.cli submit --url https://example.com/

# submit daftar URL, 20 thread, simpan laporan
python -m gsa_seo_indexer.cli submit -f urls.txt --threads 20 --report run.csv

# batasi jumlah situs, hanya situs statistik
python -m gsa_seo_indexer.cli submit -f urls.txt --sites 200 --stats-only

# custom mode program asli: hanya 500 situs pertama daftar ping
python -m gsa_seo_indexer.cli submit -f urls.txt --custom --custom-number 500

# kirim beberapa URL sekaligus per request
python -m gsa_seo_indexer.cli submit -f urls.txt --batch 10

# lewat proxy
python -m gsa_seo_indexer.cli submit -f urls.txt --use-proxy --proxy 1.2.3.4:8080

# uji proxy dulu sebelum run; yang mati dibuang,
# kalau semua mati run jalan TANPA proxy
python -m gsa_seo_indexer.cli submit -f urls.txt --proxy-file proxies.txt --test-proxies

# cek apakah URL bisa diindeks
python -m gsa_seo_indexer.cli check --url https://example.com/

# daftar situs ping
python -m gsa_seo_indexer.cli sites --count
python -m gsa_seo_indexer.cli sites --filter .ru

# daftar modern saja (2024-2026, bukan list GSA lama)
python -m gsa_seo_indexer.cli submit -f urls.txt --modern --no-legacy

# index service berbayar (format INI GSA)
python -m gsa_seo_indexer.cli services --list
python -m gsa_seo_indexer.cli services --test "BulkAddUrl.com" --url https://example.com/

# jalankan emulator index service lokal (seperti GSA sendiri)
python -m gsa_seo_indexer.cli emulate --port 8787
python -m gsa_seo_indexer.cli emulate --with-auth --login me --password secret

# proxy scraper
python -m gsa_seo_indexer.cli proxies --scrape --limit 10 --save proxies.txt

# pantau file log, submit URL baru seketika
python -m gsa_seo_indexer.cli watch -f backlinks.log --sites 50
```

---

## Cara kerja engine

`engine.Indexer` menerima daftar URL dan daftar target, lalu:

1. **Cek URL** (bisa dimatikan dengan `--no-check`): request `GET`; URL ditolak bila halaman
   berisi `noindex`, HTTP 404/410/451 (halaman hilang — tidak ada yang bisa diindeks), atau
   HTTP ≥ 500. Kode 401/403/405/429 dianggap *reachable* (halaman ada tapi menolak pembacaan).
2. **Submit** ke setiap target sesuai `mode`:
   - `append` → `http://host/path` + `http://target/` (tanpa encoding)
   - `query` → `http://host/?url=` + URL-encoded target
   - `post` → target dikirim sebagai body
3. **Deteksi backlink**: respons dipindai untuk `<a href="...target...">`. Echo parameter
   biasa **tidak** dihitung sebagai backlink.
4. **Report** CSV atau JSON, opsional dengan kolom `Indexable`.

Thread dibuat lewat `ThreadPoolExecutor`; jumlah thread, timeout, delay, proxy, dan filter site
diatur lewat `Settings`.

---

## Emulator index service (port 8787)

GSA bisa menjalankan server HTTP lokal yang **mensimulasikan** index service berbayar, supaya
tool GSA lain bisa submit tanpa membayar. Perilaku ini dipulihkan dari decompile
`TIndexServer` / `TIndexClient`:

* Server mendengarkan di `index emulation host` / `own port` (default `127.0.0.1:8787`).
* Program asli menulis ke `drivers\etc\hosts` supaya domain service sungguhan mengarah ke server
  lokal — request datang dengan `Host` header domain asli, dan itu yang dipakai untuk
  mencocokkan service.
* Tiap service dijawab sesuai INI-nya: `simulate_result` untuk sukses, `simulate_error` untuk
  gagal, `%url_count%` diganti jumlah URL.
* Dengan `with auth on` server menuntut `Authorization: Basic <base64>` atau
  `Authorization: Token <token>`, dan menjawab 401 kalau tidak cocok.

Implementasi klon: `gsa_seo_indexer/emulator.py` + subcommand `emulate`. Di GUI:
**Options → Start index-service emulator...**

Pengujian live terhadap 3 service menghasilkan balasan yang benar sesuai `simulate_result`
masing-masing:

```
Honest Indexer  -> {"success": true,"inserted":2,"remaining_credits":999}
2Index.Ninja    -> {"success": true,"errors": ["You sent invalid links"],...}
BulkAddUrl      -> {"taskId":925856,"taskCountUrls":999,...}
```

---

## Optimasi

### Di mana waktunya hilang

| Fase | Porsi | Catatan |
|---|---|---|
| read (menunggu server jawab) | **55%** | latensi server, bukan transfer |
| TLS handshake | 20% | diulang tiap request |
| TCP connect | 14% | diulang tiap request |
| DNS resolve | 11% | di-cache OS setelah lookup pertama |

### Yang diimplementasikan

| Optimasi | Efek terukur | Default |
|---|---|---|
| **Timeout 5 detik** | **1,5× lebih cepat**, hasil identik | ya |
| **50 thread** | 4,5 → 14,5 req/s | ya |
| **Cache host mati** | 1,26× lebih cepat | ya |
| **Pemisahan timeout** | host mati tidak menahan thread | ya |
| **Validasi URL** | hemat penuh | ya |
| **Deteksi backlink ketat** | angka jadi jujur | ya |
| **Dedupe per host** | 25% request lebih sedikit | opsional (`--fast`) |
| **Pre-flight liveness** | hanya efektif kalau mayoritas host mati | opsional (`--preflight`) |
| **Batch multi-URL** | **4,5× lebih cepat, tapi backlink hilang** | opsional (`--batch N`) |
| **Connection pooling** | 2,6× lebih cepat per host | opsional (`--pool`) |

Benchmark akhir — 150 situs → 314 target, 5 URL, setting sama persis:

| Strategi | Waktu | HTTP-ok | Backlink | Listed |
|---|---|---|---|---|
| **A setting awal** (10 thread, timeout 12 dtk, tanpa cache) | 337,6 dtk | 751 | **5** | 50 |
| **B setting sekarang** (50 thread, timeout 5 dtk, cache host mati) | **91,5 dtk** | 745 | **5** | 50 |
| C + batch (opt-in) | 20,6 dtk | 740 | 0 | 55 |

**B memberi 3,7× speedup dengan hasil yang identik.** C jauh lebih cepat lagi tapi kehilangan
semua backlink, jadi tidak dijadikan default.

### Perintah optimasi

```bash
# satu request per host (default GUI)
python -m gsa_seo_indexer.cli submit -f urls.txt --fast

# batch 10 URL per request + repair
python -m gsa_seo_indexer.cli submit -f urls.txt --batch 10 --repair

# matikan pre-flight
python -m gsa_seo_indexer.cli submit -f urls.txt --no-preflight

# timeout terpisah
python -m gsa_seo_indexer.cli submit -f urls.txt --connect-timeout 4 --read-timeout 8
```

---

## Proxy

**Alur proxy masuk ke program** — ada empat pintu, dan hanya hasil scrape yang otomatis diuji:

| Sumber | Diuji hidup/mati dulu? |
|---|---|
| `--scrape-proxies N` / tombol **Scrape** (GUI) | ✅ ya (tes konektivitas ke `https://www.gstatic.com/generate_204`) |
| `--proxy host:port` (CLI) | ❌ dipercaya langsung |
| `--proxy-file` (CLI) / tombol **Load...** (GUI) | ❌ dipercaya langsung |
| `proxy list` di `config.ini` | ❌ dipercaya langsung |

Proxy manual bisa diuji pra-run dengan `--test-proxies` (CLI) atau tombol **Test** di frame
Proxies (GUI): semua proxy yang terkumpul diuji paralel, hanya yang menjawab yang dipakai.
Kalau **semua** gagal, run tetap jalan **tanpa proxy** (diberi tahu di output) — bukan
membakar ribuan request lewat proxy mati. Proxy SOCKS4/SOCKS5 disaring sebelum run karena
engine hanya mendukung proxy HTTP.

Selama run, penentuan hidup/mati yang sesungguhnya terjadi secara *lazy*: proxy yang gagal
(timeout, koneksi putus, HTTP 407/429/5xx) langsung di-*retire* dari pool untuk sisa run,
dan kalau seluruh pool habis, pool di-reset sekali. Kegagalan karena proxy tidak pernah
menandai host target mati.

Empat sumber daftar proxy publik dipakai (~80.000 kandidat per scrape). Hasil ukur:

| Metrik | Langsung | Lewat proxy |
|---|---|---|
| Waktu (94 request) | 20,1 dtk | 57,3 dtk |
| HTTP-ok | 44 | 24 |
| Listed | 7 | 2 |

**2,9× lebih lambat dan menghasilkan lebih sedikit backlink.** Jalur proxy **tidak menjadi
default** — ia ada untuk kasus ketika IP asli Anda diblokir, bukan untuk kecepatan.

Jebakan yang ditemukan:

* **Proxy gagal ≠ host target mati.** Kegagalan dari proxy tidak menandai host target mati.
* SSL context harus dipasang pada **handler**, bukan lewat kwarg `context=` di `open()`.
* **TLS verify-first (v1.2.0):** koneksi HTTPS mencoba verifikasi sertifikat dulu; fallback
  tanpa verifikasi hanya untuk host berantai rusak/self-signed, dan keputusan itu diingat
  per host selama 1 jam. Berlaku untuk jalur langsung maupun lewat tunnel proxy.

---

## Mekanisme & recovery binary asli

| Temuan | Nilai |
|---|---|
| Versi program | GSA SEO Indexer v3.09 (DEMOVERSION) |
| Format submit | `http://<host><path><url-target>` (target tanpa URL-encoding) |
| Daftar situs ping | 6.671 situs, disimpan di `pr_cache.dat` |
| Jumlah thread | 10 (bawaan, `config.ini`) |
| Index service | 48 definisi INI di `index_service/` |

`SEO_Indexer.exe` dipaket dengan packer kustom yang menyamar sebagai UPX, sehingga `upx -d`
menolak membukanya. Recovery dilakukan dengan:

1. Dump 163 MB memory proses yang sedang berjalan (598 region).
2. Rekonstruksi image PE dari memory tersebut, lalu **section `CODE`** saja diekstrak (3,6 MB).
3. `CODE` section dianalisa dengan Ghidra memakai **BinaryLoader**
   (`-loader BinaryLoader -loader-baseAddr 0x401000 -processor x86:LE:32:default`) —
   11.257 fungsi, string pada VA yang benar.
4. Dari decompile diperoleh fungsi `FUN_00723338` (pasangan *host + path* dan flag
   `[Statistics Only]`) serta fungsi HTTP client `FUN_005e1dac`.
5. Daftar 6.671 situs diambil dari `pr_cache.dat` milik GSA sendiri.

Perintah pembantu:

```bash
# Ghidra headless decompile
analyzeHeadless <proj> <name> -import code_section.bin \
  -loader BinaryLoader -loader-baseAddr 0x401000 -processor x86:LE:32:default \
  -scriptPath ./ghidra_scripts -postScript Final.java

# REA CLI
rea analyze <path> --provider ghidra
rea decompile <path> --procedure=0x723338 --provider ghidra
```
