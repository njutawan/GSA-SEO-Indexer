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

## 7. Yang masih dibutuhkan (roadmap)

### P3 — kebersihan arsitektur

- **Satu jalur HTTP saja.** `submit_one()`, `submit_batch()`, dan `PooledHttpClient.get()`
  masing-masing membangun header, menangani redirect, timeout, dan fallback TLS.
  Perbedaan kecil di antaranya (mis. `max_redirects`, timeout) adalah sumber bug masa
  depan. Satukan di satu client, sisakan `submit_*` sebagai orkestrasi.
- **`random_delay` default `True`** (`engine.py:179`) bertentangan dengan `config.ini`
  (`random delay = 0`) dan dokumentasi ("tidak ada manfaat terukur, hanya menambah
  waktu"). Samakan ke `False`; sekarang API call langsung (`Indexer(Settings())`)
  tidur 10–80 ms per request tanpa alasan.
- **TLS fallback tanpa verifikasi** (`engine.py:_urlopen`) membuat siapa pun bisa
  men-downgrade koneksi dengan sertifikat rusak. Untuk pengiriman URL publik risikonya
  rendah, tapi sebaiknya tercatat eksplisit di README (sudah ada) dan tidak dipakai
  untuk index service berbayar yang membawa API key.
- **`preflight` memakai `threads * 2` worker** → burst 100 socket; batasi ke angka
  absolut (mis. 64) agar tidak membentur limit file descriptor di Windows.
- **CI + linter.** `tests/` sekarang berisi 60 test (unittest murni, tanpa dependency),
  jalankan `python -m unittest discover -s tests`. Tambahkan GitHub Actions matrix
  3.10–3.13 + `ruff check` (opsional sebagai dev-dependency, runtime tetap zero-dep).

---

## 8. Yang sudah dikerjakan di branch ini

| File | Perubahan |
|---|---|
| `gsa_seo_indexer/httpclient.py` | `tls_context()`: satu context terverifikasi + satu tanpa verifikasi per proses; dipakai juga oleh `_Pool` |
| `gsa_seo_indexer/engine.py` | `_ssl_context()` mendelegasikan ke `tls_context()` (tidak ada context per request) |
| `gsa_seo_indexer/proxies.py` | memakai `tls_context(False)`; tidak lagi membuat context sendiri |
| `gsa_seo_indexer/engine.py` | gerbang cepat lowercase + substring di `_classify_link()` (P0 #2) |
| `gsa_seo_indexer/engine.py` | `cap_targets()`: batas = N host, kedua bentuk URL dipertahankan, `stats.total` akurat (P0 #3) |
| `gsa_seo_indexer/cli.py`, `gui.py` | tidak lagi memotong daftar sendiri; banner melaporkan situs unik |
| `tools/bench_local.py` | benchmark offline: server lokal, N request, req/s + median/p95 |
| `gsa_seo_indexer/engine.py` | `check_urls()`: cek target paralel, urutan hasil stabil (P1 #1) |
| `gsa_seo_indexer/cli.py`, `gui.py` | memakai `check_urls()`; GUI menampilkan penolakan saat ditemukan |
| `gsa_seo_indexer/gui.py` | `MAX_TREE_ROWS = 5_000`: tabel dibatasi, sisa baris dihitung (P1 #2) |
| `gsa_seo_indexer/engine.py` | `as_completed()` + `cancel()`: tidak ada head-of-line blocking (P1 #3) |
| `gsa_seo_indexer/engine.py` | laporan JSON streaming + `Result(slots=True)` (P1 #4) |
| `gsa_seo_indexer/perf.py` | `HostCache`: ambang gagal berturut-turut + persistensi v2 (P2 #1) |
| `gsa_seo_indexer/perf.py` | `DnsCache`: prune + `max_entries`, error tanpa objek exception, deaktivasi aman (P2 #2) |
| `gsa_seo_indexer/engine.py` | `_ConnectTimeout*Connection/Handler`: connect timeout sungguhan (P2 #3) |
| `gsa_seo_indexer/cli.py` | `resolve_timeouts()`: `--timeout` mengisi read dan membatasi connect |
| `tests/test_engine.py` | 40 test: P0/P1 + connect timeout, read timeout per request |
| `tests/test_gui_rows.py` | 3 test: cap baris tabel GUI (dengan stub tkinter) |
| `tests/test_perf_cache.py` | 12 test: streak host, prune/batas DNS, aktivasi cache |
| `tests/test_cli_timeouts.py` | 5 test: pemetaan opsi timeout CLI |

## 9. Cara mengukur ulang

```bash
# 400 request lewat 16 thread, server lokal tanpa latensi
python tools/bench_local.py 400 16

# benar-benar memisahkan overhead klien: 1 thread
python tools/bench_local.py 400 1

# test regresi (tanpa dependency tambahan)
python -m unittest discover -s tests -v

# per modul
python -m unittest tests.test_engine -v       # engine + transport
python -m unittest tests.test_perf_cache -v   # host cache + DNS cache
python -m unittest tests.test_gui_rows -v     # cap tabel GUI (stub tkinter)
python -m unittest tests.test_cli_timeouts -v # pemetaan opsi timeout
```

Untuk mengukur dampak di jaringan nyata, jalankan run yang sama dua kali dengan build
sebelum/sesudah dan bandingkan `Submitted`, `Backlinks`, dan waktu total — jangan
mengandalkan angka benchmark lokal sebagai klaim kecepatan internet.
