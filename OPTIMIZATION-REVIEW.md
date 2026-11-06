# Review Optimasi — GSA SEO Indexer v1.2.0

Dokumen ini menjawab satu pertanyaan: **optimasi apa yang masih dibutuhkan proyek ini,
dan mana yang paling berdampak?** Semua angka di sini diukur, bukan diperkirakan —
lihat cara reproduksinya di bagian [Cara mengukur ulang](#cara-mengukur-ulang).

Ringkasnya: proyek ini sudah pintar menghindari kerja yang sia-sia (cache host mati,
DNS cache, dedupe per host, skip host yang timeout), tetapi **hot path per-request-nya
membuang ~29 ms CPU untuk setiap request** — bahkan untuk target `http://` yang tidak
memakai TLS sama sekali. Itu plafon throughput yang sebenarnya, bukan latensi server.

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
| **P2** | Tidak ada test, CI, atau linter (6.300 LOC, 0 test) | regresi performa/correctness tidak terdeteksi | 🟡 sebagian (39 test + benchmark) |
| **P3** | Tiga jalur HTTP paralel (`submit_one`, `submit_batch`, `PooledHttpClient`) | perilaku (timeout, redirect, verify) mudah menyimpang | ⬜ belum |
| **P3** | `Settings.random_delay = True`, padahal `config.ini` menulis `0` dan dokumentasi bilang "tidak ada manfaat" | sleep 10–80 ms di dalam worker untuk API call langsung | ⬜ belum |

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
| 2 | `_classify_link()` jalur "listed" menjalankan `_QUERY_ECHO_RE` atas **seluruh body** | **1,44 ms** per halaman 200 kB (anchor-hit 0,23 ms) | periksa jendela ±200 karakter di sekitar kemunculan pertama: **0,028 ms** (−98%) | sedang (echo bisa terpotong di batas jendela → perlu uji klasifikasi) | **belum — butuh korpus** |
| 3 | Laporan JSON memakai `indent=1` dan `json.dump` per baris | **100k baris: 1,90 s, 17,7 MB** (CSV: 0,10 s, 5,8 MB) | compact + tulis per blok: **−35% waktu, −14% ukuran** (100k baris) | rendah (format berubah; pembaca JSON tetap kompatibel) | **selesai** (§12.2) |
| 4 | "Run enabled" ke index service berjalan **sekuensial** di satu thread | 48 service × 200 ms = **9,4 s** | 8 worker paralel: **1,2 s** (7,8×) | rendah–sedang (butuh pembatas + status per service di GUI) | **selesai** (§12.3) |
| 5 | `self.results` menahan semua baris sampai run selesai | **17,7 MB / 100k baris** (slots) | run 660k baris ≈ 115 MB; streaming ke disk menghapusnya | sedang (mengubah API `results`/`write_report`) | **belum — ubah API** |
| 6 | Tidak ada `Accept-Encoding: gzip` di jalur urllib | body sampai 200 kB dikirim utuh | ±3–5× lebih sedikit byte pada halaman yang compressible | sedang (perlu A/B pada list asli; sebagian situs rewel soal header) | **belum — butuh A/B jaringan** |
| 7 | 429/503 hanya dicatat `failed`, `Retry-After` diabaikan | satu baris hilang per kejadian | retry/backoff dalam run menaikkan yield, bukan kecepatan | rendah (perlu plafon percobaan) | **belum — ubah semantik** |
| 8 | `PingTarget(mode="post")` tidak pernah mengirim body | `submit_one` selalu GET | fitur belum lengkap, bukan bottleneck | — (tidak ada target yang memakainya) | **belum — fitur** |
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
| `tools/verify_all.py` | skrip verifikasi ulang 24 pemeriksaan P0–P4, offline, `--only p0..p4` |
| `gsa_seo_indexer/engine.py` | `_opener()` di-cache per (proxy, verify) — 200 request = 1 build (P4 #1) |
| `gsa_seo_indexer/engine.py` | laporan JSON compact, ditulis per 512 baris (`_dump_row`) (P4 #2) |
| `gsa_seo_indexer/services.py` | `submit_many()`: 8 service paralel, urutan hasil tetap, bisa di-Stop (P4 #3) |
| `gsa_seo_indexer/gui.py` | "run enabled" memakai `submit_many`; `MAX_DRAIN_PER_TICK = 400` (P4 #3/#4) |
| `gsa_seo_indexer/cli.py` | `services --run-enabled --workers` — jalur paralel yang sama, headless |
| `gsa_seo_indexer/watcher.py` | `_seen` dibatasi `max_seen` (deque + set) (P4 #4) |
| `tests/test_watcher.py` | 5 test: baris baru, rotasi file, batas `_seen`, folder |
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

# verifikasi ulang semua klaim P0-P3 sekaligus (offline, ±16 s)
python tools/verify_all.py

# test regresi (tanpa dependency tambahan)
python -m unittest discover -s tests -v

# per modul
python -m unittest tests.test_engine -v       # engine + transport
python -m unittest tests.test_perf_cache -v   # host cache + DNS cache
python -m unittest tests.test_gui_rows -v     # cap tabel GUI (stub tkinter)
python -m unittest tests.test_cli_timeouts -v # pemetaan opsi timeout
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
python tools/verify_all.py            # 20 pemeriksaan P0-P3 (±16 s; skrip kini 24 termasuk P4)
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
python tools/verify_all.py                 # 24 pemeriksaan semua fase
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
