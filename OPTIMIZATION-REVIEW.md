# Review Optimasi — GSA SEO Indexer v1.3.0

Dokumen ini menjawab satu pertanyaan: **optimasi apa yang masih dibutuhkan proyek ini,
dan mana yang paling berdampak?** Semua angka di sini diukur, bukan diperkirakan —
lihat cara reproduksinya di bagian [Cara mengukur ulang](#cara-mengukur-ulang).

Ringkasnya: proyek ini sudah pintar menghindari kerja yang sia-sia (cache host mati,
DNS cache, dedupe per host, skip host yang timeout), tetapi **hot path per-request-nya
membuang ~29 ms CPU untuk setiap request** — bahkan untuk target `http://` yang tidak
memakai TLS sama sekali. Itu plafon throughput yang sebenarnya, bukan latensi server.

> Angka di paragraf itu adalah **keadaan saat audit** (commit `b649ab2`). Semua temuan
> P0–P7 di bawah sudah dikerjakan di branch ini; tabel §1 menyebut angka sesudahnya.

---

## 1. Ringkasan eksekutif

| Prioritas | Temuan | Dampak | Status |
|---|---|---|---|
| **P0** | `ssl.create_default_context()` dibuat **per request** (±29 ms, memuat CA bundle) | plafon ±34 req/s; ±5,6 menit CPU per run 11.669 target | ✅ **tuntas** |
| **P0** | `_classify_link` memindai seluruh body (sampai 200 kB) dua kali walau target tidak ada di halaman | ±1,9 ms CPU per halaman "miss" (mayoritas respons) | ✅ **tuntas** |
| **P0** | `max_sites` dipotong **dua kali** (di CLI/GUI *dan* di `run()`), dan `stats.total` dihitung sebelum pemotongan | `--sites 800` + 2 URL → hanya 400 situs yang benar-benar dikirimi; progress mandek 50% | ✅ **tuntas** |
| **P1** | Cek URL pra-run berjalan **serial** (1 request per URL) | 10 URL × 300 ms: 3,00 s → **0,60 s** (5×) | ✅ **tuntas** |
| **P1** | Tabel GUI menyimpan **semua** baris (333.800 baris untuk 50 URL × 6.676 host) | tabel dibatasi 5.000 baris + penghitung sisanya | ✅ **tuntas** |
| **P1** | `pool.map()` = head-of-line blocking untuk `on_result()` | 1 request lambat tidak lagi menahan 5 request lain | ✅ **tuntas** |
| **P1** | `report_format = json` menyalin seluruh hasil ke list baru | peak 28 MB → **0,3 MB** (100k baris) | ✅ **tuntas** |
| **P2** | Host yang pernah sukses **tidak pernah** ditandai mati | 5.000 URL: 5.000 request sia-sia → **2** | ✅ **tuntas** |
| **P2** | `DnsCache._entries` tidak pernah dipangkas / menyimpan objek exception | 20.000 host → tabel dibatasi **10.000** entri; exception tidak disimpan | ✅ **tuntas** |
| **P2** | `connect timeout` tidak berpengaruh di jalur default (urllib hanya menerima satu timeout) | host mati: **5,03 s → 4,00 s** (atau 1,00 s dengan `--timeout 1`) | ✅ **tuntas** |
| **P2** | Tidak ada test, CI, atau linter (6.300 LOC, 0 test) | regresi performa/correctness tidak terdeteksi | ✅ **tuntas** (123 test + CI + `ruff`) |
| **P3** | Tiga jalur HTTP paralel (`submit_one`, `submit_batch`, `PooledHttpClient`) | perilaku (timeout, redirect, verify) mudah menyimpang | ✅ **tuntas** (§7) |
| **P3** | `Settings.random_delay = True`, padahal `config.ini` menulis `0` dan dokumentasi bilang "tidak ada manfaat" | sleep 10–80 ms di dalam worker untuk API call langsung | ✅ **tuntas** (§7) |
| **P4** | Opener dibangun ulang per request; JSON `indent=1`; "run enabled" sekuensial; GUI menguras queue tanpa batas | opener 844 → **1.140 req/s**; JSON 100k **1,90 → 1,24 s**; 48 service **9,4 → 1,2 s** | ✅ **tuntas** (§12) |
| **P5** | `_classify_link` menjalankan satu alternasi besar atas seluruh body untuk memutuskan "listed" | halaman 165 kB: **1,43 → 0,41 ms** (3,5×), 280k kasus ekuivalen | ✅ **tuntas** (§13) |
| **P6** | `self.results` menahan **semua** baris sampai run selesai | 660k baris ≈ 115 MB; kini puncak datar (400k baris: 101 MB → **17 MB**) | ✅ **tuntas** (§14) |
| **P7** | Target `mode="post"` tetap dikirim sebagai **GET tanpa body** — `with_payload()` mengembalikan URL dasar dan semua transport menulis `GET` mati | target POST tidak pernah menyampaikan URL sama sekali; hasilnya "submitted" untuk halaman yang tidak menerima apa pun | ✅ **tuntas** (§15) |
| **P8** | Tidak ada `Accept-Encoding: gzip`; body sampai 200 kB dikirim utuh | halaman uji 120 kB -> **32,5 kB** di kabel (3,7x); keputusan klasifikasi identik, kedua transport | ✅ **tuntas** (§16) |
| **P9** | 429/503 hanya dicatat `failed`, `Retry-After` diabaikan | 503 lalu 200: **1 -> 2 request, failed -> submitted**; plafon tunggu mencegah worker diparkir 60 s | ✅ **tuntas** (§17) |
| **P10** | Concurrency hanya bisa dibeli dengan thread (satu stack per request) | `--transport asyncio`: baris & statistik identik; di loopback **setara** dengan thread (0,97-1,19x) | ✅ **tuntas** (§18) |
| **P11** | Wheel tidak lengkap: `assets/app.ico` tidak ikut terpaket, rilis belum ada | `pip install` kini membawa ikon + 48 INI + 11.661 situs; rilis via tag, trusted publishing | ✅ **tuntas** (§19) |
| **P12** | Jalur asyncio tidak punya pool: satu koneksi per request | pool 1 koneksi untuk 20 request ke 1 host, **1,6–3,5x** lebih cepat (tergantung server) | ✅ **tuntas** (§20) |
| **P13** | `--transport asyncio` menolak proxy (jatuh ke thread) | HTTP absolut + tunnel **CONNECT** untuk HTTPS, tetap di event loop | ✅ **tuntas** (§21) |

---

## 2. Akar masalah P0: TLS context per request

`Indexer._opener()` dulu memanggil `ssl.create_default_context()` **setiap kali**
sebuah request dibuat:

```python
# sebelum
ctx = ssl.create_default_context() if verify else self._ssl_ctx
```

Biaya yang terukur di mesin uji: **28,9 ms per panggilan** (memuat + mem-parse CA
bundle). Tiga hal membuat ini jauh lebih buruk daripada sekadar "29 ms":

1. **Dibayar juga untuk target `http://`.** Mayoritas daftar legacy adalah `http://`,
   dan context-nya tetap dibuat walau `HTTPSHandler` tidak pernah dipakai.
2. **Bersaing di GIL.** Pembuatan context adalah kerja CPU murni di dalam thread
   worker, jadi 50 thread tidak menambah throughput — mereka mengantre satu CPU.
   Plafon teoretis: `1 / 0,029 s ≈ 34 req/s`, berapa pun `--threads`-nya.
3. **Berulang 11.669×.** Satu run penuh = ±338 detik CPU hanya untuk memuat CA bundle
   yang sama, 11.669 kali.

Ini juga menjelaskan kenapa benchmark lama menemukan "peak di 50 thread lalu turun di
100": pada 50 thread, biaya CPU ini sudah jenuh; menambah thread hanya menambah
perebutan GIL.

**Perbaikan:** satu context terverifikasi + satu context tanpa verifikasi, dibuat sekali
per proses oleh `httpclient.tls_context()` (`gsa_seo_indexer/httpclient.py:70`).
Tempatnya di `httpclient`, bukan di `engine`, karena **tiga** pemakai harus berbagi
objek yang sama — kalau tidak, biaya CA bundle kembali muncul di jalur lain:

| Pemakai | Dulu | Sekarang |
|---|---|---|
| `Indexer._opener()` (jalur default) | `create_default_context()` per request | `engine._ssl_context()` → `tls_context()` |
| `_Pool` (connection pool, per proxy) | 2 context per pool/proxy | `tls_context(True/False)` |
| `proxies.py` (scrape & test proxy) | 2 context saat import + 1 per scrape | `tls_context(False)` |

Context bersifat read-only setelah dikonfigurasi, jadi aman dipakai bersama oleh semua
thread dan semua instance `Indexer`.

### Hasil terukur (server lokal, latensi ≈ 0, 400 request)

| Skenario | Sebelum | Sesudah | Perubahan |
|---|---|---|---|
| 16 thread | 43,8 req/s | **314,0 req/s** | **7,2×** |
| 1 thread | 30,9 req/s | **982,1 req/s** | **32×** |
| Median latensi (16 thread) | 229 ms | **6 ms** | 38× lebih rendah |
| Median latensi (1 thread) | 29 ms | **0 ms** | — |

> Angka ini **mengisolasi overhead klien** (server lokal menjawab seketika). Pada
> jaringan nyata, latensi server tetap mendominasi; yang hilang adalah ±42% dari satu
> core dan plafon 34 req/s. Karena itu **default `threads` perlu diukur ulang** setelah
> perubahan ini — angka "peak 50 thread" di `config.ini` diukur saat CPU masih menjadi
> plafon.

---

## 3. Akar masalah P0 kedua: klasifikasi body

`_classify_link()` menjalankan `re.finditer` atas seluruh body, lalu `_QUERY_ECHO_RE.sub`
(membuat salinan body) dan dua `.lower()` (dua salinan lagi) — untuk setiap respons,
termasuk halaman yang sama sekali tidak menyebut target. Terukur **1,90 ms** per body
245 kB; mayoritas respons adalah "miss".

**Perbaikan:** satu salinan lowercase + satu substring check sebagai gerbang cepat;
regex mahal hanya jalan untuk halaman yang benar-benar menyebut target
(`gsa_seo_indexer/engine.py:1225`). Hasil: **1,90 ms → 0,41 ms (4,6×)**, dengan 8 kasus
kebenaran (anchor, beda skema, host huruf besar, deep link, echo query, dsb.) tetap
lulus di `tests/test_engine.py`.

---

## 4. Bug efisiensi P0 ketiga: `max_sites`

`--sites` / "Max sites" seharusnya membatasi **jumlah situs**. Kenyataannya dipotong
dua kali:

- `cli.py` dan `gui.py` memotong daftar target ke N **entri** — padahal satu situs
  menyumbang dua entri (`?url=` dan bentuk path), jadi N entri = N/2 situs;
- `engine.run()` memotong **daftar pekerjaan** (`target × URL`) ke N lagi, membagi
  batas itu sekali lagi dengan jumlah URL.

Untuk 2 URL dan `--sites 4`: 8 pekerjaan dipotong jadi 4 → hanya **2 situs** yang
dikirimi, dan `stats.total = 8` sementara hanya 4 request berjalan (progress berhenti
di 50%).

**Perbaikan:** satu fungsi, satu arti — `cap_targets(targets, limit)`
(`engine.py:1345`) mengambil **N host pertama** dan **mempertahankan kedua bentuk URL**
dari setiap situs terpilih (bentuk fallback tetap dibutuhkan driver adaptif):

- `cli.py:342` dan `gui.py:748` memakai fungsi yang sama (tidak ada slice kedua);
- `Indexer._cap_targets()` dipakai `run()`, `run_batched()`, dan `run_adaptive()`;
- `stats.total` dihitung **setelah** pemotongan, jadi progress bisa mencapai 100%.

Bukti pada daftar asli (23.336 entri / 11.669 situs):

| `--sites` | Entri setelah cap | Situs unik | Request mode fast |
|---|---|---|---|
| 0 (semua) | 23.336 | 11.669 | 11.669 |
| 800 | 1.590 | **800** | **800** |
| 2 | 2 | 2 | 2 |

Sebelum perbaikan, `--sites 800` berhenti di 400 situs (dan 400 situs lagi dengan 2 URL).
Banner CLI/GUI juga sekarang melaporkan jumlah **situs unik**, bukan jumlah entri:
`Sites: 800 (1.590 submission targets)`.

### Verifikasi P0 (semua lulus)

```bash
python -m unittest discover -s tests -v     # 25 test
python tools/bench_local.py 400 16          # 315,4 req/s
python tools/bench_local.py 400 1           # 839,8 req/s
```

- **#1** — `test_contexts_are_built_once_per_kind`, `test_real_requests_never_rebuild_a_context`
  (30 submission nyata → maksimum 2 context dibuat), `test_pooled_client_shares_the_same_contexts`.
- **#2** — 11 kasus klasifikasi (anchor, beda skema, host huruf besar, deep link, query
  param, echo query, link relatif, host lain, input kosong) + fast reject pada body 245 kB.
- **#3** — `CapTargetsTest` + `MaxSitesCapTest` (mode biasa & adaptif, cap 0/1/2/4), plus
  invarian `stats.total == jumlah pekerjaan yang benar-benar dijalankan`.

---

## 5. P1 — selesai (dan dampak terukurnya)

### 5.1 Cek URL pra-run paralel

`engine.check_urls()` memeriksa target 8 sekaligus (`CHECK_WORKERS`), mengembalikan
hasil **sesuai urutan input** (supaya pesan `SKIP` di CLI tetap stabil) dan memanggil
`on_checked()` dalam urutan selesai (supaya GUI bisa menampilkan baris yang ditolak
sambil sisanya masih diperiksa). Dipakai CLI dan GUI.

| | Serial | Paralel (8 worker) |
|---|---|---|
| 10 URL, tiap cek 300 ms | 3,00 s | **0,60 s** (5,0×) |

### 5.2 Tabel GUI dibatasi 5.000 baris

`_insert_result_row()` berhenti menyisipkan baris setelah `MAX_TREE_ROWS = 5_000`;
sisanya hanya dihitung (`_hidden_rows`) dan ditampilkan di penghitung:
`5000/333800 rows (display capped at 5,000)`. Yang tidak berubah: `indexer.results`
tetap menyimpan **semua** baris untuk laporan/ekspor, dan filter/sort tetap bekerja
pada baris yang ditampilkan.

### 5.3 Hasil dikirim dalam urutan selesai (`as_completed`)

`pool.map()` menghasilkan hasil sesuai urutan pengiriman, jadi satu request yang
tertahan sampai timeout menahan `on_result()` untuk semua request di belakangnya.
Sekarang hasil dikonsumsi sesuai urutan **selesai** (`concurrent.futures.wait` dengan
`FIRST_COMPLETED`), dengan dua pengaman:

- **jendela in-flight = 4 × threads.** Mengirim 100 URL × 11k situs sekaligus berarti
  satu juta future di memori — persis masalah yang mau dihindari. Hanya ±200 future
  hidup pada 50 thread; sisanya masuk saat slot kosong.
- **Stop diperiksa tiap 0,5 s**, jadi tombol Stop bereaksi walau semua host sedang
  menunggu timeout, dan future yang belum mulai dibatalkan.

Test regresinya: satu target yang tidur 300 ms harus tiba **terakhir**; 200 pekerjaan
kecil tetap selesai semua; dan `stop()` pada 20 pekerjaan lambat tidak menguras antrean.

### 5.4 Laporan streaming + `Result` ber-`slots`

- JSON ditulis record-per-record (`[` … `]` tetap JSON valid, format tidak berubah).
- `@dataclass(slots=True)` pada `Result` menghapus `__dict__` per baris.

| Metrik | Sebelum | Sesudah |
|---|---|---|
| Peak memori tulis JSON, 100k baris | 28 MB | **0,3 MB** |
| Memori 100k `Result` | 15,2 MB | **10,4 MB** (−31%) |

### Verifikasi P1

```bash
python -m unittest discover -s tests -v     # 39 test
```

- `CheckUrlsTest` — paralel, urutan input, callback urutan selesai, exception → reject.
- `RowCapTest` (dengan stub tkinter) — cap 5.000, penghitung sisanya, `Result` utuh.
- `DeliveryOrderTest` — target lambat tiba terakhir; worker yang melempar exception
  dicatat sebagai `failed`, bukan mematikan run.
- `ReportTest` — JSON 25 baris valid, CSV header + N baris, `Result` tanpa `__dict__`.

---

## 6. P2 — selesai (dan dampak terukurnya)

### 6.1 Host yang sukses lalu mati akhirnya dianggap mati

`HostCache.record_failure()` dulu hanya menandai `alive = False` bila
`successes == 0`, jadi host yang pernah menjawab sekali tetapi kemudian mati
tetap dicoba **di setiap URL** selama run. Sekarang ada hitungan gagal
**berturut-turut** (`HostState.consecutive_failures`, ambang 2):

- host yang belum pernah menjawab → mati setelah 1 kegagalan (perilaku lama,
  supaya daftar legacy yang mayoritas mati tidak jadi dua kali lebih lambat);
- host yang pernah menjawab → toleran satu hiccup, mati setelah 2 kegagalan
  berturut-turut, dan **streak-nya ikut tersimpan** di `host_cache.json`
  (format v2; file v1 tetap terbaca).

| 1 host yang mati setelah sukses | Request sia-sia |
|---|---|
| Sebelum, 50 / 500 / 5.000 URL | 50 / 500 / **5.000** |
| Sesudah | **2 / 2 / 2** |

Diverifikasi end-to-end: run pertama menyimpan host mati ke `host_cache.json`,
proses berikutnya memulihkannya (`restored_dead: 1`) dan melewatinya
(`skipped_dead: 1`), sementara host hidup tetap dikirimi.

### 6.2 DNS cache: dibatasi, dipangkas, tanpa objek exception

- `DnsCache.prune()` membuang entri kedaluwarsa dan memangkas kelebihan ke
  `max_entries` (default 10.000). Pemangkasan berjalan otomatis tiap 512 lookup
  atau begitu tabel melewati batas, dan hanya menyentuh entri yang sudah selesai
  resolve sehingga thread yang sedang menunggu tidak kehilangan jawabannya.
- Kegagalan disimpan sebagai **tipe + pesan**, bukan objek exception: objek
  exception memegang traceback (dan semua frame di dalamnya) selama TTL negatif.
- `deactivate_dns_cache(cache)` hanya mematikan cache yang **masih aktif**, jadi
  `Indexer` yang selesai lebih dulu tidak mematikan resolver milik `Indexer` lain
  di proses yang sama.

```
20.000 host unik -> {'entries': 10000, 'evictions': 10000}
```

### 6.3 `connect timeout` akhirnya benar-benar dipakai

urllib menerima satu timeout untuk seluruh request (connect + TLS + read), dan
kode lama mengisinya dengan `max(connect_timeout, read_timeout)` — sehingga
`connect timeout = 4` tidak pernah berarti 4 detik. Sekarang ada
`_ConnectTimeoutHTTPConnection` / `_ConnectTimeoutHTTPSConnection` yang hanya
menukar timeout selama `connect()` (termasuk handshake TLS), lalu
mengembalikannya untuk fase baca. `--timeout` tetap menjadi knob tunggal:
ia mengisi fase baca **dan** menjadi batas atas fase connect.

| Host blackhole `10.255.255.1:81` | Waktu |
|---|---|
| Sebelum (satu timeout = max(4, 5)) | 5,03 s |
| Sesudah (connect 4 s / read 5 s) | **4,00 s** |
| Sesudah + `--timeout 1` | **1,00 s** |

Pada daftar legacy yang sebagian besar mati, 20% penghematan waktu tunggu per
host mati itu langsung terasa; yang lebih penting, opsinya kini tidak menipu.

> **Koreksi (verifikasi ulang 2026-11-06).** Fase *connect* memang memakai
> `connect_timeout`, tetapi fase *baca* masih memakai angka itu juga:
> `super().connect()` membuat socket saat `self.timeout` berisi connect timeout,
> dan mengembalikan `self.timeout` tidak mengubah timeout socket. Dengan nilai
> bawaan (`connect 4` / `read 5`) setiap halaman yang butuh 4–5 detik dicatat
> sebagai timeout. Sudah diperbaiki (socket di-set ulang setelah connect, di
> jalur urllib **dan** pooled client) dan dikunci `ReadTimeoutTest`. Rinciannya
> di §11.2.

### Verifikasi P2

```bash
python -m unittest discover -s tests -v     # 60 test
```

- `HostFailureStreakTest` — mati setelah 1 kegagalan (belum pernah sukses),
  toleran satu hiccup (pernah sukses), streak di-reset oleh sukses, persistensi
  v2, kompatibilitas file v1, dan TTL yang membuat host dicoba lagi.
- `DnsCacheTest` — memoise, prune entri kedaluwarsa, batas `max_entries`,
  kegagalan menyimpan tipe+pesan (tanpa objek exception), TTL negatif, dan
  aktivasi yang tidak saling menimpa.
- `ConnectTimeoutTest` + `ResolveTimeoutsTest` — timeout ditukar lalu
  dikembalikan, opener memakai handler baru, request memakai `read_timeout`,
  dan pemetaan `--timeout` / `--connect-timeout` / `--read-timeout`.

---

## 7. P3 — selesai (dan dampak terukurnya)

### 7.1 Satu bentuk request, satu kebijakan timeout/TLS

Tiga jalur dulu membangun header dan kebijakannya sendiri:

| | Sebelum | Sesudah |
|---|---|---|
| Header | `submit_one` mengirim Referer + Accept-Language, `submit_batch` tidak; `check_url` punya set sendiri | `_headers()` + `_request()` — satu set untuk ketiganya |
| Transport batch | `submit_batch` memanggil `_urlopen` langsung | lewat `_fetch()` seperti `submit_one`, jadi `--pool` dan timeout ikut berlaku |
| UA di pooled client | selalu `user_agents[0]` (rotasi diabaikan) | header yang sama dengan jalur urllib |
| Timeout/TLS | urllib vs `http.client` dengan aturan sendiri | kebijakan yang sama: connect/read terpisah, verify-first lalu fallback per host |

Dua transport tetap ada (urllib untuk default, `PooledHttpClient` opt-in untuk
keep-alive), tetapi keduanya sekarang menerima **header, timeout, dan kebijakan TLS
yang sama** dari satu tempat — perbedaan diam-diam antar-jalur itulah sumber bug yang
dihapus di sini.

### 7.2 `random_delay` default disamakan dengan config.ini

`Settings.random_delay` dulu `True` padahal `config.ini` menulis `random delay = 0`
dan pengukuran menunjukkan tidak ada manfaat. Sekarang default-nya `False`; API call
seperti `Indexer(Settings())` tidak lagi tidur 10–80 ms per request tanpa alasan.

### 7.3 Index service berbayar selalu memverifikasi sertifikat

Fallback tanpa verifikasi di `engine._urlopen` hanya untuk host ping dengan rantai
rusak. `IndexService.submit()` — yang mengirim **API key** — sekarang memakai
`tls_context(True)` secara eksplisit, dan test menegaskan `CERT_REQUIRED` dan
`check_hostname=True`, bukan sekadar "kebetulan default urllib".

### 7.4 Burst preflight dibatasi

`preflight` memakai `threads * 2` worker (100 socket pada default 50 thread).
Sekarang `MAX_PREFLIGHT_WORKERS = 64`, dan tetap `2 × threads` untuk run kecil
(4 thread → 8 worker).

### 7.5 CI + linter (dan satu bug yang tertangkap)

`.github/workflows/ci.yml`: matrix Python 3.10–3.13, `pip install -e .`, `ruff check .`,
`unittest discover`, dan smoke test CLI. Ruff dikonfigurasi di `pyproject.toml`
(`select = ["E4", "E7", "E9", "F"]`, dev extra, runtime tetap zero-dependency).

Pada pembersihan awal, ruff menemukan **bug nyata**: `emulator.do_GET` memakai variabel
`path` yang hanya didefinisikan di `do_POST` (F821), sehingga GET halaman root melempar
`NameError` begitu daftar service kosong. Sudah diperbaiki dan dikunci oleh
`tests/test_emulator.py` (2 test).

### Verifikasi P3

```bash
pip install -e ".[dev]"          # dev extra: ruff
ruff check .                     # All checks passed!
python -m unittest discover -s tests -v   # 92 test
python -m gsa_seo_indexer.cli --version
```

- `SharedRequestShapeTest` — `submit_one` dan `submit_batch` mengirim header identik,
  `check_url` mempertahankan Accept yang lebih sempit, pooled client menerima header
  yang sama (termasuk UA hasil rotasi).
- `PreflightWorkerCapTest`, `SettingsDefaultsTest` — cap worker dan default delay.
- `ServiceTlsTest` — context verifikasi dipatok + API key terkirim.
- `EmulatorRootPageTest` — halaman root emulator tanpa service.

---

## 8. Sisa peluang optimasi (audit 2026-11-06)

Diukur ulang di mesin yang sama dengan benchmark P0 (server lokal, tanpa
latensi jaringan). Angka "dampak" adalah penghematan CPU/waktu lokal, bukan
janji kecepatan internet. Kolom **Status** menunjuk ke §12 untuk yang sudah
dikerjakan.

| # | Temuan | Terukur sekarang | Potensi | Risiko | Status |
|---|---|---|---|---|---|
| 1 | `_opener()` dibangun **per request** (`build_opener` + 3 handler) | **163 µs/request** — 13% dari 1,2 ms/request di loopback | cache opener per `(proxy, verify)`: **+24% @1 thread** (934→1.161 req/s), setara di 16 thread | rendah (handler urllib tidak menyimpan state) | **selesai** (§12.1) |
| 2 | `_classify_link()` jalur "listed" menjalankan `_QUERY_ECHO_RE` atas **seluruh body** | **1,44 ms** per halaman 200 kB (anchor-hit 0,23 ms) | pecah alternasi jadi dua regex + cari di celah antar-match: **1,43 → 0,41 ms** (3,5×) | rendah — dengan harness ekuivalensi (280k kasus, 0 selisih) | **selesai** (§13) |
| 3 | Laporan JSON memakai `indent=1` dan `json.dump` per baris | **100k baris: 1,90 s, 17,7 MB** (CSV: 0,10 s, 5,8 MB) | compact + tulis per blok: **−35% waktu, −14% ukuran** (100k baris) | rendah (format berubah; pembaca JSON tetap kompatibel) | **selesai** (§12.2) |
| 4 | "Run enabled" ke index service berjalan **sekuensial** di satu thread | 48 service × 200 ms = **9,4 s** | 8 worker paralel: **1,2 s** (7,8×) | rendah–sedang (butuh pembatas + status per service di GUI) | **selesai** (§12.3) |
| 5 | `self.results` menahan semua baris sampai run selesai | **17,7 MB / 100k baris** (slots) | jendela 20k baris + tumpah ke disk: **400k baris puncak 17 MB** (datar), laporan identik | sedang — **selesai tanpa mengubah API**: `ResultStore` tetap list-like (`append`/`iter`/`len`) | **selesai** (§14) |
| 6 | Tidak ada `Accept-Encoding: gzip` di jalur urllib | body sampai 200 kB dikirim utuh | **120 kB -> 32,5 kB** di kabel (3,7x, halaman uji yang sengaja sulit dikompresi) | rendah — opt-in `--gzip`, plus dekode berplafon | **selesai** (§16) |
| 7 | 429/503 hanya dicatat `failed`, `Retry-After` diabaikan | satu baris hilang per kejadian | **503 → 200 jadi 2 request dan `submitted`**; `Retry-After` dihormati sampai plafon | rendah — opt-in `--retries 0` default, plafon `retry_max_wait` | **selesai** (§17) |
| 8 | `PingTarget(mode="post")` tidak pernah mengirim body | `submit_one` selalu GET, jadi target POST mengirim **nol** URL | kini form body sungguhan di kedua transport; perilaku GET tidak berubah | rendah (default tetap `append`/`query`, 15 test baru) | **selesai** (§15) |
| 11 | Concurrency terbatas jumlah thread (satu stack per request) | — (bukan penghambat di loopback) | `--transport asyncio`: ribuan request in-flight tanpa thread | sedang — jalur baru; batch masih thread | **selesai** (§18) |
| 12 | Paket tidak siap rilis: `assets/app.ico` tidak ikut wheel, tidak ada workflow publish | `pip install` tanpa ikon tray | wheel lengkap + rilis tag dengan trusted publishing | rendah | **selesai** (§19) |
| 13 | Tidak ada connection pooling di event loop (satu koneksi per request) | 20 request ke 1 host = 20 koneksi | **1 koneksi**, 1,6x (fixture) sampai 3,5x (server ber-NODELAY) lebih cepat | rendah — opt-in `--pool`, sama seperti jalur thread | **selesai** (§20) |
| 14 | Transport asyncio tidak mendukung proxy | 429 situs di balik proxy tetap memakai thread | HTTP lewat request line absolut, HTTPS lewat **CONNECT** + TLS; tetap di loop | sedang — tunnel diuji dengan sertifikat self-signed | **selesai** (§21) |
| 9 | `Gui._poll()` menguras queue tanpa batas per tick | — (tkinter tidak ada di sandbox) | batasi ±500 baris/tick agar jendela tetap responsif saat burst | rendah | **selesai** (§12.4) |
| 10 | `UrlWatcher._seen` tumbuh tanpa batas pada watch panjang | — | LRU/batas ukuran | rendah | **selesai** (§12.4) |

Diukur dan **tidak** layak dikerjakan (supaya tidak diulang):

- `with self._lock` per hasil di `run()`: overhead **0,08 s / 500k baris** (0,16 µs
  per baris) — batching lock tidak sepadan dengan risikonya.
- `check_url` men-`decode`+`lower()` 120 kB hanya untuk mencari `noindex`:
  **0,08 ms**, sama dengan pencarian di bytes — bukan target.
- `_headers()` (0,4 µs), `host_of()` (2,5 µs), `Result()` (0,6 µs) — sudah murah.
- `TCP_NODELAY` sudah di-set oleh `http.client` (dan warisan `super().connect()`
  kita mempertahankannya), jadi tidak ada penundaan Nagle yang tertinggal.

Ide besar yang tetap di luar cakupan: **transport asyncio** untuk run 10.000+
target (satu event loop menggantikan 50 thread) dan **publikasi PyPI** dengan
workflow rilis dari tag.

---

## 9. Yang sudah dikerjakan di branch ini

| File | Perubahan |
|---|---|
| `gsa_seo_indexer/httpclient.py` | `tls_context()`: satu context terverifikasi + satu tanpa verifikasi per proses; dipakai juga oleh `_Pool` |
| `gsa_seo_indexer/engine.py` | `_ssl_context()` mendelegasikan ke `tls_context()` (tidak ada context per request) |
| `gsa_seo_indexer/proxies.py` | memakai `tls_context(False)`; tidak lagi membuat context sendiri |
| `gsa_seo_indexer/engine.py` | gerbang cepat lowercase + substring di `_classify_link()` (P0 #2) |
| `gsa_seo_indexer/engine.py` | `cap_targets()`: batas = N host, kedua bentuk URL dipertahankan, `stats.total` akurat (P0 #3) |
| `gsa_seo_indexer/cli.py`, `gui.py` | tidak lagi memotong daftar sendiri; banner melaporkan situs unik |
| `tools/bench_local.py` | benchmark offline: server lokal, N request, req/s + median/p95; backlog 1024 + laporan request yang gagal |
| `gsa_seo_indexer/engine.py` | `check_urls()`: cek target paralel, urutan hasil stabil (P1 #1) |
| `gsa_seo_indexer/cli.py`, `gui.py` | memakai `check_urls()`; GUI menampilkan penolakan saat ditemukan |
| `gsa_seo_indexer/gui.py` | `MAX_TREE_ROWS = 5_000`: tabel dibatasi, sisa baris dihitung (P1 #2) |
| `gsa_seo_indexer/engine.py` | `as_completed()` + `cancel()`: tidak ada head-of-line blocking (P1 #3) |
| `gsa_seo_indexer/engine.py` | laporan JSON streaming + `Result(slots=True)` (P1 #4) |
| `gsa_seo_indexer/perf.py` | `HostCache`: ambang gagal berturut-turut + persistensi v2 (P2 #1) |
| `gsa_seo_indexer/perf.py` | `DnsCache`: prune + `max_entries`, error tanpa objek exception, deaktivasi aman (P2 #2) |
| `gsa_seo_indexer/engine.py` | `_ConnectTimeout*Connection/Handler`: connect timeout sungguhan (P2 #3) |
| `gsa_seo_indexer/cli.py` | `resolve_timeouts()`: `--timeout` mengisi read dan membatasi connect |
| `gsa_seo_indexer/engine.py` | `_headers()`/`_request()`: satu bentuk request untuk submit_one, submit_batch, check_url (P3 #1) |
| `gsa_seo_indexer/engine.py` | `submit_batch` lewat `_fetch` (pool + timeout berlaku); pooled client menerima header yang sama |
| `gsa_seo_indexer/engine.py` | `random_delay` default `False`, `MAX_PREFLIGHT_WORKERS = 64` (P3 #2/#4) |
| `gsa_seo_indexer/services.py` | `submit()` memakai `tls_context(True)` — API key tidak pernah lewat koneksi tanpa verifikasi (P3 #3) |
| `gsa_seo_indexer/emulator.py` | `do_GET` mendefinisikan `path` (F821 yang ditemukan ruff) |
| `.github/workflows/ci.yml`, `pyproject.toml` | CI matrix 3.10–3.13, `ruff check`, dev extra (P3 #5) |
| `gsa_seo_indexer/engine.py`, `httpclient.py` | **fix P4**: timeout socket di-set ulang ke `read_timeout` setelah connect (urllib dan pooled) — read timeout sebelumnya tertimpa connect timeout |
| `tools/verify_all.py` | skrip verifikasi ulang 34 pemeriksaan P0–P14, offline, `--only p0..p14` |
| `gsa_seo_indexer/engine.py` | `_opener()` di-cache per (proxy, verify) — 200 request = 1 build (P4 #1) |
| `gsa_seo_indexer/engine.py` | laporan JSON compact, ditulis per 512 baris (`_dump_row`) (P4 #2) |
| `gsa_seo_indexer/services.py` | `submit_many()`: 8 service paralel, urutan hasil tetap, bisa di-Stop (P4 #3) |
| `gsa_seo_indexer/gui.py` | "run enabled" memakai `submit_many`; `MAX_DRAIN_PER_TICK = 400` (P4 #3/#4) |
| `gsa_seo_indexer/cli.py` | `services --run-enabled --workers` — jalur paralel yang sama, headless |
| `gsa_seo_indexer/watcher.py` | `_seen` dibatasi `max_seen` (deque + set) (P4 #4) |
| `tests/test_watcher.py` | 5 test: baris baru, rotasi file, batas `_seen`, folder |
| `gsa_seo_indexer/engine.py` | `_QUERY_RUN_RE` + `_QUOTED_URL_RE` dan pencarian di celah antar-match: keputusan "listed" 3,5× lebih cepat, hasil identik (P5) |
| `tools/classify_diff.py` | harness ekuivalensi: implementasi lama disimpan sebagai referensi, korpus 12+ kasus tetap dan generator acak, mode `--only-timing` |
| `tests/test_classify_equiv.py` | 8 test: fuzz via harness, korpus tetap, dan tiap bentuk yang menentukan (echo, teks, anchor, query) |
| `gsa_seo_indexer/resultstore.py` | `ResultStore`: jendela `max_memory` baris di RAM, sisanya tumpah ke satu file pickle sementara (urut, thread-safe), `clear()`/`close()` menghapus file (P6) |
| `gsa_seo_indexer/engine.py` | `Settings.max_results_in_memory = 20_000`; `Indexer.results` memakai `ResultStore` (pack/unpack tuple); `RunStats.indexable`/`listed` dihitung saat hasil masuk (P6) |
| `gsa_seo_indexer/cli.py` | ringkasan memakai `stats.indexable`/`stats.listed`, bukan memindai seluruh `indexer.results` (yang kini bisa ada di disk) (P6) |
| `gsa_seo_indexer/httpclient.py` | `PooledHttpClient.request(method, url, body=…)`: jalur pool tidak lagi GET-only; `get()` tetap jadi pembungkus tipis, redirect 301/302/303 → GET, 307/308 → metode+body diulang (P7) |
| `gsa_seo_indexer/engine.py` | `PingTarget.body` (default `url={enc}`), `http_method`, `post_body()`; `_request()`/`_fetch()` menerima `method`/`data`; `submit_one`/`submit_batch` mengirim POST untuk target `post`; `mode` dari JSON dinormalkan (P7) |
| `gsa_seo_indexer/httpclient.py` | `decode_content()`: gzip/x-gzip/deflate, teks polos berheader gzip, stream terpotong, plafon hasil dekompresi (P8) |
| `gsa_seo_indexer/engine.py` | `Settings.accept_gzip` + `_read_body()` dipakai urllib dan `check_url`; pool mendekode setelah header (P8) |
| `gsa_seo_indexer/engine.py` | `Settings.retries` / `retry_max_wait`, `retry_after_seconds()`, `retry_delay()`, `_submit_attempt()` dan loop di `submit_one()`, tidur lewat `Event.wait` (P9) |
| `gsa_seo_indexer/asyncio_transport.py` | transport event loop: HTTP/1.1 minimal (Content-Length/chunked/EOF), redirect, TLS verify-first + fallback, gzip, POST, retry, semaphore; `run_work()` (P10) |
| `gsa_seo_indexer/engine.py` | `Settings.transport`, `Indexer._record()` sebagai satu-satunya pemilik statistik, `transport_fallback` (P10) |
| `tools/check_package.py` | bangun wheel offline lalu periksa data, entry point, METADATA, dan workflow rilis (P11) |
| `.github/workflows/release.yml` | rilis dari tag: build, `twine check --strict`, cek isi wheel, smoke CLI, upload artifact, publish dengan trusted publishing (P11) |
| `.github/workflows/ci.yml` | job `package`: wheel dibangun dan CLI diuji dari site-packages (P11) |
| `gsa_seo_indexer/asyncio_transport.py` | `_Pool` (kunci scheme/host/port/proxy, batas per host/total, idle TTL, `made`/`reused`), keep-alive + retry satu kali untuk koneksi basi (P12) |
| `gsa_seo_indexer/asyncio_transport.py` | `parse_proxy()`, tunnel `CONNECT` (socket di executor + `loop.sock_*`, lalu TLS di atasnya), request line absolut untuk HTTP, retire proxy yang gagal (P13) |
| `gsa_seo_indexer/engine.py` | `use_loop` tidak lagi mensyaratkan tanpa proxy; `transport_fallback = "batch -> threads"` (P13) |
| `tests/test_asyncio_net.py` | 10 test: pool 1 vs 20 koneksi, koneksi basi, batas per host, POST di pool, proxy absolut, CONNECT+TLS, proxy mati, validasi alamat |
| `tests/test_gzip.py` | 12 test: keputusan identik, kedua transport, server yang berbohong, stream terpotong, plafon, noindex |
| `tests/test_retry.py` | 18 test: parsing `Retry-After`, plafon, exhausted, kedua transport, Stop saat backoff |
| `tests/test_asyncio_transport.py` | 14 test: paritas baris/statistik, POST, gzip, redirect, chunked/EOF, host mati, cap koneksi, Stop, HTTPS self-signed |
| `tests/test_packaging.py` | 9 test: metadata, package-data, versi, workflow rilis, build wheel sungguhan |
| `tests/test_post_target.py` | 15 test: body form, GET tidak berubah (tanpa body/Content-Type), kedua transport, 303 jadi GET, 307 mengulang body, batch satu body, normalisasi loader |
| `tests/test_resultstore.py` | 8 test: jendela tanpa disk, tumpahan menjaga urutan, `clear()`/`close()`, pack/unpack, dan integrasi engine (laporan + ringkasan identik) |
| `tests/test_engine.py` | 57 test: P0/P1/P2/P3 + `ReadTimeoutTest`, `OpenerCacheTest`, `ReportFormatTest` |
| `tests/test_gui_rows.py` | 3 test: cap baris tabel GUI (dengan stub tkinter) |
| `tests/test_perf_cache.py` | 12 test: streak host, prune/batas DNS, aktivasi cache |
| `tests/test_services.py` | 8 test: TLS verifikasi + API key, `submit_many` paralel (5), emulator e2e (1) |
| `tests/test_emulator.py` | 2 test: halaman root emulator tanpa service |
| `tests/test_cli_timeouts.py` | 5 test: pemetaan opsi timeout CLI |

## 10. Cara mengukur ulang

```bash
# apa yang dijalankan CI (matrix 3.10-3.13)
pip install -e ".[dev]"
ruff check .
python -m unittest discover -s tests -v

# 400 request lewat 16 thread, server lokal tanpa latensi
python tools/bench_local.py 400 16

# benar-benar memisahkan overhead klien: 1 thread
python tools/bench_local.py 400 1

# verifikasi ulang semua klaim P0-P14 sekaligus (offline, ±59 s)
python tools/verify_all.py

# ekuivalensi klasifikasi: implementasi lama vs sekarang, 40k kasus acak
python tools/classify_diff.py --fuzz 40000 --seed 42

# test regresi (tanpa dependency tambahan)
python -m unittest discover -s tests -v

# per modul
python -m unittest tests.test_engine -v       # engine + transport
python -m unittest tests.test_perf_cache -v   # host cache + DNS cache
python -m unittest tests.test_gui_rows -v     # cap tabel GUI (stub tkinter)
python -m unittest tests.test_cli_timeouts -v # pemetaan opsi timeout
python -m unittest tests.test_resultstore -v  # tumpahan hasil ke disk
python -m unittest tests.test_post_target -v  # target POST + redirect
python -m unittest tests.test_gzip -v         # gzip + decode
python -m unittest tests.test_retry -v        # retry 429/503
python -m unittest tests.test_asyncio_transport -v  # transport event loop
python -m unittest tests.test_asyncio_net -v  # pool + proxy di event loop
python tools/verify_all.py --only p14          # versi rilis konsisten
python -m unittest tests.test_packaging -v    # wheel + workflow rilis

# bangun wheel dan periksa isinya (dipakai CI dan verify_all P11)
python tools/check_package.py
python -m unittest tests.test_engine.ReadTimeoutTest -v  # connect vs read timeout
```

Untuk mengukur dampak di jaringan nyata, jalankan run yang sama dua kali dengan build
sebelum/sesudah dan bandingkan `Submitted`, `Backlinks`, dan waktu total — jangan
mengandalkan angka benchmark lokal sebagai klaim kecepatan internet.

---

## 11. Verifikasi ulang P0–P3 (2026-11-06)

Semua klaim P0–P3 diukur ulang dari nol oleh satu skrip, bukan dari catatan
tahap sebelumnya:

```bash
python tools/verify_all.py            # 34 pemeriksaan P0-P14 (±59 s)
python tools/verify_all.py --only p2  # satu fase
```

```
[PASS] P0 TLS context dibagi per proses      identitas sama=True, 0 context baru / 30 opener
[PASS] P0 Throughput 16 thread               400 req / 16 thread = 317.5 req/s
[PASS] P0 Throughput 1 thread                400 req / 1 thread = 951.5 req/s
[PASS] P0 Classifier menolak cepat           200 kB tanpa target = 0.239 ms/panggilan
[PASS] P0 Batas situs dihitung sekali        800 situs -> 800 host / 1600 target; run 3x2 = 6
[PASS] P1 check_urls paralel                 10 URL x 300 ms = 0.61 s
[PASS] P1 Laporan JSON streaming             5000 baris: puncak 0.36 MB (cara lama 8.7 MB)
[PASS] P1 Result ber-slots                   100k baris: 17.7 MB vs 22.5 MB (-21%)
[PASS] P1 Batas baris GUI                    MAX_TREE_ROWS = 5000
[PASS] P2 Host mati setelah sukses           2 percobaan dari 5000 URL, 4998 di-skip
[PASS] P2 Plafon cache DNS                   5000 host, batas 1000: 1000 entri, 4000 eviction
[PASS] P2 Connect timeout dipakai            connect 0.5 -> 0.50 s, 2.0 -> 2.00 s
[PASS] P2 Read timeout tidak tertimpa        halaman 0,5 s: connect 0,1 s -> submitted; read 0,2 s -> failed 0,20 s
[PASS] P2 Host cache bertahan antar proses   1 host mati ditulis/dibaca (v2)
[PASS] P3 Satu bentuk header                 submit_one/batch/check_url/pooled: 4/4 key
[PASS] P3 Cap worker preflight               4 -> 8, 50 -> 64, 100 -> 64
[PASS] P3 Default random_delay               random_delay=False
[PASS] P3 TLS index service                  verify_mode=CERT_REQUIRED, check_hostname=True
[PASS] P3 Halaman root emulator              GET / -> 200, GET /no/such/service -> 200
[PASS] P3 ruff + test + CI                   ruff bersih; 92 test OK; CI matrix 3.10-3.13

20/20 PASS dalam 16.2 s
```

### 11.1 Yang berubah dari angka tahap sebelumnya

| Klaim | Angka saat fase | Angka sekarang | Catatan |
|---|---|---|---|
| Throughput 16 thread | 315,4 req/s | 317,5 req/s | sama (variasi mesin) |
| Throughput 1 thread | 839,8 req/s | 951,5 req/s | sama |
| Classifier (200 kB, miss) | 0,41 ms | 0,24 ms | sama, lebih cepat |
| `check_urls` 10 × 300 ms | 0,60 s | 0,61 s | sama |
| JSON peak 5.000 baris | 0,3 MB | 0,36 MB | sama (cara lama 8,7 MB) |
| `Result` 100k | 15,2→10,4 MB | 17,7 MB vs 22,5 MB (−21%) | baseline tanpa-slots di pengukuran ini berbeda; rasionya yang penting |
| Connect timeout | 4,00 s | 4,00 s / 0,50 s | sama |
| Jumlah test | 71 | **75** | +4 test `ReadTimeoutTest` |

### 11.2 Bug yang ditemukan justru saat verifikasi ulang

**`read timeout` tidak pernah berlaku.** Tes P2 (`ConnectTimeoutTest`) hanya
memeriksa `conn.timeout`, bukan timeout pada socket. Kenyataannya:

```
setelah connect: self.timeout=8.0, sock.timeout=0.5   # 0.5 = connect timeout
```

| Skenario (server menjawab 0,5–1,5 s) | Sebelum perbaikan | Sesudah |
|---|---|---|
| urllib, connect 0,2 s / read 8 s | **failed 0,23 s** (`timed out`) | **submitted 1,53 s** |
| pooled client, connect 0,2 s / read 8 s | **failed 0,60 s** | **submitted 1,50 s** |
| urllib, connect 5 s / read 0,4 s | submitted 3,00 s (read diabaikan) | **failed 0,40 s** |

Dampak nyata: dengan `config.ini` bawaan (`connect timeout = 4`,
`read timeout = 5`) setiap situs yang menjawab 4–5 detik dicatat gagal —
padahal profil P2 sendiri mencatat 42% request sukses butuh lebih dari 3 detik.
Perbaikannya tiga baris (set timeout socket setelah connect di kedua subclass
dan di `PooledHttpClient._get_once`), dikunci `ReadTimeoutTest` (4 test) dan
pemeriksaan `P2 Read timeout tidak tertimpa` di `tools/verify_all.py`.

---

## 12. P4 — selesai (empat optimasi dari audit §8)

Dikerjakan setelah verifikasi ulang: hanya item yang **aman** dan **terukur**.
Yang belum masuk daftar ini punya alasan eksplisit di §8 (kolom Status).

### 12.1 Opener urllib dibangun sekali per kebijakan

`urllib.request.build_opener()` + tiga handler = **163 µs**, dan itu dibayar
**per request** — 13% dari total waktu request di loopback. Opener tidak
menyimpan state (setiap `open()` membuat koneksi sendiri), jadi satu opener per
`(proxy, verify, redirect, connect_timeout)` sudah cukup.

| | Sebelum | Sesudah |
|---|---|---|
| Build opener untuk 200 request | 200 kali | **1 kali** |
| Throughput 1 thread (`bench_local.py 400 1`, median 3 run) | 844 req/s | **1.140 req/s** (+35%) |
| Throughput 16 thread (median 3 run) | 317 req/s | 317 req/s (setara — di 16 thread bottleneck-nya server/GIL, bukan CPU klien) |
| Cache | — | maks 32 entri FIFO (`MAX_CACHED_OPENERS`); dikosongkan oleh `apply_settings()` dan `close()` |

Build dilakukan **di dalam lock**: melepas lock lebih dulu membuat 50 thread
yang baru start membangun 50 opener identik. Miss hanya terjadi untuk pasangan
(proxy, verify) baru, jadi lock tidak diperebutkan di kondisi normal.

### 12.2 Laporan JSON compact dan ditulis per blok

`indent=1` untuk file yang dibaca mesin, plus `json.dump` dua kali per baris.

| 100k baris | Sebelum | Sesudah |
|---|---|---|
| Waktu | 1,90 s | **1,24 s** (−35%) |
| Ukuran | 17,7 MB | **15,19 MB** (−14%) |
| Puncak memori (5k baris, dari `verify_all`) | 0,36 MB | 0,36 MB (tetap) |

Sekarang `separators=(",", ":")` dan satu `write()` per 512 baris
(`_REPORT_BLOCK`). Isi file tetap sama persis dengan `[asdict(r) …]`
(`ReportFormatTest` membandingkan keduanya).

### 12.3 Index service dijalankan paralel

GUI "run enabled" mengirim ke setiap service **berurutan** dalam satu thread;
`services.submit_many()` sekarang memakai 8 worker (`DEFAULT_SERVICE_WORKERS`),
mengembalikan hasil **sesuai urutan service**, meneruskan progres per service,
dan berhenti kalau Stop ditekan.

| 48 service, latensi 200 ms | Sebelum | Sesudah |
|---|---|---|
| Total waktu | 9,4 s | **1,2 s** (7,8×) |

CLI mendapat paritas: `gsa-seo-indexer services --run-enabled --url …`
(`--workers` untuk mengubah batasnya) — jalur yang sama, bisa dipakai headless.

### 12.4 Batas antrean GUI dan memori watcher

- `Gui._poll()` dulu menguras queue sampai kosong; saat burst, jendela tidak
  sempat menggambar. Sekarang maks **400 baris per tick**
  (`MAX_DRAIN_PER_TICK`), lalu langsung menjadwal tick berikutnya.
- `UrlWatcher._seen` tumbuh seumur proses. Sekarang dibatasi `max_seen`
  (bawaan 50.000) dan membuang yang tertua lebih dulu (`deque` + `set`).
  URL yang sudah dibuang hanya bisa muncul lagi kalau log benar-benar
  mengulang barisnya, dan log append-only tidak melakukannya.

### Catatan alat ukur

`tools/bench_local.py` memakai server Python dengan satu loop accept. Dengan
backlog 256 dan 50 thread, server itu sendiri menjadi bottleneck: ~1% request
habis timeout dan angka throughput jadi menyesatkan (325 req/s pada 1000
request/50 thread). Backlog dinaikkan ke 1024 dan skrip sekarang melaporkan
berapa request yang tidak selesai, supaya angka 50-thread tidak dibaca sebagai
biaya klien.

### Verifikasi P4

```bash
python -m unittest discover -s tests -v    # 92 test
python tools/verify_all.py --only p4       # 4 pemeriksaan
python tools/verify_all.py                 # 33 pemeriksaan semua fase
python tools/bench_local.py 400 1          # +24% vs commit sebelumnya
```

- `OpenerCacheTest` (5) — identitas per kebijakan, proxy punya opener sendiri,
  cache dibatasi, **hanya satu build di bawah 8 thread**, cache gugur saat
  `apply_settings()`.
- `ReportFormatTest` — JSON compact sama persis dengan `asdict` dan tanpa
  indentasi.
- `SubmitManyTest` (5) + `SubmitManyAgainstEmulatorTest` (1) — paralel 7,8×,
  urutan tetap, satu service error tidak menghentikan yang lain, Stop
  melewati sisa antrean, dan jalur HTTP nyata ke emulator.
- `UrlWatcherTest` (5) — baris baru saja, komentar/kosong diabaikan, file
  terpotong dibaca dari awal, `_seen` dibatasi, folder diperluas.

---

## 13. P5 — klasifikasi lebih murah tanpa mengubah satu pun keputusan

`_classify_link()` memutuskan "listed" dengan cara menghapus semua *echo* lebih
dulu (`_QUERY_ECHO_RE.sub`) lalu mencari target di sisa teks. Pada halaman
165 kB langkah itu memakan **0,86 ms**, dan ia dijalankan untuk setiap halaman
yang menyebut target.

### 13.1 Yang dicoba lebih dulu, dan gagal

Rencana awal: periksa setiap kemunculan secara lokal (jendela ±200 karakter)
alih-alih memindai seluruh body. Terukur 0,03 ms — tapi harness pembanding
langsung membuktikan itu **tidak setara**:

| Percobaan | Selisih vs implementasi lama |
|---|---|
| Aturan "run sebelum kemunculan dimulai dengan `?`" | 828 dari 5.000 kasus |
| + aturan nilai `="https://…"`, skema case-sensitive | 175 kasus |
| + `?` di mana pun di dalam run yang sama | 126 kasus |

Penyebabnya dua hal yang tidak terlihat dari membaca kode:

1. `=\s*["'](https?://[^"']*)["']` memakai `[^"']*`, jadi satu match bisa
   **melintasi spasi, `<`, dan `>`** sampai kutip berikutnya - jauh lebih jauh
   dari jendela mana pun.
2. Match saling memakan: `="http://a <a href='http://b'` membuat alt2 pertama
   menelan `href=` sehingga alt2 kedua tidak pernah jalan.

Heuristik lokal dibuang. Yang dipakai sekarang **algoritma yang setara secara
konstruksi**, bukan perkiraan.

### 13.2 Temuan sebenarnya: alternasinya yang mahal

| 165 kB | Waktu |
|---|---|
| `_QUERY_ECHO_RE.sub()` (satu alternasi, kode lama) | 0,86 ms |
| `\?[^"'<>\s]*` saja | **0,06 ms** |
| `=\s*["']https?://[^"']*["']` saja | **0,13 ms** |
| keduanya berurutan | 0,19 ms |

Alternasi `A\|B` mematikan optimasi pencarian literal di mesin regex, jadi dua
pola terpisah **4,5× lebih cepat** daripada satu pola gabungan.

### 13.3 Algoritma: cari di celah antar-match

Menghapus echo lalu mencari target di sisa teks sama artinya dengan mencari
target di **celah di antara match**, tanpa membuat salinan body:

1. kumpulkan match `_QUERY_RUN_RE` (alt1);
2. kumpulkan match `_QUOTED_URL_RE` (alt2) **pada teks asli**, lalu buang yang
   `=`-nya jatuh di dalam salah satu match alt1 - persis kondisi di mana
   pass tunggal lama sudah menelan `=` itu (kasus `<a href= ?url="http://…">`);
3. gabungkan, urutkan, dan cari needle di setiap celah dengan `str.find`;
   berhenti pada kecocokan pertama.

Karena `https?://` peka huruf besar-kecil, regex tetap dijalankan pada teks
asli sementara pencarian memakai salinan `lower()`; keduanya hanya ditukar
ketika `len(lower) == len(body)` (beberapa kodepoint memanjang saat
di-lower-case) - kalau tidak, jalur lama dipakai apa adanya. Payload yang
mengandung spasi juga memakai jalur lama, karena `sub(" ")` bisa menyambung
teks di batas match.

### 13.4 Bukti ekuivalensi, bukan klaim

`tools/classify_diff.py` menyimpan implementasi lama **apa adanya** sebagai
referensi dan membandingkan hasil keduanya:

```bash
python tools/classify_diff.py --fuzz 40000 --seed 42
```

- 7 seed × 40.000 kasus acak + 22 kasus tetap = **280.014 perbandingan, 0 selisih**.
- Korpusnya sengaja jahat: kutip tidak tertutup, kutip campur (`="…'`), query
  sebelum atribut, skema huruf besar, `?` di dalam nilai, run 3.000 karakter,
  target di awal body, dan target diulang 100 kali di dalam echo.
- Harness-nya terbukti bergigi: implementasi "sub dua kali" yang naif
  (ALT1 lalu ALT2) **tertangkap** oleh dua kasus tetap (`<a href= ?url="…">`).

### 13.5 Dampak terukur

| | Sebelum | Sesudah |
|---|---|---|
| Halaman 165 kB, keputusan "listed" | 1,43 ms | **0,41 ms** (3,5×) |
| Halaman 165 kB, echo saja | 1,66 ms | **0,43 ms** (3,8×) |
| End-to-end, 400 request / 8 thread, halaman 164 kB "listed" | 325-335 req/s | **570-611 req/s** (+78%) |
| End-to-end, halaman 164 kB echo | 300-340 req/s | **586-624 req/s** (+85%) |
| Klasifikasi yang dihasilkan | `listed=400` | `listed=400` (identik) |

### 13.6 Test

```bash
python -m unittest discover -s tests -v      # 100 test (+8)
python tools/verify_all.py --only p5         # 1 pemeriksaan
```

- `FuzzEquivalenceTest` — menjalankan harness (3.000 kasus, seed 7) lewat
  subprocess dan membandingkan seluruh korpus tetap dengan referensi.
- `ListingDecisionTest` — tiap bentuk yang menentukan satu per satu: echo saja
  (bukan listed), teks terlihat, query sebelum atribut (tetap listed), anchor
  asli (indexed), target di awal body, dan payload ber-query string yang
  memang tidak pernah "listed" oleh kedua implementasi.

---

## 14. P6 — log hasil berbatas, laporan tetap identik

Laporan baru ditulis setelah run selesai, jadi setiap baris harus tetap ada
sampai saat itu. Pada run 50 URL x 11.000 situs (±660.000 baris) itu berarti
**±115 MB** RAM yang ditahan dari awal sampai akhir — persis di run terbesar
yang justru paling sering dipakai.

### 14.1 Bentuk solusinya

`self.results` tetap dipakai apa adanya oleh `write_report`, GUI, dan test,
karena `ResultStore` berperilaku seperti list: `append`, `iter`, `len`, `bool`,
`[index]`, `clear`. Bedanya hanya di penyimpanan:

1. baris masuk ke jendela RAM (`max_results_in_memory = 20_000`);
2. saat jendela penuh, isinya dipickle berurutan ke **satu** file sementara
   (`tempfile.mkstemp(prefix="gsa-results-")`) lalu jendela dikosongkan;
3. iterasi memutar ulang isi file lebih dulu, lalu jendela — urutannya sama
   dengan list biasa;
4. `clear()` (dipanggil di setiap awal run) dan `close()` menutup file dan
   menghapusnya.

Amannya kecil: `max_results_in_memory = 0` mengembalikan perilaku lama (semua
di RAM), dan run di bawah 20.000 baris **tidak menyentuh disk sama sekali**.

### 14.2 Biaya yang tidak kelihatan: pickle per baris

Percobaan pertama menyimpan objek `Result` langsung. Hasil mikrobrench 500k
baris: **3,19 µs/baris** dan file 19,1 MB. Yang mahal bukan I/O-nya, tapi
pickle dataclass-nya. Karena itu baris dipak ke tuple delapan field sebelum
dipickle, dan dibangun ulang jadi `Result` saat dibaca:

| Per baris (500k baris) | Dataclass | Tuple |
|---|---|---|
| Waktu tumpah | 3,19 µs | **0,31 µs** |
| Ukuran file | 19,1 MB | **13,9 MB** |

`_pack_result()` menulis atribut satu per satu; versi `getattr` dalam loop
terukur **0,79 µs** vs **0,30 µs** per baris, jadi loop itu dibuang.

### 14.3 Dampak terukur

Diukur dengan `tracemalloc`, hanya bagian *append* (pembangunan baris tidak
ikut dihitung, kalau tidak yang terukur cuma biaya membuat `Result`):

| 400.000 baris | List lama | `ResultStore` |
|---|---|---|
| Puncak memori | **101 MB** | **17 MB** (5,8x lebih kecil) |
| Puncak saat 50k baris | - | 17 MB |
| Puncak saat 400k baris | - | **17 MB** (datar) |
| File tumpahan | - | 38 MB (20 spill), dihapus saat `close()` |
| Overhead append | - | **+0,82 µs/baris** (500k baris = 0,41 s) |
| Membaca ulang untuk laporan | - | 0,47-0,52 µs/baris (500k baris ± 0,25 s) |

Angka pentingnya bukan "5,8x" (rasio itu ikut naik kalau run-nya lebih besar:
500.000 baris terukur **125,0 MB vs 10,0 MB, 12,5x**), melainkan kolom
**datar**: puncak memori 50k dan 400k baris sama-sama 17 MB, karena yang
ditahan hanya jendela 20.000 baris. Selisih 10 MB vs 17 MB bukan regresi: yang
terukur 10 MB adalah puncak jendela saja, sedangkan 17 MB termasuk batch tuple
sementara saat menumpah 20.000 baris sekaligus.

Waktu append (0,82 µs/baris) diukur **tanpa** `tracemalloc` aktif; dengan
tracing menyala, angka yang sama terlihat seperti 7,4 µs/baris karena setiap
alokasi dilacak - itu biaya alat ukurnya, bukan biaya kode.

### 14.4 Yang dijamin sama, dan bagaimana dibuktikan

- **Isi laporan**: `write_report` tidak diubah, ia tetap meng-iterasi
  `self.results`. `ResultStoreTest` membandingkan isi laporan dengan cara lama
  baris per baris (diurutkan berdasarkan `(target, url, status)`, karena
  urutan selesai request tidak deterministik).
- **Angka ringkasan**: CLI dulu menghitung `links`/`listed` dengan memindai
  seluruh `indexer.results` — sekarang itu berarti membaca disk. Keduanya
  dihitung saat hasil masuk (`RunStats.indexable`, `RunStats.listed`, di dalam
  lock yang sama dengan `append`). Definisi `listed` yang lama
  (`r.listed and not r.indexable`) dipertahankan lewat `elif`.
- **Run kecil**: di bawah 20.000 baris tidak ada file yang dibuat (ada test-nya).

### 14.5 Yang tidak diukur

- Tidak ada run jaringan sungguhan 660k baris; semua angka di atas lokal.
- Biaya tulis 38 MB ke disk diukur di sandbox ini, bukan di disk lambat/HDD.
- Kalau proses **crash** di tengah run, file `gsa-results-*.pkl` tertinggal di
  folder temp OS (tidak dibersihkan otomatis oleh aplikasi, hanya oleh OS).

### 14.6 Test

```bash
python -m unittest discover -s tests -v      # 108 test (+8)
python tools/verify_all.py --only p6         # 1 pemeriksaan
```

---

## 15. P7 — target POST akhirnya mengirim body

`PingTarget` sudah punya `mode="post"` sejak awal, dan `load_modern_targets()`
membacanya dari JSON. Tapi tidak ada satu pun bagian transport yang
menghormatinya:

1. `with_payload()` mengembalikan `self.url` apa adanya untuk mode `post`
   (memang benar — URL-nya tidak diubah);
2. `Indexer._request()` menulis `method="GET"` mati;
3. `_fetch()` memakai `PooledHttpClient.get()`, dan `_get_once()` memanggil
   `conn.request("GET", ...)` — juga mati.

Akibatnya target `post` mengirim **GET ke URL dasar tanpa parameter apa pun**.
Situs menjawab halaman biasa, `_classify_link()` tidak menemukan target, dan
hasilnya tercatat `submitted` dengan `indexable=False`. Tidak ada error, tidak
ada baris `failed`: submission-nya hanya tidak pernah terjadi. Ini bug
kebenaran, bukan soal kecepatan.

### 15.1 Bentuk request sekarang

| `mode` | Request line | Body |
|---|---|---|
| `append` | `GET <base><target>` | — |
| `query` | `GET <base>?url=<enc>` | — |
| `post` | `POST <base>` | `url=<enc>` (default), `Content-Type: application/x-www-form-urlencoded`, `Content-Length` diisi otomatis |

Body-nya templat, memakai placeholder yang sama dengan URL (`{url}` mentah,
`{enc}` ter-encode). Bisa diubah per target lewat JSON:

```json
{"url": "https://situs.example/ping", "mode": "post", "body": "links[]={enc}"}
```

`body: ""` mengirim POST tanpa payload (beberapa endpoint hanya butuh
sentuhan). `mode` yang tidak dikenal (`"telepathy"`, atau `" POST "` hasil
edit tangan) dinormalkan: yang pertama jatuh ke `append`, yang kedua jadi
`post` - daftar lama tetap jalan, tapi salah ketik tidak lagi diam-diam
berubah jadi GET.

### 15.2 Redirect tetap mengikuti RFC

| Status | Kelanjutan |
|---|---|
| 301/302/303 | `GET` tanpa body (303 memang berarti "ambil hasilnya dengan GET") |
| 307/308 | metode **dan** body diulang |

urllib sendiri menolak POST + 307 (`HTTPRedirectHandler.redirect_request`
melempar `HTTPError` untuk POST kecuali 301/302/303), jadi perilaku 307 hanya
ada di jalur pool — dan itu diuji.

### 15.3 Yang dijaga agar tidak berubah

Target `GET` harus tetap **persis** seperti sebelumnya: tidak ada body, tidak
ada `Content-Type`, header set sama, dan `_fetch()` tetap memanggil `get()`
(metode yang sudah dikenal semua test double). Tiga test khusus menjaga ini:
satu untuk urllib, satu untuk pool, satu untuk `submit_batch`.

### 15.4 Bukti

```bash
python -m unittest tests.test_post_target -v   # 15 test
python tools/verify_all.py --only p7           # 1 pemeriksaan
```

- `P7 Target POST mengirim body` — server lokal menerima
  `POST /ping` dengan body `url=http%3A%2F%2Ft.example%2F` di **kedua**
  transport, `Content-Type` benar, dan hasilnya `submitted` + `indexable=True`;
  request GET berikutnya tidak membawa `Content-Type`/`Content-Length`.
- Test lain mengunci: template kustom, body kosong, batch (satu request, body
  gabungan), 303 → GET, 307 → POST+body, dan normalisasi `mode` dari JSON.

### 15.5 Yang tidak diukur, dan risikonya

- Tidak ada endpoint POST sungguhan yang diuji di internet; semua bukti dari
  server lokal.
- Pool mengulang satu kali saat koneksi gagal (`_get_once`). Kalau koneksi
  mati **setelah** server memproses request, POST bisa terkirim dua kali.
  Endpoint ping idempoten (URL yang sama), dan percobaan ulang hanya terjadi
  ketika koneksi gagal, tapi ini bukan jaminan "tepat sekali".
- Belum ada target `post` di `modern_targets.json` bawaan: fitur ini
  melengkapi jalur yang sudah ada di format data, bukan menambah endpoint baru.
- Deteksi 401/403/429 dan retry/backoff tetap seperti sebelumnya — itu masih
  item terpisah (§8 #7) dan sengaja tidak diubah di sini.

---

## 16. P8 — gzip: lebih sedikit byte, keputusan yang sama

Halaman hasil dari situs ping/statistik adalah HTML 100–200 kB yang sebagian
besar berupa template berulang. `--gzip` menambahkan `Accept-Encoding: gzip`,
lalu mendekode jawabannya sebelum diklasifikasi.

### 16.1 Angka

| Halaman uji | Di kabel |
|---|---|
| 120 kB, tanpa `--gzip` | 120 kB |
| 120 kB, dengan `--gzip` | **32,5 kB** (27%, 3,7x lebih sedikit) |

Halaman uji itu sengaja **sulit** dikompresi (kata dan angka acak); satu
kalimat yang diulang-ulang memberi 0,5 kB dan itu akan membesar-besarkan
hasilnya. Halaman ping sungguhan ada di antara keduanya, jadi 3,7x adalah
batas bawah.

Yang penting bukan angkanya, tapi ini: keputusan `indexed`/`listed` tetap
identik, di kedua transport, dan `check_url` tetap menemukan `noindex` di
dalam body yang terkompresi.

### 16.2 Yang harus hati-hati

Tiga hal yang membuat fitur ini gampang salah:

1. **Body dipotong di `max_body_bytes`** — stream gzip yang terpotong bukan
   error, dan `decode_content()` mengembalikan apa yang bisa didekode.
2. **Server bisa berbohong**: mengaku `gzip` tapi mengirim teks polos. Kalau
   `zlib` gagal, byte aslinya dipakai.
3. **Zip bomb**: hasil dekompresi dibatasi `max_body_bytes`, jadi 1 MB nol yang
   dikompres jadi 1 kB tidak menjadi 1 MB di RAM.

Karena sebagian situs ping menjawab 406 untuk encoding yang tidak mereka
harapkan, dan A/B di jaringan nyata belum dijalankan, `accept_gzip` **default
off** (`--gzip`, config.ini `accept gzip 1`).

---

## 17. P9 — retry 429/503, dengan plafon

`429` dan `503` berarti "nanti", bukan "tidak". Dulu keduanya langsung
`failed`, jadi baris yang kena rate limit hilang dari laporan.

| Skenario | Tanpa `--retries` | `--retries 1` |
|---|---|---|
| 503 lalu 200 | 1 request, `failed` | **2 request, `submitted`** |
| 503 terus | 1 request, `failed` | 2 request, `failed (retry habis)` |
| `Retry-After: 60`, plafon 1 s | 1 request, `failed` | **1 request, 0,00 s, `failed`** |

Kebijakannya:

- `Retry-After` dipakai apa adanya (delta-detik **atau** HTTP-date; tanggal
  lampau menjadi 0).
- Tanpa header: backoff 0,5 s berlipat dengan jitter sampai 25%, supaya daftar
  yang menghantam host yang sama tidak mencoba ulang serempak.
- Tunggu yang lebih lama dari `retry_max_wait` (default 10 s) **ditolak**:
  hasilnya `failed`, bukan worker yang diparkir satu menit. Ini bagian yang
  paling mudah dilupakan saat menambahkan retry.
- Tidur memakai `Event.wait()`, jadi **Stop** memutus backoff dalam hitungan
  milidetik dan barisnya dicatat `skipped - stopped during retry`.
- Default `retries = 0` = perilaku lama, satu percobaan.

Retry hanya berlaku di jalur `submit_one`; jalur batch sengaja dibiarkan (batch
sudah opt-in dan memakai satu request untuk banyak URL).

---

## 18. P10 — transport asyncio: setara, bukan ajaib

`--transport asyncio` menjalankan pekerjaan yang sama di satu event loop dengan
semaphore sebesar `threads`. Yang **dipakai ulang**, bukan ditulis ulang:
`PingTarget.with_payload`/`post_body`, `_classify_link`, `HostCache`,
`decode_content()` (gzip), dan kebijakan retry. `Indexer._record()` menjadi
satu-satunya pemilik statistik, jadi kedua transport melaporkan angka yang sama.

### 18.1 Hasil pengukuran (loopback)

| Beban | threads | asyncio | Rasio |
|---|---|---|---|
| 600 request @ 50 concurrency | 803–960 req/s | 782–1108 req/s | 0,97–1,19x |
| 2.000 request @ 100, server delay 10 ms | 831 req/s | **993 req/s** | 1,19x |
| 2.000 request @ 500, server delay 10 ms | 887 req/s | **1.022 req/s** | 1,15x |
| Puncak RSS, 200 concurrency | 29 MB | 30 MB | sama |

**Kesimpulannya jujur: di mesin ini asyncio setara, bukan lebih cepat.**
Keunggulan yang tidak terlihat di tabel: thread memakan ~8 MB *virtual address
space* per unit dan jumlahnya dibatasi OS, sedangkan event loop tidak — itu
alasan arsitektural untuk memakainya pada run yang sangat lebar (10.000+
target), bukan alasan performa yang terbukti di loopback.

### 18.2 Jebakan yang sempat menipu

Pengukuran pertama menunjukkan asyncio "2x lebih cepat" (390 vs 182 req/s).
Itu **artefak fixture**: `socketserver` memakai backlog listen default 5, jadi
50 koneksi bersamaan sebagian ditolak dan jalur thread yang lebih lambat
kebetulan punya pola yang berbeda. Setelah `request_queue_size` dinaikkan,
angkanya menjadi 1,0–1,2x. Pelajarannya: ukur dulu servernya, baru kliennya.

### 18.3 Yang belum ada di jalur ini

- **Proxy**: hanya jalur thread. Kalau `use_proxy` aktif, run memakai thread dan
  mencatat alasannya di `Indexer.transport_fallback`.
- **Batch** (`--batch N`) juga tetap di jalur thread.
- **Connection pooling**: transport asyncio selalu `Connection: close`.
- **HTTPS** hanya diuji dengan sertifikat self-signed lokal (verify-first, lalu
  fallback tanpa verifikasi) — cukup untuk membuktikan SNI dan fallback-nya
  bekerja, bukan pengganti uji ke situs nyata.

---

## 19. P11 — paket PyPI

### 19.1 Bug yang ditemukan

Wheel 1.2.0 yang dibangun dari repo **tidak berisi `assets/app.ico`**, karena
`[tool.setuptools.package-data]` hanya menyebut `data/*` dan `index_service/*`.
`pip install` karena itu menghasilkan program tanpa ikon tray/GUI — tanpa pesan
error apa pun. Ini jenis bug yang baru terlihat setelah rilis.

### 19.2 Yang ditambahkan

| Berkas | Isi |
|---|---|
| `pyproject.toml` | `assets/*` di package-data, `[project.urls]`, extra `release` (build+twine) |
| `tools/check_package.py` | bangun wheel offline, lalu periksa: 5 berkas runtime wajib, 48 INI, `entry_points.txt` (`gsa-seo-indexer`), METADATA (Name/Version/Requires-Python), jumlah modul, dan workflow rilis (OIDC, tanpa token tersimpan) |
| `.github/workflows/release.yml` | tag `v*` -> build sdist+wheel -> `twine check --strict` -> cek isi wheel -> install wheel di venv + smoke CLI -> upload artifact -> publish |
| `.github/workflows/ci.yml` | job `package`: membangun wheel dan menjalankan CLI dari site-packages pada setiap push |
| `tests/test_packaging.py` | 9 test, termasuk satu build wheel sungguhan |

Publikasi memakai **trusted publishing** (`id-token: write`, environment
`pypi`): tidak ada API token PyPI di repo maupun di secrets. Ada
`workflow_dispatch` dengan `dry_run` untuk membangun tanpa mengunggah.

### 19.3 Bukti lokal

```bash
python tools/check_package.py     # wheel=…1.3.0…, services=48, modules=16, data lengkap
python -m venv /tmp/venv && /tmp/venv/bin/pip install dist/*.whl
/tmp/venv/bin/gsa-seo-indexer sites --count      # 11661
/tmp/venv/bin/gsa-seo-indexer services --list    # 48 service dari site-packages
```

### 19.4 Yang belum dilakukan

- **Belum ada unggahan sungguhan ke PyPI**: sandbox ini tidak punya kredensial,
  dan trusted publishing harus didaftarkan dulu di halaman PyPI
  (project -> Publishing -> tambahkan repo + `release.yml` + environment `pypi`).
  Yang dikerjakan di sini adalah jalur rilis yang siap dipakai.
- Versi dinaikkan manual (pyproject + `engine.APP_VERSION`), tapi sekarang
  dijaga dua lapis: test `test_packaging` dan cek `P14` di `verify_all`
  (pyproject = engine = badge README = tag terakhir).
- Belum ada percobaan ke TestPyPI.

---

## 20. P12 — connection pooling di event loop

Transport asyncio dulu membuka satu koneksi per request (`Connection: close`),
sama seperti urllib. Sekarang `--pool` berlaku juga di event loop: satu pool
kecil per `(scheme, host, port, proxy)`, dengan batas `pool_per_host` /
`pool_max_total` dan idle TTL 20 detik — angka yang sama dengan pool thread,
supaya `--pool` berarti satu hal di kedua transport.

### 20.1 Angka, dan jebakan di baliknya

| 20 request ke 1 host | Koneksi | Waktu |
|---|---|---|
| tanpa pool | 20 | 20 koneksi baru |
| `--pool` | **1** | 1,6x lebih cepat (cek `P12` di verify_all) |
| `--pool`, server ber-`TCP_NODELAY` | **1** | **3,2–3,5x** lebih cepat |

Baris ketiga butuh penjelasan, karena pengukuran pertama saya menunjukkan
kebalikannya: pool tampak **26x lebih lambat** (23 vs 604 req/s). Penyebabnya
bukan koneksi yang dipakai ulang, tapi `http.server` di fixture: ia menulis
status line, header, dan body dengan syscall terpisah, dan selama koneksi
tetap terbuka Nagle di sisi server menahan sisanya sampai delayed ACK datang
(~40 ms). Dengan `Connection: close` efek itu tidak pernah muncul, karena
penutupan socket langsung membuang buffer.

Jadi: **angka pool sangat bergantung pada server**, dan 3,5x itu bukan janji
untuk internet. Yang bisa diklaim: pool benar-benar memakai satu koneksi
(dibuktikan dari port klien di sisi server, bukan dari hitungan internal), dan
koneksi yang sudah ditutup server diulang sekali sehingga tidak ada baris yang
hilang.

### 20.2 Yang dijaga

- Body harus terbaca penuh sebelum koneksi dikembalikan: body yang dipotong di
  `max_body_bytes` menyisakan byte di socket, dan request berikutnya akan
  membacanya sebagai status line.
- `Connection: close` dari server, chunked yang terpotong, atau body tanpa
  `Content-Length` (berakhir di EOF) berarti koneksi dibuang.
- Koneksi keep-alive yang ternyata sudah mati dicoba ulang **sekali** pada
  koneksi baru — sama dengan jalur thread.
- Pool ditutup di `finally` saat run selesai, jadi tidak ada "unclosed
  transport" saat event loop dibuang.

---

## 21. P13 — proxy untuk transport asyncio

Sebelumnya `--transport asyncio` diam-diam jatuh ke thread kalau `--use-proxy`
aktif (`transport_fallback = "proxy/batch -> threads"`). Sekarang proxy
didukung penuh, dan hanya jalur batch yang tersisa di thread.

### 21.1 Dua bentuk yang dipakai

| Target | Yang dikirim ke proxy |
|---|---|
| `http://…` | request line **absolut**: `GET http://host/path?q HTTP/1.1` |
| `https://…` | `CONNECT host:443 HTTP/1.1`, lalu **handshake TLS di dalam tunnel** |

Tunnel dibangun dari socket mentah (`socket.create_connection` di executor,
lalu `loop.sock_sendall`/`sock_recv` untuk pertukaran CONNECT) dan diserahkan
ke `asyncio.open_connection(sock=…, ssl=…)`. Ini perlu karena stream asyncio
tidak bisa "di-upgrade" menjadi TLS setelah dipakai; socket-nya bisa.

### 21.2 Kebijakan yang sama dengan jalur thread

- **Verify-first**: handshake pertama memakai context terverifikasi; kalau
  sertifikatnya rusak, host diingat (`needs_ssl_fallback`) dan tunnel dibangun
  ulang tanpa verifikasi. Untuk sertifikat self-signed ini berarti **dua
  CONNECT** — terlihat di keluaran `P13` (`CONNECT,CONNECT`), dan itu memang
  perilaku yang diharapkan.
- **Proxy mati tidak menandai host mati**: proxy di-retire
  (`Indexer._retire_proxy`) dan barisnya `failed` dengan pesan transport,
  karena proxy yang mati tidak mengatakan apa pun tentang situs tujuan.
- **Alamat proxy divalidasi** (`parse_proxy`): hanya `http`/`https`, dengan
  port default 8080. Skema lain (mis. `socks5`) ditolak dengan pesan jelas
  daripada dipakai sebagai proxy HTTP dan gagal secara membingungkan.

### 21.3 Yang belum ada

- **Proxy dengan autentikasi** (`Proxy-Authorization`): belum dikirim, sama
  seperti jalur thread. Proxy berbayar yang meminta kredensial belum bisa
  dipakai di kedua transport.
- **SOCKS**: tidak didukung di mana pun.
- Tunnel hanya diuji dengan server TLS lokal bersertifikat self-signed; belum
  ada uji ke proxy sungguhan di internet.

### 21.4 Test

```bash
python -m unittest tests.test_asyncio_net -v   # 10 test
python tools/verify_all.py --only p12 --only p13
```

---

## 22. Rilis 1.3.0

Versi dinaikkan dari `1.2.0` ke `1.3.0` karena dua kemampuan baru yang
mengubah permukaan CLI (`--pool` dan `--use-proxy` kini juga berlaku di
`--transport asyncio`). Tidak ada perubahan yang memutus pemakaian lama:
tanpa `--pool` dan tanpa proxy, jalur request tetap satu koneksi per request.

### 22.1 Isi rilis

| Fase | Perubahan |
|---|---|
| P12 | `--pool` di event loop: satu koneksi per host, batas per host/total, idle TTL, retry sekali untuk koneksi basi (§20) |
| P13 | Proxy di event loop: request line absolut untuk HTTP, tunnel `CONNECT` + TLS untuk HTTPS, verify-first dengan fallback per host (§21) |
| — | `build/`, `dist/`, `*.whl` masuk `.gitignore` (dulu bisa ikut ter-commit) |
| — | Cek `P14`: versi di `pyproject.toml`, `engine.APP_VERSION`, badge README, dan tag terakhir harus sama |

### 22.2 Prosedur tag

```bash
# 1. versi naik di dua tempat (dijaga test + P14)
python -m unittest tests.test_packaging -v
python tools/verify_all.py --only p14

# 2. tag anotasi, lalu dorong HANYA tag-nya
git tag -a v1.3.0 -m "GSA SEO Indexer 1.3.0"
git push origin v1.3.0
```

Tag `v*` memicu `.github/workflows/release.yml`: build sdist+wheel,
`twine check --strict`, `tools/check_package.py`, smoke test CLI dari wheel di
venv baru, unggah artefak, lalu job `publish` (environment `pypi`,
`id-token: write`) memakai **trusted publishing** — tanpa token di repo.

Sebelum tag didorong, jalur ini bisa diuji tanpa menerbitkan apa pun:

```bash
gh workflow run release.yml -f dry_run=true   # default-nya memang true
```

Job `publish` hanya jalan untuk `refs/tags/v*` saat `dry_run != true`, jadi
rehearsal lewat `workflow_dispatch` selalu aman.

### 22.3 Yang belum bisa saya lakukan (dan tidak saya klaim)

- **Trusted publisher belum terdaftar, dan saya tidak bisa mendaftarkannya.**
  Pendaftaran ada di UI PyPI (project → Publishing → tambahkan owner/repo,
  nama workflow `release.yml`, environment `pypi`), dan itu butuh login akun
  PyPI pemilik proyek. Saya tidak punya dan tidak akan meminta kredensial
  tersebut.
- **Proyeknya bahkan belum ada di PyPI**: `https://pypi.org/pypi/gsa-seo-indexer/json`
  mengembalikan **404** saat rilis ini disiapkan, jadi belum pernah ada
  unggahan sukses. Perlu diingat: *pending publisher* di PyPI tidak membuat
  halaman proyek, jadi 404 ini **tidak** membuktikan trusted publisher belum
  didaftarkan — hanya membuktikan belum ada rilis.
- **Konsekuensinya**: tag `v1.3.0` baru didorong setelah pemilik repo
  memastikan trusted publisher-nya ada. Kalau didorong lebih dulu, job
  `build` tetap sukses (artefak bisa diunduh) tapi job `publish` gagal di
  langkah unggah dengan pesan OIDC dari PyPI.
- Belum ada uji ke **TestPyPI**; itu cara paling murah untuk membuktikan
  konfigurasi OIDC sebelum proyeknya benar-benar terbit.

### 22.4 Kalau tag ternyata salah

```bash
git tag -d v1.3.0                 # lokal
git push origin :refs/tags/v1.3.0 # remote (hanya bila belum ada unggahan)
```

Setelah sebuah versi benar-benar terbit, PyPI menolak nomor yang sama untuk
selamanya — koreksi harus lewat versi baru (`1.3.1`), bukan menimpa tag.

### 22.5 Revisi: tag pertama merah, dan kenapa

Tag `v1.3.0` didorong pertama kali ke commit `8a0f601`, dan **CI-nya merah**.
Itu bukan kegagalan rilis 1.3.0 saja: CI sudah merah sejak commit P11 dua hari
sebelumnya dan saya tidak memeriksanya — klaim "CI + package job PASS" di §19
berarti "lolos di mesin lokal", bukan "lolos di GitHub Actions". Dua penyebab,
keduanya bisa dibuktikan tanpa membaca log:

| Gejala | Penyebab | Perbaikan |
|---|---|---|
| `Python 3.10` gagal, 3.11 lolos | `tests/test_packaging.py` mengimpor `tomllib`, yang **baru ada di 3.11** | impor `tomllib`/`tomli` opsional + test versi berbasis regex yang jalan di semua versi |
| `Python 3.12/3.13` dan job `package`/`Release` gagal di "Check wheel contents" | `tools/check_package.py` memakai `pip wheel --no-build-isolation`, sedangkan env baru 3.12+ **tidak punya setuptools** | coba tanpa isolasi dulu, lalu ulangi **dengan** build isolation; workflow juga memasang `setuptools>=68` eksplisit |

Bukti lokal untuk yang kedua: venv 3.11 tanpa setuptools
(`pip uninstall setuptools`) menjalankan `tools/check_package.py` dan gagal
dengan `ModuleNotFoundError: No module named 'setuptools'` — persis pesan yang
akan muncul di runner. Sesudah perbaikan, venv yang sama **PASS**.

Bukti untuk yang pertama: modul 3.10 disimulasikan dengan memblokir
`tomllib`/`tomli` di `builtins.__import__`; hasilnya `Ran 10 tests ... OK
(skipped=5)` — nol kegagalan, dan test versi baru tetap jalan. Di CI 3.10,
`tomli` dipasang supaya lima test metadata itu benar-benar dieksekusi, bukan
dilewati.

Karena belum ada satu pun berkas yang terunggah (job `publish` tidak pernah
jalan), tag dipindahkan ke commit perbaikan dengan `git tag -f` +
`git push --force origin v1.3.0`. Memindahkan tag seperti ini **hanya** sah
selama versi itu belum terbit: sesudah PyPI menerima `1.3.0`, nomornya tidak
bisa dipakai lagi dan koreksi harus lewat `1.3.1`.

Pelajaran yang saya catat untuk diri sendiri: `verify_all` membuktikan angka,
bukan pipeline. Status CI harus dibaca (`gh run list`), bukan diasumsikan dari
"semua hijau di lokal".

### 22.6 Percobaan pertama setelah perbaikan: build hijau, publish ditolak

Run `v1.3.0` yang kedua (`42494107139`):

| Job | Hasil |
|---|---|
| Build sdist + wheel | ✅ 23 s, artefak `dist` 492 026 byte (bisa diunduh dari halaman run) |
| Publish to PyPI | ❌ 12 s — ditolak di langkah `pypa/gh-action-pypi-publish` |

Pesan dari PyPI, apa adanya:

```
Trusted publishing exchange failure:
Token request failed: the server refused the request for the following reasons:
* `invalid-publisher`: valid token, but no corresponding publisher
  (Publisher with matching claims was not found)
```

Klaim yang dikirim GitHub (dari halaman run, untuk debugging):

```
sub             repo:njutawan@61883069/GSA-SEO-Indexer@1432203421:environment:pypi
repository      njutawan/GSA-SEO-Indexer
workflow_ref    njutawan/GSA-SEO-Indexer/.github/workflows/release.yml@refs/tags/v1.3.0
```

Artinya token OIDC-nya sah, tapi **tidak ada konfigurasi publisher di PyPI yang
cocok dengan klaim itu**. Yang harus sama persis di halaman PyPI
(*Your projects → Publishing*, atau *Pending publisher* bila proyeknya belum
pernah terbit):

| Kolom PyPI | Nilai untuk repo ini |
|---|---|
| PyPI Project Name | `gsa-seo-indexer` |
| Owner | `njutawan` |
| Repository name | `GSA-SEO-Indexer` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

Empat kesalahan yang paling sering menghasilkan `invalid-publisher`:
mendaftarkannya di **TestPyPI** padahal unggahan menuju PyPI; nama proyek
ditulis `gsa_seo_indexer` (garis bawah); kolom *workflow* diisi `release.yaml`
atau path lengkap `.github/workflows/release.yml` (PyPI hanya mau nama
berkasnya); dan kolom *environment* dibiarkan kosong — pada kasus terakhir
klaim `sub` kehilangan bagian `:environment:pypi` sehingga tidak cocok.

Karena unggahan belum pernah berhasil (PyPI masih 404 untuk proyek ini),
**tag tidak perlu diganti dan versi tidak perlu dinaikkan**. Sesudah
konfigurasi PyPI diperbaiki, job yang gagal diulang tanpa commit baru:

```bash
gh run rerun 42494107139 --failed
```

Status pipeline pada commit `9844057`: **CI 5/5 hijau** (Python 3.10, 3.11,
3.12, 3.13, dan job paket). Yang tersisa hanya satu langkah milik akun PyPI,
bukan milik kode.
