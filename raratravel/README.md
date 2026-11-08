# Rara Travel & Tour — Landing Page

Landing page statis premium untuk **Rara Travel & Tour** — travel antar jemput
door-to-door Bali ↔ Jawa, carter mobil, dan titipan paket kilat.

- **Domain:** https://www.raratravel.id
- **WhatsApp:** +62 813 5354 957
- **Email:** hubungi@raratravel.id
- **Stack:** HTML + CSS + vanilla JavaScript. Tanpa build step, tanpa npm, tanpa backend.

---

## 1. Struktur proyek

```
raratravel/
├── index.html              # Halaman utama (semua section)
├── 404.html                # Halaman error kustom
├── css/style.css           # Sumber styling (custom properties, mobile-first)
├── css/style.min.css       # Versi ter-minify yang dimuat halaman (dibuat oleh tools/minify.py)
├── js/boot.js              # 1 baris: penanda JS aktif (critical, tanpa defer)
├── js/main.js              # Progressive enhancement, defer (< 7 KB)
├── assets/
│   ├── favicon.svg
│   ├── apple-touch-icon.png
│   ├── fonts/              # Playfair Display + Plus Jakarta Sans (woff2, self-hosted)
│   └── img/                # Semua gambar: .webp (utama) + .jpg (fallback)
├── _headers                # Cache + security headers (Cloudflare Pages)
├── _redirects              # Redirect canonical & normalisasi URL
├── robots.txt
├── sitemap.xml
├── tools/minify.py         # Minifier CSS stdlib (tanpa npm) — bukan bagian dari situs
└── README.md
```

**Semua file berada di root folder `raratravel/`.** Saat deploy, folder inilah
yang menjadi *build output directory* (root). Tidak ada build command.

---

## 2. Deploy

### 2a. Deploy via dashboard (Git integration)

1. Push folder ini ke repository GitHub/GitLab.
2. Buka **Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git**.
3. Pilih repository, lalu isi:
   - **Framework preset:** `None`
   - **Build command:** *(kosongkan)*
   - **Build output directory:** `raratravel` (atau `/` bila isi repo = isi folder ini)
4. **Save and Deploy**. Situs akan tersedia di `<project>.pages.dev`.

### 2b. Deploy via CLI (Wrangler)

```bash
# sekali saja
npm install -g wrangler

# login (browser akan terbuka)
wrangler login

# deploy langsung dari folder ini — tanpa build
cd raratravel
wrangler pages deploy . --project-name rara-travel
```

Deploy ulang: jalankan perintah `wrangler pages deploy .` yang sama.
Untuk preview branch: `wrangler pages deploy . --branch=preview`.

> Alternatif tanpa Wrangler: drag-and-drop folder `raratravel` ke
> **Dashboard → Pages → Upload assets**.

---

## 3. Update konten

| Yang ingin diubah | Lokasi |
|---|---|
| Nomor WhatsApp | Cari & ganti `628135354957` di `index.html` (semua tautan `wa.me`) dan `404.html` |
| Email | Cari & ganti `hubungi@raratravel.id` di `index.html`, `404.html`, `sitemap.xml` (bila perlu) |
| Teks hero / headline | `index.html` → `<section class="hero">` |
| Kartu layanan | `index.html` → `<section id="layanan">` |
| Daftar keunggulan | `index.html` → `<section id="keunggulan">` |
| Rute & kota (chips) | `index.html` → `<section id="rute">` |
| Harga | `index.html` → `<section id="harga">` |
| Testimoni | `index.html` → `<section id="testimoni">` |
| FAQ | `index.html` → `<section id="faq">` **dan** blok `FAQPage` di JSON-LD (harus sinkron) |
| Jam operasional | `index.html` → footer + `openingHoursSpecification` di JSON-LD |
| Warna / tipografi | `css/style.css` → blok `:root` (design tokens) |

> **Setelah mengubah `css/style.css`, jalankan `python3 tools/minify.py`.**
> Halaman memuat `css/style.min.css`; tanpa langkah ini perubahan CSS tidak
> akan tampil. Script ini hanya membuang komentar/whitespace — tidak ada
> dependency, tidak ada bundler, tidak ada build server-side.

**Penting:** bila Anda mengubah pertanyaan/jawaban FAQ, perbarui juga
`application/ld+json` di `<head>` agar schema Google Rich Results tetap valid.
Harga di JSON-LD (`"price"`) juga perlu disamakan dengan kartu harga.

Pesan WhatsApp yang sudah terisi otomatis (URL-encoded) ada di setiap CTA —
ubah teks `?text=...` bila ingin mengubah template pesan.

---

## 4. Update gambar

Semua gambar punya dua format: **`.webp`** (utama) + **`.jpg`** (fallback),
dirujuk melalui `<picture>`.

| File | Dipakai di | Ukuran target |
|---|---|---|
| `hero-1600.webp` / `hero-1200.webp` / `hero-800.webp` + `hero-800.jpg` / `hero-fallback.jpg` | Hero (LCP, responsif via `srcset`) | tiap file < 250 KB |
| `hero-og.jpg` | Open Graph / Twitter | 1200×630 |
| `*-card.webp` / `*-card-fallback.jpg` | Kartu layanan | < 100 KB |
| `apple-touch-icon.png` | iOS home screen | 180×180 |

Cara menambah/mengganti gambar (tanpa tool berat):

```bash
# WebP
cwebp -q 74 foto.jpg -o assets/img/rute-card.webp

# JPG fallback teroptimasi
convert foto.jpg -strip -resize '900x675^' -gravity center -extent 900x675 \
        -interlace Plane -quality 76 assets/img/rute-card-fallback.jpg
```

Aturan penting:

- Pertahankan atribut `width` / `height` agar CLS tetap kecil.
- Hero memakai `srcset` + `sizes="100vw"` (800/1200/1600 px). Bila Anda
  mengganti foto hero, buat ketiga lebar tersebut dan perbarui blok
  `<link rel="preload" as="image" imagesrcset="...">` di `<head>`.
- Hero **jangan** diberi `loading="lazy"` (harus `fetchpriority="high"`).
- Gambar di bawah fold pakai `loading="lazy" decoding="async"`.
- Isi `alt` dengan deskripsi nyata — jangan kosong, jangan diisi kata kunci berulang.

---

## 5. Custom domain `raratravel.id`

1. Tambahkan domain ke akun Cloudflare (ubah nameserver di registrar).
2. **Pages → project → Custom domains → Set up a domain**:
   - tambahkan `www.raratravel.id` (jadikan **primary**)
   - tambahkan `raratravel.id` (apex)
3. Cloudflare membuat record `CNAME`/`ALIAS` otomatis. Pastikan SSL mode
   **Full (strict)** dan opsi **Always Use HTTPS** aktif.
4. **Canonical = `https://www.raratravel.id/`**, maka apex harus redirect ke www:

   **Rules → Redirect Rules → Create rule**
   - *If incoming requests match:* `Hostname equals "raratravel.id"`
   - *Then:* **Static redirect** → `https://www.raratravel.id` + *preserve query string*
   - *Status code:* `301`

   Aturan yang sama juga sudah ditulis di `_redirects` sebagai jaring pengaman,
   namun Pages tidak dapat melakukan redirect berbasis hostname — jadi aturan
   di level zona (langkah ini) tetap wajib.
5. Tambahkan juga aturan **Normalize trailing slash** bila ingin
   `.../halaman/` → `.../halaman`.
6. Setelah domain aktif, submit `https://www.raratravel.id/sitemap.xml` di
   **Google Search Console** dan **Bing Webmaster Tools**.

---

## 6. Troubleshooting

| Gejala | Penyebab umum | Solusi |
|---|---|---|
| Halaman 404 setelah deploy | Build output directory salah | Set output directory ke folder yang berisi `index.html` |
| CSS/JS tidak termuat | Path absolut `/css/...` dipakai di sub-path | Deploy di root domain, bukan sub-folder |
| Gambar tidak muncul | Huruf besar/kecil nama file | Cloudflare case-sensitive; samakan nama file |
| Font tidak ter-load | Header `Access-Control` atau path salah | Pastikan `assets/fonts/*.woff2` ada & preload mengarah ke file yang benar |
| `_headers` / `_redirects` diabaikan | File tidak berada di root output | Keduanya harus sejajar dengan `index.html` |
| CSP memblokir sesuatu | Resource eksternal ditambahkan | CSP di `_headers` hanya mengizinkan `'self'`, `wa.me`, dan `data:` |
| WhatsApp link error | Nomor tidak format internasional | Gunakan `https://wa.me/62xxxxxxxxxx` tanpa `+` dan tanpa spasi |
| Perubahan tidak terlihat | Cache HTML 1 jam + asset immutable | Hard reload, atau naikkan nama file asset (`style.v2.css`) |
| Preview `*.pages.dev` redirect ke www | Rule canonical | Tambahkan pengecualian hostname `*.pages.dev` di Redirect Rule |
| Lighthouse Performance turun | Gambar > 250 KB atau font ekstra | Kompres ulang WebP, kurangi weight font yang di-preload |

### Checklist QA

- [ ] Responsif di 320 / 375 / 768 / 1024 / 1440 px
- [ ] Navigasi keyboard penuh + focus ring terlihat
- [ ] `prefers-reduced-motion` menghormati preferensi pengguna
- [ ] Semua CTA WhatsApp membuka chat dengan pesan terisi
- [ ] Schema divalidasi di [Rich Results Test](https://search.google.com/test/rich-results)
- [ ] HTML valid di [W3C Validator](https://validator.w3.org/)
- [ ] Lighthouse ≥ 95 (Performance, Accessibility, Best Practices, SEO)
      *Hasil pengukuran terakhir (Chrome headless, gzip aktif):*
      *Performance 99 · Accessibility 100 · Best Practices 100 · SEO 100 —
      LCP 2,0 s · CLS 0 · TBT 0 ms.*
- [ ] Tidak ada mixed content (semua resource HTTPS/relatif)

---

## 7. Batasan teknis yang dipatuhi

- 100% statis — tidak ada PHP, database, SSR, atau fungsi server-side.
- Tidak ada npm/bundler di produksi; font & gambar disajikan apa adanya.
- Total file proyek < 60, tidak ada file > 500 KB.
- Gambar `webp` + fallback `jpg`; hero < 250 KB.
- JS < 50 KB dan CSS < 40 KB (uncompressed), script `defer`.
- Font `woff2` self-hosted dengan `font-display: swap`.
- `_headers`: CSP dasar, `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, cache HTML 1 jam, asset immutable 1 tahun.

---

© Rara Travel & Tour — Lumajang, Jawa Timur, Indonesia.

---

## 8. Catatan hasil QA

Pengujian dijalankan pada salinan lokal yang disajikan dengan gzip:

| Pemeriksaan | Hasil |
|---|---|
| Lighthouse (mobile) | Performance **99**, Accessibility **100**, Best Practices **100**, SEO **100** |
| Core Web Vitals (lab) | LCP **2,0 s**, CLS **0**, TBT **0 ms** |
| axe-core (WCAG 2.0/2.1/2.2 A+AA) | **0** pelanggaran — desktop, mobile, dan 404 |
| html-validate (recommended) | **0** error — `index.html` & `404.html` |
| Responsif | Tidak ada horizontal scroll pada 320 / 375 / 768 / 1024 / 1440 px |
| Tanpa JavaScript | Semua konten tetap terbaca, navigasi (termasuk menu mobile) tetap berfungsi |
| `prefers-reduced-motion` | Semua transisi/reveal dimatikan, konten langsung tampil |
| Keyboard | Skip-link, focus ring emas 3px, menu mobile bisa ditutup dengan Esc |
| Ukuran | 32 file (termasuk README & tools) · ~1,1 MB total · tidak ada file > 500 KB · CSS 34 KB · JS 6,6 KB · gambar hero 33–80 KB |

Catatan: angka Lighthouse diukur di lingkungan CI (CPU emulation) tanpa CDN.
Di Cloudflare Pages, aset disajikan dari edge dengan Brotli sehingga hasil
lapangan umumnya lebih baik.
