# GSA SEO Indexer — semissal (reimplementasi)

Program ini adalah **reimplementasi dari nol** dari mekanisme kerja
*GSA SEO Indexer v3.09* yang sudah terpasang di komputer ini
(`C:\Program Files (x86)\GSA SEO Indexer\SEO_Indexer.exe`).

Tidak ada kode, binary, atau resource milik GSA yang dipakai. Yang diambil
hanya **pengetahuan tentang mekanismenya**, yang dipulihkan lewat reverse
engineering dengan Ghidra (lewat MCP REA).

---

## 1. Mekanisme yang ditemukan

GSA SEO Indexer bekerja dengan cara berikut:

> Kirim URL target ke ribuan situs pihak ketiga bertipe *statistik*, *whois*
> dan *network log*. Situs-situs itu sangat sering di-crawler Googlebot dan
> Bingbot, sehingga target muncul sebagai **backlink** di domain yang
> berotorasi tinggi. Crawler yang folgt backlink itu kemudian menemukan
> halaman target.

Ini bukan "kirim ke Google" — Google tidak pernah menerima request dari
program ini. Yang terjadi adalah **membuat jejak digital** (digital
footprint) di situs yang sudah terindeks search engine.

Dari binary asli berhasil dipulihkan:

| Temuan | Nilai |
|---|---|
| Versi program | GSA SEO Indexer v3.09 (DEMOVERSION) |
| Mekanisme | submit URL ke situs statistik/whois + index service berbayar |
| Format submit | `http://<host><path><url-target>` (target **tanpa** URL-encoding, scheme dipertahankan) |
| Format alternatif | `http://<host>/?url=<url-encoded>` |
| Daftar situs ping | 6.671 situs, disimpan di `pr_cache.dat` (format: `URL\xFF<status>\r\n`) |
| Host statistik bawaan | `alexa.com`, `siteadvisor.com`, `traffic.alexa.com`, `grapher.compete.com`, `siteanalytics.compete.com`, `quantcast.com`, `api.thumbalizr.com`, `open.thumbshots.org`, `pagepeeker.com`, `majesticseo.com`, `yandex.ru/cycounter` |
| Jumlah thread | 10 (bawaan, `config.ini`) |
| Mode | *Statistics Only* / *Redirect Only* / *Custom* (custom mode number bawaan 800) |
| Proxy | proxy scraper bawaan, diuji lewat `azenv.php` / `proxyjudge.us` |
| Index service | 48 definisi INI di `index_service/` (API berbayar) |
| Emulator | HTTP server lokal di `127.0.0.1:8787` untuk menyimulasikan index service (class `TIndexServer` / `TIndexClient`) |

### Cara recovery dilakukan

`SEO_Indexer.exe` dipaket dengan packer kustom yang menyamar sebagai UPX
(nama section `.UPX0/.UPX1/.UPX2` dipalsukan), sehingga `upx -d` menolak
membukanya. Solusinya:

1. Dump 163 MB memory proses yang sedang berjalan (598 region) — paket
   ter-unpack di memory.
2. Rekonstruksi image PE dari memory tersebut, lalu **section `CODE` saja**
   diekstrak (3,6 MB).
3. `CODE` section dianalisa dengan Ghidra memakai **BinaryLoader**
   (`-loader BinaryLoader -loader-baseAddr 0x401000 -processor x86:LE:32:default`).
   Loader PE bawaan Ghidra memetakan section dengan offset yang tidak cocok
   pada PE hasil rekonstruksi; BinaryLoader memetakan 1:1 sehingga
   decompile akurat (11.257 fungsi, string pada VA yang benar).
4. From decompile diperoleh fungsi `FUN_00723338` yang memuat pasangan
   *host + path* (`/siteinfo/`, `/sites/`, `/charts/backlinks-discovery`,
   `/graph`) dan flag `[Statistics Only]`, plus fungsi HTTP client
   `FUN_005e1dac` yang membangun request.
5. Daftar 6.671 situs diambil dari `pr_cache.dat` milik GSA sendiri.

> Catatan: file `config.json` MCP REA di `~/.zcode/cli/config.json` sudah
> ditambahkan env `GHIDRA_INSTALL_DIR` dan `JAVA_HOME` agar provider Ghidra
> bisa dipakai (backup ada di `config.json.bak-gsa`).

---

## 2. Struktur program

```
C:\tools\GSA INDEXER\
├── gsa_seo_indexer/
│   ├── __init__.py
│   ├── __main__.py          python -m gsa_seo_indexer  -> GUI
│   ├── engine.py             inti: submit, check URL, thread pool, report
│   ├── cli.py                antarmuka command line
│   ├── gui.py                antarmuka Tkinter
│   ├── services.py           pembacaan definisi index service (format INI GSA)
│   ├── emulator.py           server lokal yang mensimulasikan index service
│   ├── proxies.py            proxy scraper + tester
│   ├── crawler.py            crawl situs untuk URL (padanan TForm11 "Crawl for URLs")
│   ├── watcher.py            pemantau file untuk URL baru
│   ├── data/
│   │   ├── ping_sites.txt    6.671 situs ping hasil recovery (2019-2023)
│   │   └── modern_targets.json  18 endpoint modern terverifikasi (2024-2026)
│   ├── index_service/        47 definisi index service (format INI GSA)
│   └── reports/              output CSV/JSON
├── reverse_engineering_evidence/   bukti: script Ghidra, decompile, dump
├── start_gui.bat / start_cli.bat / run_demo.bat
└── README.md
```

Kebutuhan: **Python 3.10+**, tanpa dependensi pihak ketiga.

---

## 3. Pemakaian

### GUI

```
start_gui.bat
```

atau

```
python -m gsa_seo_indexer
```

Jendela meniru layout program asli: kolom input URL/Domain + tombol Start /
Multiple / Stop, opsi threads, filter site, dan penghitung `Q:` / `LpM:`
di kanan bawah.

**Help → About…** menampilkan nama aplikasi, versi, dan kredit developer
(RIZAL DRAG); CLI setara dengan `--version`:
```
GSA SEO Indexer Clone v1.0.0
Developed by RIZAL DRAG © 2026
```

Tabel hasil memakai kolom **Target URL | Site | Link | Code | ms**:

| Kolom | Isi |
|---|---|
| Target URL | URL yang Anda submit (`Result.target`) |
| Site | host tujuan yang diping |
| Link | `✓ indexed` (anchor asli ke target), `● listed` (muncul di halaman hasil), `○ echo` (hanya HTTP 200 tanpa link), atau pesan error |
| Code | kode HTTP |
| ms | waktu respons |

Warna baris mengikuti jenis link (hijau = indexed, kuning tua = listed,
abu-abu = echo/skipped, merah = failed). Penghitung **Backlinks:** di kanan
bawah menghitung baris `indexed` + `listed` — itu metrik hasil yang
sebenarnya, bukan sekadar HTTP 200.

Sebelum run dimulai setiap URL divalidasi (`validate_url`); URL yang tidak
lengkap (mis. tanpa `http://`) ditolak di muka dengan alasannya, bukan
dikirim ke ribuan situs lalu gagal diam-diam. Tombol **Start** dinonaktifkan
selama run berjalan dan **Stop** aktif; shortcut: `Ctrl+O` buka daftar URL,
`Ctrl+S` simpan laporan, `Esc` stop.

Progress bar dihitung dari total riil engine (satu request per host saat
mode *Fast* aktif, termasuk penambahan total oleh pass repair), bukan dari
`jumlah URL × jumlah target` yang menggandakan host legacy dan membuat bar
berhenti di 50%.

#### Pemilih daftar target

Grup **Sources** punya radio **Targets: Legacy / Modern / Both**:

| Pilihan | Target | Catatan |
|---|---|---|
| Legacy | 6.671 situs (13.356 bentuk URL) | daftar hasil recovery dari program asli (2019-2023) |
| Modern | 18 endpoint terverifikasi | yield backlink asli ~37× lebih tinggi per request |
| Both | gabungan keduanya | setara CLI `--modern` tanpa `--no-legacy` |

#### Custom mode

Checkbox **Custom** + kotak angka di grup Run memetakan *custom mode*
program asli (key config `custom mode` + `custom mode number`, bawaan
**800**): saat aktif, hanya **N situs pertama** daftar ping yang dipakai.
Cap-nya di level *situs* — tiap situs tetap menghasilkan dua bentuk
submit, jadi 800 situs ≈ 1.600 target — berbeda dari *max sites* di
Advanced settings yang memotong daftar target jadi-jadi. *Statistics
only* tetap diutamakan bila keduanya aktif. Versi CLI:
`submit --custom --custom-number 500`.

#### Deep link indexer only

Checkbox **Deep links only** di baris filter grup Sources memetakan key
config asli `deep link indexer only`: kirim **hanya ke situs yang
mengindeks deep link** (URL halaman dalam seperti `example.com/blog/post`),
bukan cuma domain akar — berguna saat mengindeks backlink hasil SER yang
hampir selalu URL halaman dalam. Daftar ping legacy hasil recovery tidak
membawa flag kapabilitas per-situs (metadata itu tidak ada di binary),
jadi pemetaannya: saat aktif, daftar legacy **dilewati** dan yang dipakai
hanya set yang terbukti menyimpan URL utuh — 14 layanan statistik
otoritas (bentuk submit hasil decompile menempel URL penuh) plus daftar
modern terverifikasi. Versi CLI: `submit --deep-links`.

#### config.ini

Semua pengaturan — threads, proxy, pilihan target, timeout, batch, filter —
disimpan ke `gsa_seo_indexer\config.ini` dan dibaca kembali saat launch
berikutnya. File ini **dipakai bersama GUI dan CLI**: nilai yang tidak
dilewatkan sebagai flag di CLI memakai nilai config, dan flag yang
dilewatkan selalu menang. Nama kunci meniru config asli GSA (`threads`,
`fast mode`, `check url`, `use proxy`, `report format csv`, `site filter`,
…) sehingga `config.ini` milik program asli juga bisa dibaca langsung.

Setting engine yang lebih dalam (timeout, connect/read timeout, delay,
max sites, preflight, redirect, skip-dead-host, random delay, connection
pool, kolom backlink di laporan) ada di **Options → Advanced settings…**
dan ikut tersimpan di config.ini. Dua kunci tambahan: `persist host cache`
(menulis host mati ke `gsa_seo_indexer\host_cache.json`, default aktif)
dan `dns cache` (resolve DNS sekali per host per run, default aktif).


#### Indexing services

**Options → Indexing services…** sekarang manajer sungguhan (dulu hanya
dump teks): daftar 48 definisi service, **Toggle enabled** (menulis ulang
satu baris `enabled=` di file INI tanpa merusak komentar), **Set key…**
(menyimpan kredensial layanan), **Test** (mengirim URL dari jendela utama
ke service terpilih), **Run all enabled** (submit ke semua service aktif;
hasilnya masuk tabel utama dan laporan), dan **Edit .ini…** (membuka
definisi di editor default).

Kolom **Key** menunjukkan layanan yang sudah punya kredensial tersimpan.
Tanpa key, request keluar dengan teks literal `%API-Key%` di dalam body —
tidak ada layanan asli yang menerimanya (sejauh ini yang membuat path ini
bekerja hanyalah emulator). Key disimpan di `gsa_seo_indexer\keys.json`,
terpisah dari `config.ini`, dan tidak pernah ditulis ke laporan:

```json
{ "Instant Link Indexer": { "API-Key": "kunci-anda" } }
```

CLI setara: `services --set-key "Instant Link Indexer" kunci-anda`
(opsi `--key-var` untuk variabel selain `API-Key`).

#### Import & preview

**Multiple** atau `Ctrl+O` membuka dialog preview: daftar URL hasil
parse bisa diedit sebelum run, lengkap dengan hitungan duplikat dan
invalid, plus tombol **Dedupe**. **File → Import from clipboard**
(`Ctrl+Shift+V`) mem-paste daftar URL langsung dari clipboard ke preview
yang sama.

#### Crawl Online

Tombol **Crawl online...** di dialog preview (padanan tombol *Crawl
Online* TForm2 → TForm11 di program asli) membuka dialog *Crawl for
URLs*: beri satu URL seed, atur **Levels deep**, lalu centang *With
anchor text* / *External sites* / *Same folder* / *Use proxies* —
persis kontrol yang dipulihkan dari TForm11. Hasilnya muncul di tabel
URL / Anchor / DoFollow / Level untuk direview dulu, lalu **Add URLs to
queue** menambahkannya ke preview (URL seed sendiri tidak ikut; link
eksternal dikumpulkan tapi tidak pernah di-crawl). Versi CLI-nya:

```bash
python -m gsa_seo_indexer.cli crawl https://example.com/blog/ --depth 2 --out urls.txt
python -m gsa_seo_indexer.cli crawl https://example.com/ --same-folder --tsv
```

#### Filter & pencarian hasil

Bar di atas tabel hasil: dropdown **Show** (All / Backlinks / Listed /
Echo / Failed / Skipped) + kolom pencarian bebas (cocok ke Target, Site,
atau Link; `Ctrl+F` langsung fokus ke kolom pencarian). Baris yang tidak
cocok di-detach, bukan dihapus — ganti filter dan semuanya kembali.
Counter `n/m rows` di kanan menunjukkan berapa yang terlihat.

#### Dark mode & font

**Options → Dark mode** mengganti palet lengkap (window, frame, tabel,
entry; warna tag Link ikut disesuaikan agar tetap terbaca), tersimpan
di config.ini, dan berlaku juga saat launch berikutnya. Semua teks
memakai Segoe UI 10 dengan baris tabel lebih tinggi.

#### Minimize to tray

**Options → Minimize to tray** (key config asli `min to tray`): saat
aktif, meminimalkan jendela menyembunyikannya ke area notifikasi
bukan taskbar. Klik kiri ikon tray membuka kembali jendela; klik kanan
memberi menu Restore/Quit. Ikon tray dibuat langsung lewat
`Shell_NotifyIconW` (ctypes/Win32) — tetap **tanpa dependensi pihak
ketiga** — dan otomatis muncul kembali bila Explorer restart.
Di luar Windows opsi ini diabaikan dengan aman (minimize biasa).

#### Lanjutkan run yang terputus

Setiap run menulis daftar URL-nya ke `gsa_seo_indexer\last_queue.txt`
(mekanisme yang sama dengan program asli). Run yang selesai bersih
menghapus file-nya; run yang di-Stop atau crash meninggalkannya — dan
saat launch berikutnya muncul tawaran untuk membuka URL tersebut kembali
di preview. Dead-host cache juga bertahan **antar-proses**: host yang
terbukti mati ditulis ke `gsa_seo_indexer\host_cache.json` di akhir
tiap run, dan run berikutnya melewatinya tanpa membayar connect timeout
lagi (selama belum lewat TTL 30 menit). Di samping itu, resolve DNS
dibatalkan duplikasinya per host per run (`dns cache`), karena kedua form
satu situs memakai host yang sama dan sering in-flight bersamaan.

### CLI

Semua flag `submit` yang tidak dilewatkan mengambil nilainya dari
`gsa_seo_indexer\config.ini` (file yang sama dengan GUI), lalu dari default.

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

## 4. Cara kerja engine

`engine.Indexer` menerima daftar URL dan daftar target, lalu:

1. **Cek URL** (bisa dimatikan dengan `--no-check`): request `GET`, Reading
   respons; URL ditolak bila HTTP ≥ 400 atau halaman berisi `noindex`.
2. **Submit** ke setiap target sesuai `mode`:
   - `append` → `http://host/path` + `http://target/` (tanpa encoding)
   - `query` → `http://host/?url=` + URL-encoded target
   - `post` → target dikirim sebagai body
3. **Deteksi backlink**: respons dipindai untuk `<a href="...target...">`.
   Echo parameter biasa **tidak** dihitung sebagai backlink.
4. **Report** CSV atau JSON, opsional dengan kolom `Indexable`.

Thread dibuat lewat `ThreadPoolExecutor`; jumlah thread, timeout, delay,
proxy, dan filter site semuanya diatur lewat `Settings` (bisa disimpan ke
`config.ini` dengan format yang sama seperti GSA).

---

## 5. Hasil pengukuran

Diuji terhadap `http://example.com/` (URL uji standar). Dua daftar diuji
dengan deteksi backlink yang ketat.

### A. Daftar legacy (6.671 situs GSA, sampel 500)

| Metrik | Nilai |
|---|---|
| Percobaan | 1.014 request |
| Respons HTTP 2xx/3xx | 453 (45%) |
| **Backlink terverifikasi** | **3 (0,30%)** |
| Hanya "listed" (tampak di halaman, bukan link) | 206 |

Tiga backlink yang terverifikasi, masing-masing dicek ulang manual:

```
https://anolink.com/http://example.com/
http://jump.x0.to/http://example.com/
http://m-husc.a17.csinet.es/?url=http%3A%2F%2Fexample.com%2F
```

### B. Daftar modern (18 endpoint, terverifikasi 2024-2026)

| Metrik | Nilai |
|---|---|
| Percobaan | 18 request |
| Respons HTTP 2xx/3xx | 18 (100%) |
| **Backlink terverifikasi** | **2 (11%)** |
| Hanya "listed" | 12 |

```
https://search.naver.com/search.naver?query=...   (link ke target)
https://search.seznam.cz/?q=...                    (link ke target)
```

**Perbandingan: 0,30% vs 11% backlink per request — daftar modern ~37x
lebih tinggi yield**, dan tidak ada request yang gagal.

### Kenapa yield legacy sekecil itu

Sebagian besar situs di list GSA hanya menggemakan parameter `?url=` di
halaman depan tanpa pernah menyimpan link. Hanya sebagian kecil yang
benar-benar menulis URL ke halaman statistik mereka. Daftar itu juga sudah
berumur 5+ tahun (2019-2023), sehingga banyak yang mati atau berubah arah.

Detektor program sengaja ketat — hanya menghitung anchor yang benar-benar
menunjuk ke host target, dan mengabaikan:

* echo parameter (`/?url=...` yang menunjuk ke halaman itu sendiri)
* `mailto:` dan link relatif
* halaman hasil pencarian Google/Bing (bukan sumber backlink)

Hasil yang hanya "tampil di halaman" dilaporkan terpisah sebagai
**Listed**, supaya tidak tercampur dengan backlink sungguhan.

---

## 6. Emulator index service (port 8787)

GSA bisa menjalankan server HTTP lokal yang **mensimulasikan** index service
berbayar, supaya tool GSA lain bisa submit tanpa membayar. Perilaku ini
dipulihkan dari decompile `TIndexServer` / `TIndexClient`:

* Server mendengarkan di `index emulation host` / `own port`
  (default `127.0.0.1:8787`).
* Program asli menulis ke `drivers\etc\hosts` supaya domain service
  sungguhan mengarah ke server lokal — request datang dengan `Host` header
  domain asli, dan itu yang dipakai untuk mencocokkan service.
* Tiap service dijawab sesuai INI-nya: `simulate_result` untuk sukses,
  `simulate_error` untuk gagal, `%url_count%` diganti jumlah URL.
* `[error_message] front=` / `back=` membingkai pesan error.
* Dengan `with auth on` server menuntut `Authorization: Basic <base64>`
  atau `Authorization: Token <token>`, dan menjawab 401 kalau tidak cocok.
* Response root: `<html><head><title>Indexer Server here</title></head>...`

Implementasi klon: `gsa_seo_indexer/emulator.py` + subcommand `emulate`.

```bash
python -m gsa_seo_indexer.cli emulate --port 8787
python -m gsa_seo_indexer.cli emulate --with-auth --login me --password secret
```

Di GUI: **Options → Start index-service emulator...**

Pengujian live terhadap 3 service menghasilkan balasan yang benar sesuai
`simulate_result` masing-masing:

```
Honest Indexer  -> {"success": true,"inserted":2,"remaining_credits":999}
2Index.Ninja    -> {"success": true,"errors": ["You sent invalid links"],...}
BulkAddUrl      -> {"taskId":925856,"taskCountUrls":999,...}
```

---

## 7. Optimasi

### Di mana waktunya hilang

Run di-profil dulu (300 situs, 25 thread, timeout 10 dtk) sebelum menulis
optimasi apa pun. Waktu di dalam satu request terbagi begini:

| Fase | Porsi | Catatan |
|---|---|---|
| read (menunggu server jawab) | **55%** | latensi server, bukan transfer |
| TLS handshake | 20% | diulang tiap request |
| TCP connect | 14% | diulang tiap request |
| DNS resolve | 11% | di-cache OS setelah lookup pertama |

Tiga konsekuensi:

1. Yang paling mahal bukan request-nya, tapi **host mati** — satu host mati
   membakar satu slot thread selama penuh timeout.
2. TCP + TLS = 34% waktu, dan keduanya diulang untuk tiap request.
3. Regex scan isi halaman **tidak** masalah (0 ms per halaman 300 KB).

DNS juga ternyata mahal kalau dingin: 223 ms per host, 0,01 ms setelah cache
OS hangat. Probe paralel 300 host: **6 detik** (19 ms/host efektif).

### Yang diimplementasikan

| Optimasi | Cara kerja | Efek terukur | Default |
|---|---|---|---|
| **Timeout 5 detik** | 42% request sukses butuh >3 dtk dan memakai 62% waktu; cap 10 dtk hanya menambah timeout | **1,5× lebih cepat**, HTTP-ok/backlink/listed identik | ya |
| **50 thread** | Throughput diukur: puncak di 50 thread, turun lagi di 100 | 4,5 → 14,5 req/s | ya |
| **Cache host mati** | Host yang gagal dicatat; request berikutnya ke host itu dilewati tanpa request | 1,26× lebih cepat | ya |
| **Pemisahan timeout** | connect timeout (4 dtk) dan read timeout (5 dtk) terpisah | host mati tidak menahan thread | ya |
| **Validasi URL** | URL rusak ditolak sebelum run, bukan setelah 13.000 request | hemat penuh | ya |
| **Deteksi backlink ketat** | Echo query string tidak lagi dihitung sebagai "listed" | angka jadi jujur | ya |
| **Dedupe per host** | Daftar lama punya 2 bentuk URL per situs di host yang sama | 25% request lebih sedikit | opsional (`--fast`) |
| **Pre-flight liveness** | Probe TCP paralel ke semua host, submit hanya ke yang hidup | hanya efektif kalau sebagian besar host mati | opsional (`--preflight`) |
| **Batch multi-URL** | 10 URL per request per situs, bukan 1 | **4,5× lebih cepat, tapi backlink hilang** | opsional (`--batch N`) |
| **Connection pooling** | TCP/TLS di-reuse antar request ke host yang sama | 2,6× lebih cepat **per host**, tapi ~10% lebih lambat di run lebar | opsional (`--pool`) |

### Batch: cepat tapi kehilangan link

Batch multi-URL adalahoptimasi terbesar dan sekaligus yang paling
berbahaya. Diuji dengan 10 URL × 414 target:

Benchmark akhir — 150 situs → 314 target, 5 URL, setting sama persis:

| Strategi | Waktu | HTTP-ok | Backlink | Listed |
|---|---|---|---|---|
| **A setting awal** (10 thread, timeout 12 dtk, tanpa cache) | 337,6 dtk | 751 | **5** | 50 |
| **B setting sekarang** (50 thread, timeout 5 dtk, cache host mati) | **91,5 dtk** | 745 | **5** | 50 |
| C + batch (opt-in) | 20,6 dtk | 740 | 0 | 55 |

**B memberi 3,7× speedup dengan hasil yang identik** — HTTP-ok, backlink
dan listed sama persis dengan A. C jauh lebih cepat lagi tapi kehilangan
semua backlink, jadi tidak dijadikan default.

Batch jelas menang: **2,2× lebih cepat** tanpa mengorbankan listed (18 vs
15 — justru naik, karena satu request memuat beberapa URL).

Tapi batch ada risikonya: sebuah situs biasanya hanya menyimpan URL
pertama dari sebuah batch, sehingga **backlink bisa hilang**. Di run dengan
10 URL, backlink turun dari 10 ke 0. Karena itu `run_batched()` menambahkan
pass "repair" — URL yang tidak tertinggal dikirim ulang satu per satu.

Ternyata batch sendiri sempat menipu deteksi: situs yang meng-echo query
string di halaman depan terhitung "listed", sehingga repair tidak pernah
jalan. Setelah echo query string tidak lagi dihitung sebagai listing,
angkanya konsisten — tapi batch tetap kehilangan backlink, jadi **repair
tidak dijadikan default**.

Ternyata batch sendiri mengubah deteksi: situs yang meng-echo query string
di halaman depan sempat terhitung "listed", sehingga repair tidak pernah
jalan. Setelah query-echo tidak lagi dihitung sebagai listing, angkanya
konsisten — tapi **batch tetap kehilangan backlink** dan karena itu
**batch bukan default**; dipakai lewat `--batch N --repair`.

### Perintah optimasi

```bash
# satu request per host, retry bentuk URL kedua hanya bila perlu (default GUI)
python -m gsa_seo_indexer.cli submit -f urls.txt --fast

# batch 10 URL per request + repair
python -m gsa_seo_indexer.cli submit -f urls.txt --batch 10 --repair

# matikan pre-flight (mulai lebih cepat, banyak request terbuang)
python -m gsa_seo_indexer.cli submit -f urls.txt --no-preflight

# timeout terpisah
python -m gsa_seo_indexer.cli submit -f urls.txt --connect-timeout 4 --read-timeout 8
```

Di GUI ada kolom **Batch**, centang **Fast (1 req/host)** dan **Repair misses**.

---

## 8. Proxy

Program asli GSA punya proxy scraper bawaan dan memakai proxy untuk
menyembunyikan asal traffic. Klon ini punya implementasi yang sama
(`gsa_seo_indexer/proxies.py`):

```bash
# scrape proxy publik, validasi terhadap target nyata, lalu pakai
python -m gsa_seo_indexer.cli submit -f urls.txt --use-proxy --scrape-proxies 20

# pakai daftar proxy sendiri
python -m gsa_seo_indexer.cli submit -f urls.txt --use-proxy --proxy-file proxies.txt
```

Empat sumber daftar proxy publik dipakai (~80.000 kandidat per scrape), dan
proxy yang lolos diuji **dua kali**: dulu konektivitas umum, lalu terhadap
URL target yang sebenarnya.

### Hasil pengukuran: proxy publik gratis tidak layak

| Metrik | Langsung | Lewat proxy |
|---|---|---|
| Waktu (94 request) | 20,1 dtk | 57,3 dtk |
| HTTP-ok | 44 | 24 |
| Listed | 7 | 2 |

**2,9× lebih lambat dan menghasilkan lebih sedikit backlink**, bahkan
setelah proxy divalidasi terhadap target asli.

Penyebabnya terukur:

* Hanya **6-14%** proxy publik yang benar-benar bisa menjangkau target
  HTTPS (dari 250 kandidat: 35 ke gstatic, 15 ke seznam).
* Proxy publik mati dalam **1-2 request** — satu timeout 5 detik sudah
  cukup untuk membunuhnya.
* Latensi lewat proxy jauh lebih tinggi: 6-10 detik per request,
  dibanding 276 ms langsung.

Jadi jalur proxy **tidak menjadi default** — ia ada untuk kasus ketika
IP asli Anda diblokir, bukan untuk kecepatan. Rotation proxy sudah
dibuat: proxy yang gagal dicoret, dan pool di-reset sekali kalau semua
gagal (kebanyakan kegagalan bersifat sementara).

Dua jebakan yang ditemukan diuji:

* **Proxy gagal ≠ host target mati.** Versi pertama menandai host target
  sebagai mati saat proxy timeout, lalu melewati host itu untuk sisa run.
  Sekarang hanya kegagalan yang datang dari target sendiri yang
  menandai host mati.
* SSL context harus dipasang pada **handler**, bukan lewat kwarg
  `context=` di `open()` — kalau tidak, tunnel proxy tidak pernah dipakai
  dan setiap proxy dianggap gagal.

---

## 9. Catatan etika dan batasan


* Program ini mengirim request otomatis ke ribuan situs. rate default
  sengaja konservatif; gunakan `--delay` bila dibutuhkan.
* Sebagian besar situs di daftar sudah mati atau berubah arah — ini
  daftar yang pulih dari GSA v3.09 (sekitar 2019–2023), bukan daftar
  fresh.
* Target harus `example.com`/URL milik Anda sendiri. Mengindekskan URL
  orang lain tanpa izin adalah penyalahgunaan.
* Tidak ada request submisi ke Google/Bing — mekanisme ini murni membuat
  peninggalan digital, sama seperti program aslinya. Opsi `--with-search-engines`
  hanya menambah kueri pencarian, bukan pengiriman backlink.

---

## 10. Perintah pembantu yang dipakai

```bash
# Ghidra headless decompile
analyzeHeadless <proj> <name> -import code_section.bin \
  -loader BinaryLoader -loader-baseAddr 0x401000 -processor x86:LE:32:default \
  -scriptPath ./ghidra_scripts -postScript Final.java

# REA CLI
rea analyze <path> --provider ghidra
rea decompile <path> --procedure=0x723338 --provider ghidra
```
