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
| **P1** | Cek URL pra-run berjalan **serial** (1 request per URL) | 50 URL × timeout 5 s = sampai 4 menit sebelum run dimulai | ⬜ belum |
| **P1** | Tabel GUI menyimpan **semua** baris (333.800 baris untuk 50 URL × 6.676 host) | RAM ±51 MB hanya untuk objek `Result`, plus baris Tk; GUI melambat/hang | ⬜ belum |
| **P1** | `pool.map()` = head-of-line blocking untuk `on_result()` | progress UI diam sampai request terlambat selesai; Stop tidak responsif | ⬜ belum |
| **P1** | `report_format = json` menyalin seluruh hasil ke list baru | lonjakan memori 2× saat export | ⬜ belum |
| **P2** | Host yang pernah sukses **tidak pernah** ditandai mati | host yang mati setelah sukses tetap dibayar timeout-nya di setiap URL | ⬜ belum |
| **P2** | `DnsCache._entries` tidak pernah dipangkas / menyimpan objek exception | memori tumbuh antar-run; traceback ikut tersimpan | ⬜ belum |
| **P2** | `connect timeout` tidak berpengaruh di jalur default (urllib hanya menerima satu timeout) | timeout koneksi 4 s vs read 5 s tidak dibedakan | ⬜ belum |
| **P2** | Tidak ada test, CI, atau linter (6.300 LOC, 0 test) | regresi performa/correctness tidak terdeteksi | 🟡 sebagian (13 test + benchmark) |
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

## 5. Yang masih dibutuhkan (roadmap)

### P1 — dampak besar, perlu sedikit desain

**5.1 Cek URL pra-run secara paralel** (`cli.py:431`, `gui.py` worker validation)
Sekarang: `for u in urls: indexer.check_url(u)`. Satu URL mati = satu timeout penuh,
berurutan. Ganti dengan `ThreadPoolExecutor(max_workers=min(8, len(urls)))`; hemat
hingga menit-menit pada daftar 50+ URL. Risiko rendah, tidak mengubah semantik.

**5.2 Batasi baris tabel GUI** (`gui.py:701`)
`_insert_result_row()` menambah satu baris Tk per hasil; 50 URL × 6.676 host = 333.800
baris. Selain lambat, `_all_rows` + `_row_tag` tumbuh tanpa batas. Saran: simpan
`Result` penuh di engine (untuk laporan), tampilkan maksimum N baris (mis. 5.000) +
penghitung "… dan 328.800 baris lainnya", atau virtualisasi dengan `ttk.Treeview` yang
di-truncate per tick.

**5.3 Ganti `pool.map` dengan `as_completed`** (`engine.py:903`)
`pool.map` mengembalikan hasil **berurutan**, jadi satu request 5 detik menahan
`on_result()` untuk semua hasil sesudahnya. Dengan `as_completed` + set future,
progress dan Stop jadi responsif, dan tidak ada `list()` yang menahan hasil di memori.

**5.4 Tulis laporan secara streaming** (`engine.py:1153`)
`json.dump([asdict(r) for r in self.results])` membuat salinan seluruh hasil (untuk
333.800 baris: ±2× memori). Tulis per-record (JSON Lines atau manual `[`…`]`), dan
pertimbangkan `@dataclass(slots=True)` untuk `Result` (Python 3.10+) yang memotong
memori per baris cukup signifikan.

### P2 — correctness jangka panjang

**5.5 Host yang pernah sukses tidak pernah dianggap mati** (`perf.py:94`)
`record_failure()` hanya menandai `alive = False` bila `successes == 0`. Host yang
sukses sekali lalu mati akan dibayar timeout-nya di setiap URL berikutnya. Tambahkan
ambang "gagal berturut-turut" (mis. 2–3) sebelum menandai mati, atau decay skor.

**5.6 `DnsCache` tumbuh tanpa batas** (`perf.py:218`)
Entri tidak pernah dipangkas dan menyimpan objek exception (traceback ikut hidup).
Pangkas entri kedaluwarsa secara periodik (mis. saat `len > 4096`) dan simpan
`(type, str)` bukan instance exception. Catatan: cache ini mem-patch
`socket.getaddrinfo` **seluruh proses** dan hanya mendukung satu cache aktif —
dua `Indexer` bersamaan akan saling menimpa.

**5.7 `connect timeout` tidak berlaku di jalur default**
`_timeout()` mengembalikan `max(connect_timeout, read_timeout)` dan urllib hanya
menerima satu timeout, jadi `connect timeout = 4` tidak pernah dipakai; hanya
`PooledHttpClient` yang membedakannya. Pilih salah satu: pakai pooled client sebagai
default (dengan perbaikan 5.8), atau hapus opsi dari config agar tidak menyesatkan.

**5.8 Connection pool: default `off` itu keputusan yang benar — untuk beban ini**
`reuse_connections` mati secara default, dan itu wajar: setiap host hanya dikunjungi
1–2 kali per run, sehingga keep-alive hampir tidak pernah terpakai (dan 96 koneksi
terbuka malah membatasi host yang bisa dikerjakan bersamaan). Jangan "optimalkan"
dengan menyalakannya; kalau mau, pakai hanya saat `len(urls) >= 10` **dan** thread
lebih sedikit dari jumlah host, lalu ukur ulang.

**5.9 Gzip + baca body lebih pendek**
Body dibaca sampai 200 kB (`max_body_bytes`) untuk sekadar mencari satu substring.
Dua kandidat: kirim `Accept-Encoding: gzip` dan dekompres (mengurangi byte di jaringan),
atau baca bertahap dan berhenti begitu target ditemukan. Yang kedua hanya menolong
halaman "hit"; mayoritas adalah miss, jadi ukur dulu sebelum dikerjakan.

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
- **CI + linter.** `tests/` sekarang berisi 13 test (unittest murni, tanpa dependency),
  jalankan `python -m unittest discover -s tests`. Tambahkan GitHub Actions matrix
  3.10–3.13 + `ruff check` (opsional sebagai dev-dependency, runtime tetap zero-dep).

---

## 6. Yang sudah dikerjakan di branch ini

| File | Perubahan |
|---|---|
| `gsa_seo_indexer/httpclient.py` | `tls_context()`: satu context terverifikasi + satu tanpa verifikasi per proses; dipakai juga oleh `_Pool` |
| `gsa_seo_indexer/engine.py` | `_ssl_context()` mendelegasikan ke `tls_context()` (tidak ada context per request) |
| `gsa_seo_indexer/proxies.py` | memakai `tls_context(False)`; tidak lagi membuat context sendiri |
| `gsa_seo_indexer/engine.py` | gerbang cepat lowercase + substring di `_classify_link()` (P0 #2) |
| `gsa_seo_indexer/engine.py` | `cap_targets()`: batas = N host, kedua bentuk URL dipertahankan, `stats.total` akurat (P0 #3) |
| `gsa_seo_indexer/cli.py`, `gui.py` | tidak lagi memotong daftar sendiri; banner melaporkan situs unik |
| `tools/bench_local.py` | benchmark offline: server lokal, N request, req/s + median/p95 |
| `tests/test_engine.py` | 25 test: reuse context (termasuk end-to-end), klasifikasi, cap situs, smoke `submit_one` |

## 7. Cara mengukur ulang

```bash
# 400 request lewat 16 thread, server lokal tanpa latensi
python tools/bench_local.py 400 16

# benar-benar memisahkan overhead klien: 1 thread
python tools/bench_local.py 400 1

# test regresi (tanpa dependency tambahan)
python -m unittest discover -s tests -v
```

Untuk mengukur dampak di jaringan nyata, jalankan run yang sama dua kali dengan build
sebelum/sesudah dan bandingkan `Submitted`, `Backlinks`, dan waktu total — jangan
mengandalkan angka benchmark lokal sebagai klaim kecepatan internet.
