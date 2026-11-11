# Cerekey

Aplikasi web **keyword research** untuk proyek PKL (nama kode di repo: TrendWatch). Ketik satu kata kunci (misalnya
**cpns**) dan dapatkan ratusan ide keyword dari Google Autocomplete (opsional YouTube dan Bing) yang digabung dengan
data Google Trends, lengkap dengan maksud pencarian dan turunan tiap keyword. Setelah login, pengguna memilih salah satu dari 15
platform; tiap platform punya dashboard berisi riset keyword, berita Google Berita, dan riwayat keyword (Dipakai/Tidak).
Semua sumber data **gratis**; volume pencarian dan keyword difficulty tidak tersedia (lihat "Sumber data").

FastAPI + SQLite + halaman web biasa (tanpa framework front-end). Login dengan akun pengguna wajib.

## Mulai cepat

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app          # buka http://127.0.0.1:8000, klik "Buat akun", lalu mulai mencari
```

Tanpa jaringan atau untuk mencoba tampilan: centang **Pakai data contoh** di halaman utama. Untuk pengembangan lokal tanpa
login: `TRENDWATCH_AUTH=off`. Tes: `pip install -r requirements-dev.txt && python -m pytest -q`.

## Halaman

Antarmuka hanya memiliki halaman di bawah ini, ditambah halaman akun (`/login`, `/daftar`, `/lupa`, `/reset`, `/akun`).
Fitur lain (booming per topik, riset topik, jelajah, Daily Discovery, tren harian) tetap tersedia sebagai **API dan perintah CLI**
tanpa halaman: `/api/booming`, `/api/research`, `/api/explore`, `/api/research/save`, perintah `collect`/`discover`, dan
workflow GitHub Actions. Bagian "Cara kerja" di bawah menjelaskan mesin itu.

| Halaman | Alamat | Fungsi |
|---|---|---|
| **Pilih platform** | `/platform` | Setelah login, pengguna memilih salah satu dari **15 platform** (JagoUKP, JadiASN, JadiBUMN, JadiCerebrum, JadiPPPK, JadiPRAJURIT, JadiPOLISI, JadiPCPM, JagoTPA, JadiBEASISWA, JadiSEKDIN, Psikoteskerja, ToeflAcademy, JadiOJK, JadiPPG). Daftarnya ada di `app/platforms.py`. `/` mengalihkan ke sini. |
| **Dashboard platform** | `/p/<platform>` (mis. `/p/jadiasn`) | Satu dashboard per platform, bisa pindah ke platform lain lewat pemilih di atas. Isinya: **Keyword inti** (96 topik tetap di 15 platform, lihat `app/core.py`; tiap topik **diriset otomatis setiap hari** pukul 06:00 WIB (`TRENDWATCH_CORE_SCHEDULE`, `off` untuk mematikan; hanya berjalan selama aplikasi menyala) dan menampilkan keyword turunan beserta kenaikannya hari itu; bisa juga lewat tombol **Perbarui dari Google** atau `python -m app.cli core-refresh [--platform jadiasn] [--demo]`; data lama tidak ditimpa bila pengambilan gagal), **Keyword research** (di bawah, untuk custom topik), **Berita terbaru** dari Google Berita (RSS gratis; kata cari per platform, mis. JadiASN = "ASN", Psikoteskerja = "Psikotes kerja", ToeflAcademy = "TOEFL"; diperbarui otomatis tiap 3 jam, ubah dengan `TRENDWATCH_NEWS_TTL_HOURS`), dan **Riwayat keyword**. Pada tiap keyword pengguna memilih **Dipakai** atau **Tidak**; pilihan masuk ke tab **Riwayat keyword** platform itu. Riwayat berlaku untuk seluruh platform (tidak terikat topik yang sedang dicari), dilihat semua pengguna, tercatat siapa, topik, dan kapan; bisa diganti, dibatalkan, disaring (status, topik, kata), dan diunduh CSV. |
| **Keyword research** | di dalam dashboard platform | Ketik satu *seed keyword*, dapatkan ratusan keyword turunan dari **Google Autocomplete** (huruf a-z, awalan "apa itu/cara/harga", akhiran "terbaik/2026"; opsional ditambah **saran YouTube dan Bing**, gratis tanpa kunci), digabung dengan data **Google Trends** (naik **24 jam terakhir**, 7 hari, dan 90 hari, serta permintaan relatif; ada saringan "Naik 24 jam terakhir" untuk melihat hanya yang baru meledak). Tiap keyword diberi **maksud pencarian** (informasional, komersial, transaksional, navigasi) dan saran format konten (tabel tidak lagi menampilkan kolom permintaan, skor, dan tahap funnel; urutan "Paling relevan" memakai aturan internal). **Klik nama keyword** (mis. "cpns 2027") untuk melihat turunannya ("kapan cpns 2027 dibuka", "info cpns 2027", "jadwal cpns 2027", ...) dari saran pencarian, lengkap dengan maksud. Tersedia tampilan daftar dan kelompok topik; bisa disalin, diunduh CSV, dan disimpan ke Daily Discovery. **Tidak ada volume pencarian dan keyword difficulty**: keduanya tidak tersedia gratis dan sengaja tidak dikarang. |

### Mesin di belakang layar: "Riset topik" (`/api/research`)

Satu riset = 4 permintaan (satu per rentang waktu) + sampai 5 permintaan turunan. Semua memakai data
*pencarian terkait* Google Trends yang sama dengan yang dilihat manual, lalu digabung:

- **Baru naik**: naik hanya di 24 jam / 7 hari, belum terlihat di 30 / 90 hari (kandidat yang baru mulai ramai).
- **Naik konsisten**: naik di 3 rentang atau lebih.
- **Turunan**: keyword memuat semua kata topik (mis. "bumn 2026" untuk "bumn").
- Urutan: Breakout dulu, lalu paling banyak rentang yang naik, lalu kenaikan terbesar. Yang hanya populer ditaruh paling akhir dan disembunyikan secara bawaan.
- Turunan tingkat dua diambil dari keyword naik teratas (7 hari). Rentang atau turunan yang gagal dicatat di halaman tanpa menggagalkan seluruh riset.

- **Skor peluang (0–100)**: aturan sederhana buatan kami untuk memprioritaskan keyword: pertumbuhan (maks 40), konsistensi lintas
  rentang (25), sinyal dini (15), belum pernah tersimpan (10), memuat semua kata topik (10). Rinciannya muncul saat kursor diarahkan
  ke skor. Ini bukan data Google dan bukan volume pencarian.
- **Maksud pencarian**: tebakan dari kata di keyword (beli/harga, situs/aplikasi, informasi, kabar terbaru). Aturannya ada di
  `app/insight.py` dan bisa meleset; pakai sebagai petunjuk.
- **Kelompok**: keyword yang memakai kata pembeda sama (mis. "2026") dikumpulkan agar variasi tidak terbaca sebagai topik terpisah.
- **Hari tersimpan**: berapa hari keyword itu pernah tersimpan di Daily Discovery (Google Trends tidak punya riwayat).
- **Simpan ke Daily Discovery**: tombol di hasil riset menyimpan keyword yang tampil dan otomatis memasukkan topiknya ke Watchlist
  (hanya untuk satu negara, dan tidak untuk data contoh).
- **Belum pernah tersimpan**: setelah ada riwayat Daily Discovery untuk topik itu, keyword yang belum pernah tersimpan di hari-hari
  sebelumnya diberi tanda dan bisa disaring. Pada hari pertama belum ada pembanding, jadi tanda ini tidak ditampilkan.

Instagram dan TikTok **belum** termasuk: keduanya tidak punya sumber resmi gratis untuk keyword yang sedang naik.

### Mesin di belakang layar: "booming" (`/api/booming`)

Daftar diambil dari *pencarian terkait yang sedang naik* (rising related queries) milik Google Trends:

1. Pencarian yang **naik** diurutkan: **Breakout** (naik lebih dari 5.000%) lebih dulu, lalu menurut persentase kenaikan.
2. **Hanya yang naik yang ditampilkan.** Jika yang naik kurang dari 10, jumlahnya memang segitu; jika tidak ada, halaman
   menyatakannya dan menawarkan periode lebih panjang atau Riset topik. Pencarian yang sekadar populer tidak ikut
   (API: `rising_only=false` mengembalikan perilaku lama yang diisi populer).
   Tiap keyword menampilkan persen kenaikan dan skor peluang (dari 1 periode); klik keyword untuk mencari turunannya.
3. Pencarian yang sama dengan topiknya dan duplikat dibuang.
4. Bila ada data tren harian yang memuat topik itu (judul berita atau keyword), ditampilkan di bawahnya.

Angka dari Google bersifat **relatif** (0–100), bukan jumlah pencarian.

## Menjalankan

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

python -m uvicorn app.main:app --reload
```

Buka http://127.0.0.1:8000. Di **GitHub Codespaces**: **Code → Codespaces → Create codespace on main**,
jalankan perintah di atas, lalu klik **Open in Browser** pada notifikasi port 8000.
Pakai `python -m uvicorn`, bukan `uvicorn`, karena folder skrip pip belum tentu ada di PATH.

Centang **Pakai data contoh** untuk mencoba tampilan tanpa menghubungi Google. Data contoh ditandai jelas
sebagai bukan dari Google.

## Sumber data (penting)

Google tidak menyediakan API publik untuk data ini (API resminya masih alpha dan berbasis pendaftaran).
Pilihan di proyek ini, diatur lewat `TRENDWATCH_EXPLORE_SOURCE`:

| Nilai | Sumber | Catatan |
|---|---|---|
| `auto` (default) | Google langsung; jika gagal dan `SERPAPI_KEY` terisi, lanjut ke SerpApi | Paling praktis |
| `google` | Endpoint internal trends.google.com | Gratis, **tidak resmi**: bisa berubah atau diblokir kapan saja. Google sering membatasi IP datacenter (termasuk Codespaces) dengan HTTP 429. Dari laptop sendiri biasanya lebih lancar. |
| `serpapi` | [SerpApi](https://serpapi.com) | Perlu akun dan `SERPAPI_KEY`. Paket gratis ±250 pencarian/bulan. Satu pencarian Booming memakai 1 kuota, satu penjelajahan penuh hingga 3. |
| `mock` | Data contoh | Untuk demo dan pengembangan |

Semua hasil di-cache (Booming 1 jam, Jelajah 6 jam) sehingga pencarian yang sama tidak menembak sumber lagi.
Jika sumber gagal tetapi ada cache lama, cache lama ditampilkan dengan peringatan.

## Data harian otomatis (GitHub Actions)

`.github/workflows/collect.yml` berjalan setiap hari pukul 07.30 WIB: mengambil tren harian Indonesia dari RSS,
lalu men-commit hasilnya ke `data/snapshots.csv`. Dashboard memuat CSV ini saat menyala. Tab **Actions →
Ambil data tren harian → Run workflow** untuk mencoba manual. Jika pengambilan gagal, job berwarna merah dan
GitHub mengirim notifikasi.

```bash
python -m app.cli collect --geo ID            # ambil hari ini, perbarui data/snapshots.csv
python -m app.cli collect --geo ID --geo MY   # beberapa negara sekaligus
python -m app.cli import --csv data/snapshots.csv
python -m app.cli export --csv data/snapshots.csv
```

## Pantau topik setiap hari (Watchlist otomatis)

`.github/workflows/discover.yml` berjalan setiap hari pukul 08.00 WIB: meriset semua topik aktif di `data/watchlist.csv`
(sama dengan halaman Riset topik), lalu menyimpan keyword yang naik ke `data/discovery.csv`, lengkap dengan skor peluang.
Riwayat inilah yang membuat penanda **belum pernah tersimpan** dan **tersimpan N hari** bermakna.

**Menambah topik tanpa terminal**: tab **Actions → Pantau topik Watchlist harian → Run workflow**, isi kolom *topik*
(mis. `BUMN`). Topik langsung tersimpan dan ikut dipantau setiap hari. Bisa juga dari terminal:

```bash
python -m app.cli discover --add "BUMN"        # tambah topik lalu riset sekarang
python -m app.cli discover --windows 2         # hanya 24 jam + 7 hari (hemat kuota)
python -m app.cli discover --demo --add BUMN   # uji coba dengan data contoh, tidak menyimpan apa pun
python -m app.cli export-discovery             # tulis Watchlist dan hasil dari database lokal ke CSV
```

Aturan kegagalan: **semua topik gagal = job merah** (Anda mendapat notifikasi); **sebagian gagal = job hijau dengan peringatan
kuning**, karena Google sering menolak sebagian permintaan. Bila sumber gagal dan hanya ada cache lama, data lama **tidak** disimpan
sebagai data hari ini.

Catatan penting:
- Google sering menolak IP server (HTTP 429), termasuk runner GitHub. Untuk keandalan, isi secret **`SERPAPI_KEY`**
  (Settings → Secrets and variables → Actions). SerpApi gratis ±250 pencarian/bulan dan satu topik memakai 4 pencarian per hari,
  jadi dengan rentang 4 hanya muat ±2 topik; turunkan `rentang` ke 1 atau 2 bila topiknya lebih banyak.
- Dashboard memuat dua CSV ini saat menyala. Yang sudah ada di database lokal **tidak ditimpa**, dan topik yang Anda hapus di
  dashboard bisa muncul lagi dari CSV bila tidak dihapus juga di CSV. Edit Watchlist lokal baru terlihat oleh Actions setelah
  `python -m app.cli export-discovery` lalu commit dan push.

## Tes

```bash
python -m pytest
```

`.github/workflows/tests.yml` menjalankan tes yang sama pada setiap push.

Catatan jujur tentang tes: balasan Google dan SerpApi di tes disusun dari dokumentasi dan contoh publik
(bukan rekaman langsung). Tes membuktikan parser konsisten dengan format itu, bukan bahwa Google masih
memakai format yang sama hari ini. Cara memastikannya: jalankan tanpa data contoh dan lihat hasilnya.

## Variabel lingkungan

| Variabel | Default | Fungsi |
|---|---|---|
| `TRENDWATCH_EXPLORE_SOURCE` | `auto` | Sumber Booming/Jelajah: `auto`, `google`, `serpapi`, `mock` |
| `SERPAPI_KEY` | (kosong) | API key SerpApi (jangan di-commit; isi lewat variabel lingkungan atau Codespaces secrets) |
| `TRENDWATCH_BOOMING_TTL_MINUTES` | `60` | Masa berlaku cache Booming |
| `TRENDWATCH_EXPLORE_TTL_HOURS` | `6` | Masa berlaku cache Jelajah |
| `TRENDWATCH_GOOGLE_MIN_INTERVAL` | `1.0` | Jeda minimal (detik) antar permintaan ke Google |
| `TRENDWATCH_TZ_MINUTES` | `-420` | Selisih zona waktu untuk permintaan ke Google (WIB = -420) |
| `TRENDWATCH_SOURCE` | `rss` | Sumber tren harian: `rss` atau `mock` |
| `TRENDWATCH_GEO` | `ID` | Negara bawaan |
| `TRENDWATCH_DB` | `trendwatch.db` di folder proyek | Lokasi file database SQLite (akun, riwayat, cache). Di Docker otomatis `/data/trendwatch.db` |
| `TRENDWATCH_CSV` | `data/snapshots.csv` | CSV riwayat tren harian yang dimuat saat start |
| `TRENDWATCH_WATCHLIST_CSV` | `data/watchlist.csv` | CSV topik Watchlist |
| `TRENDWATCH_DISCOVERY_CSV` | `data/discovery.csv` | CSV hasil Daily Discovery |
| `TRENDWATCH_DISCOVER_WINDOWS` | `4` | Jumlah rentang waktu per topik untuk `discover` |
| `TRENDWATCH_TZ` | `Asia/Jakarta` | Zona waktu untuk menentukan "hari ini" |
| `TRENDWATCH_AUTH` | `on` | Login selalu wajib. Isi `off` hanya untuk pengembangan lokal (menonaktifkan seluruh login) |
| `TRENDWATCH_SESSION_HOURS` | `168` | Lama sesi login (7 hari) |
| `TRENDWATCH_SECRET` | (dibuat otomatis, disimpan di database) | Kunci penanda tangan sesi |
| `TRENDWATCH_ADMIN_EMAIL`, `TRENDWATCH_ADMIN_PASSWORD`, `TRENDWATCH_ADMIN_NAME` | (kosong) | Membuat satu akun saat start bila belum ada (berguna di hosting yang databasenya hilang saat restart) |
| `TRENDWATCH_SMTP_HOST`, `_PORT` (587), `_USER`, `_PASSWORD`, `_FROM`, `_SSL` | (kosong) | Server email untuk mengirim tautan lupa kata sandi |
| `TRENDWATCH_BASE_URL` | (alamat dari permintaan) | Alamat publik aplikasi untuk tautan di email reset, mis. `https://app.contoh.id` |
| `TRENDWATCH_COLLECT_MIN_INTERVAL` | `300` | Jeda minimal (detik) antar pengambilan tren harian |

## Akun dan login

Aplikasi memakai akun pengguna seperti aplikasi web pada umumnya; **login selalu wajib**.

- **Daftar** di `/daftar` (nama, email, kata sandi minimal 8 karakter), **masuk** di `/login`, **keluar** dari tombol di pojok kanan atas.
- **Akun** di `/akun`: ganti kata sandi. Setelah diganti, sesi di perangkat lain otomatis tidak berlaku.
- **Lupa kata sandi** di `/lupa`: tautan atur ulang (berlaku 1 jam, sekali pakai) dikirim ke email. Butuh server email (`TRENDWATCH_SMTP_*`).
  Tanpa SMTP, tautannya ditulis ke log server (peringatan "SMTP belum diatur") sehingga tetap bisa dicoba saat pengembangan.
- Kata sandi disimpan sebagai hash scrypt dengan garam acak; sesi berupa cookie HttpOnly bertanda tangan HMAC.
  Percobaan masuk dibatasi (5 kali salah per akun dalam 15 menit; 10 per alamat IP dalam 5 menit), pendaftaran dan permintaan reset juga dibatasi.
- Untuk pengembangan lokal tanpa login: `TRENDWATCH_AUTH=off`.
- Semua akun adalah pengguna biasa dan melihat data yang sama; belum ada peran admin atau halaman kelola pengguna.
- **Di mana akun disimpan:** di file SQLite (`trendwatch.db` di folder proyek; lokasinya tercatat di log saat aplikasi menyala). File ini tidak ikut Git. Menyalin atau memindahkan proyek tanpa file itu berarti mulai dari akun kosong.
- **Hosting/Docker:** sistem file kontainer hilang saat dibuat ulang (deploy ulang, restart di hosting gratis). Pasang volume/disk permanen di `/data` (Docker: `-v cerekey-data:/data`),
  atau isi `TRENDWATCH_ADMIN_EMAIL/PASSWORD` agar satu akun dibuat ulang tiap start. Pakai HTTPS.

## Mempublikasikan (opsional)

Ada `Dockerfile`; platform seperti Render atau Railway bisa membangunnya dari repo ini. Jika dashboard bisa
diakses publik, gunakan HTTPS: pencarian memakai kuota SerpApi dan menghubungi
Google atas nama Anda. Database SQLite di hosting gratis biasanya hilang saat restart; riwayat tren harian tetap
aman karena dimuat ulang dari `data/snapshots.csv`.

## Arsitektur

```
Pengguna ─► halaman (static/) ─► main.py (API) ─► explore_service.py ─► explore.py ─► Google / SerpApi / contoh
                                       │               └─► cache (SQLite)
                                       └─► db.py ◄── collector.py ◄── RSS tren harian ◄── GitHub Actions
```

Diagram lengkap (alur, ERD, urutan harian, alur Booming) ada di [docs/arsitektur.md](docs/arsitektur.md),
dan kerangka bab laporan PKL di [docs/kerangka-laporan.md](docs/kerangka-laporan.md).

- `app/explore.py`: sumber data Booming/Jelajah (`GoogleDirectProvider`, `SerpApiProvider`, `MockExploreProvider`,
  `ChainProvider`). Semua mengikuti antarmuka yang sama, jadi mengganti sumber tidak menyentuh API atau halaman.
- `app/explore_service.py`: validasi masukan, cache, peringkat "booming" (`rank_booming`).
- `app/suggest.py`: saran keyword dari Google Autocomplete (tidak resmi), dengan jeda dan penanda saat diblokir.
- `app/keyword_research.py`: menggabungkan saran + Trends, skor peluang, kelompok, ringkasan, dan cache (6 jam; hasil sebagian 10 menit).
- `app/insight.py`: skor peluang, maksud pencarian, dan pengelompokan keyword (aturan sederhana di atas data Google Trends).
- `app/categories.py`: daftar kategori Google Trends dan rentang waktu.
- `app/collector.py`, `app/db.py`, `app/cli.py`: tren harian dari RSS, penyimpanan, dan perintah terjadwal.
- `app/main.py`: `/api/keywords` (`kanal=youtube,bing` opsional), `/api/keywords/turunan` (turunan satu keyword), `/api/booming`, `/api/explore`, `/api/categories`, `/api/overview`, `/api/history`, `/api/top`,
  `/api/export.csv`, `/api/collect`, `/api/demo-seed`, `/api/health`.

## Keterbatasan yang perlu dicatat di laporan

- Data Booming dan Jelajah berasal dari sumber tidak resmi atau pihak ketiga. Format dan ketersediaannya
  bisa berubah; arsitektur dibuat agar sumber mudah diganti.
- Angka bersifat relatif, bukan volume pencarian. Topik dengan volume rendah bisa tidak menghasilkan data.
- Jumlah pencarian yang tercatat "naik" bisa kurang dari 10; sisanya diisi yang populer dan diberi label.
- Daftar kategori hanya memuat tingkat teratas dan beberapa subkategori.
- Tren harian (RSS) hanya memuat sekitar 10–20 tren teratas dan hanya tren saat ini; riwayat dibangun lewat
  pengambilan harian, jadi status naik/turun baru bermakna setelah beberapa hari.

## Ide pengembangan

- Penjadwalan Booming otomatis untuk daftar topik instansi (mis. BUMN, kementerian) dan menyimpan riwayatnya.
- Notifikasi (email/Telegram) bila ada pencarian Breakout baru untuk topik pantauan.
- Ekspor ke Excel (.xlsx) dengan grafik.
