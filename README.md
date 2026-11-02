# TrendWatch

Dashboard untuk memantau **keyword yang sedang naik** di Google Trends (tren pencarian harian per negara).
Dibuat sebagai proyek PKL: FastAPI + SQLite + dashboard web (Chart.js), dengan pengambilan data
harian otomatis lewat GitHub Actions.

## Menjalankan dashboard

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

python -m uvicorn app.main:app --reload
```

Buka http://127.0.0.1:8000

Di **GitHub Codespaces**: buka repo, **Code → Codespaces → Create codespace on main**, jalankan
perintah di atas (tanpa langkah venv), lalu klik **Open in Browser** pada notifikasi port 8000.
Pakai `python -m uvicorn`, bukan `uvicorn`, karena folder skrip pip belum tentu ada di PATH.

Di dashboard:

- **Ambil data sekarang**: mengambil tren hari ini dari Google Trends dan menyimpannya
  (dibatasi satu kali per 5 menit per negara).
- **Isi data demo**: mengisi riwayat 14 hari dengan data contoh. Data demo selalu masuk ke
  pilihan **Data demo (XX)** sehingga tidak bercampur dengan data nyata.
- **Unduh CSV**: riwayat 30 hari terakhir untuk negara yang dipilih (siap dibuka di Excel).
- **Filter**: cari keyword, saring menurut status, dan **Fokus topik** (kata kunci dipisah koma,
  mis. `beasiswa, cpns`) untuk hanya melihat tren yang relevan dengan bidang Anda.

## Data harian otomatis (GitHub Actions)

`.github/workflows/collect.yml` berjalan setiap hari pukul 07.30 WIB: mengambil tren Indonesia,
lalu men-commit hasilnya ke `data/snapshots.csv`. Riwayat jadi tersimpan di Git (berkas teks,
mudah dilihat perubahannya), dan dashboard memuat CSV ini otomatis saat menyala.

Cara mencoba tanpa menunggu besok: buka tab **Actions → Ambil data tren harian → Run workflow**.
Kalau pengambilan gagal (Google menolak atau format feed berubah), job berwarna merah dan GitHub
mengirim notifikasi. Setelah itu di Codespaces/komputer Anda, jalankan `git pull` lalu restart
server agar riwayat terbaru termuat.

Perintah yang sama bisa dijalankan manual:

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

## Variabel lingkungan

| Variabel | Default | Fungsi |
|---|---|---|
| `TRENDWATCH_SOURCE` | `rss` | Sumber data: `rss` (nyata) atau `mock` (contoh, masuk ke geo `XX`) |
| `TRENDWATCH_GEO` | `ID` | Negara bawaan |
| `TRENDWATCH_DB` | `trendwatch.db` | Lokasi file database SQLite |
| `TRENDWATCH_CSV` | `data/snapshots.csv` | CSV riwayat yang dimuat saat start |
| `TRENDWATCH_TZ` | `Asia/Jakarta` | Zona waktu untuk menentukan "hari ini" |
| `TRENDWATCH_PASSWORD` | (kosong) | Jika diisi, seluruh dashboard dan API meminta login (HTTP Basic) |
| `TRENDWATCH_USER` | `admin` | Nama pengguna untuk login di atas |
| `TRENDWATCH_COLLECT_MIN_INTERVAL` | `300` | Jeda minimal (detik) antar pengambilan dari Google |

## Mempublikasikan (opsional)

Ada `Dockerfile`; platform seperti Render atau Railway bisa membangunnya langsung dari repo ini.
Jika dashboard bisa diakses publik, **isi `TRENDWATCH_PASSWORD`** (dan gunakan HTTPS), karena tombol
pengambilan data dan data demo bisa dipanggil siapa saja. Catatan: database SQLite di layanan hosting
gratis biasanya hilang saat restart; riwayat tetap aman karena dimuat ulang dari `data/snapshots.csv`.

## Arsitektur

```
Google Trends RSS ──► collector.py ──► db.py (SQLite) ──► main.py (API) ──► static/index.html
   (atau mock)        (antarmuka tetap)   snapshot harian     /api/...         dashboard
                                              ▲
                          data/snapshots.csv ─┘  (diisi GitHub Actions tiap hari)
```

Diagram lengkap (alur, ERD, urutan harian) ada di [docs/arsitektur.md](docs/arsitektur.md),
dan kerangka bab laporan PKL di [docs/kerangka-laporan.md](docs/kerangka-laporan.md).

- `app/collector.py`: semua sumber data memakai antarmuka `fetch(geo, day)`. Sumber nyata memakai
  feed RSS publik `https://trends.google.com/trending/rss?geo=ID` dengan percobaan ulang untuk
  gangguan sementara. Kalau Google mengubah feed, atau nanti ingin pindah ke API resmi / API
  berbayar, **cukup tambah satu kelas collector baru**.
- `app/db.py`: satu baris per (negara, keyword, hari); status dihitung di sini
  (`baru`, `muncul lagi`, `naik`, `turun`, `tetap`); impor/ekspor CSV.
- `app/main.py`: `/api/overview`, `/api/history`, `/api/top`, `/api/export.csv`,
  `/api/collect`, `/api/demo-seed`, `/api/health`.
- `app/cli.py`: perintah untuk pengambilan terjadwal.

## Keterbatasan yang perlu dicatat di laporan

- Feed RSS hanya memuat sekitar 10–20 tren teratas per negara dan hanya tren **saat ini**.
  Riwayat dibangun sendiri lewat pengambilan harian, jadi status naik/turun baru bermakna
  setelah beberapa hari data terkumpul.
- Traffic berupa perkiraan dari Google (misalnya `50K+`), bukan volume pencarian pasti.
- Ini bukan API resmi. API Trends resmi masih alpha dan berbasis pendaftaran, dan pytrends
  (library tidak resmi) sudah diarsipkan. Karena itu collector dibuat mudah diganti.
- Belum ada pemantauan keyword pilihan sendiri dengan grafik minat dari waktu ke waktu
  (interest-over-time). Itu membutuhkan sumber data lain dan menjadi pengembangan lanjutan.

## Ide pengembangan

- Sumber data interest-over-time untuk keyword pilihan (API resmi bila sudah terbuka, atau API berbayar).
- Notifikasi (email/Telegram) saat ada keyword baru dengan traffic besar.
- Ekspor ke Excel (.xlsx) dengan grafik.
