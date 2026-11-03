# TrendWatch

Dashboard Google Trends untuk proyek PKL. Ketik sebuah topik (misalnya **BUMN**) dan lihat **10 pencarian
yang sedang booming** untuk topik itu. Ada juga halaman untuk menjelajah keyword dari waktu ke waktu dan
tren harian per negara.

FastAPI + SQLite + halaman web biasa (Chart.js hanya di halaman Jelajah).

## Tiga halaman

| Halaman | Alamat | Fungsi |
|---|---|---|
| **Booming per topik** | `/` | Ketik topik, tampil top 10 pencarian yang sedang naik paling tajam. Filter periode (24 jam sampai 90 hari), wilayah, dan kategori. Hasil bisa diunduh sebagai CSV. |
| **Tren hari ini** | `/hari-ini` | Daftar tren harian per negara dari feed RSS, dengan status baru/naik/turun dibanding kemarin. |
| **Jelajah keyword** | `/explore` | Bandingkan hingga 5 keyword: grafik minat dari waktu ke waktu, query terkait, dan minat per wilayah. |

### Cara kerja "booming"

Daftar diambil dari *pencarian terkait yang sedang naik* (rising related queries) milik Google Trends:

1. Pencarian yang **naik** diurutkan: **Breakout** (naik lebih dari 5.000%) lebih dulu, lalu menurut persentase kenaikan.
2. Jika yang naik kurang dari 10, sisanya diisi pencarian **populer** (sering dicari). Keduanya diberi label
   berbeda supaya yang populer tidak disangka ikut naik.
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
| `TRENDWATCH_DB` | `trendwatch.db` | Lokasi file database SQLite |
| `TRENDWATCH_CSV` | `data/snapshots.csv` | CSV riwayat tren harian yang dimuat saat start |
| `TRENDWATCH_TZ` | `Asia/Jakarta` | Zona waktu untuk menentukan "hari ini" |
| `TRENDWATCH_PASSWORD` | (kosong) | Jika diisi, seluruh dashboard dan API meminta login (HTTP Basic) |
| `TRENDWATCH_USER` | `admin` | Nama pengguna untuk login di atas |
| `TRENDWATCH_COLLECT_MIN_INTERVAL` | `300` | Jeda minimal (detik) antar pengambilan tren harian |

## Mempublikasikan (opsional)

Ada `Dockerfile`; platform seperti Render atau Railway bisa membangunnya dari repo ini. Jika dashboard bisa
diakses publik, **isi `TRENDWATCH_PASSWORD`** dan gunakan HTTPS: pencarian memakai kuota SerpApi dan menghubungi
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
- `app/categories.py`: daftar kategori Google Trends dan rentang waktu.
- `app/collector.py`, `app/db.py`, `app/cli.py`: tren harian dari RSS, penyimpanan, dan perintah terjadwal.
- `app/main.py`: `/api/booming`, `/api/explore`, `/api/categories`, `/api/overview`, `/api/history`, `/api/top`,
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
