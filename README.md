# TrendWatch

Dashboard untuk memantau **keyword yang sedang naik** di Google Trends (tren pencarian harian per negara).
Dibuat sebagai proyek PKL: FastAPI + SQLite + dashboard web (Chart.js).

## Cara menjalankan

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

Buka http://127.0.0.1:8000

- **Ambil data sekarang**: mengambil tren hari ini dari Google Trends lalu menyimpannya.
- **Isi data demo**: mengisi riwayat 14 hari dengan data contoh, supaya grafik dan status
  (baru / naik / turun) langsung bisa dicoba tanpa menunggu data menumpuk.

Variabel lingkungan (opsional):

| Variabel | Default | Fungsi |
|---|---|---|
| `TRENDWATCH_SOURCE` | `rss` | Sumber data: `rss` (nyata) atau `mock` (contoh) |
| `TRENDWATCH_GEO` | `ID` | Negara bawaan |
| `TRENDWATCH_DB` | `trendwatch.db` | Lokasi file database SQLite |

## Tes

```bash
pytest
```

## Arsitektur

```
Google Trends RSS ──► collector.py ──► db.py (SQLite) ──► main.py (API) ──► static/index.html
   (atau mock)        (antarmuka tetap)   snapshot harian     /api/...         dashboard
```

- `app/collector.py` — semua sumber data memakai antarmuka `fetch(geo, day)`. Sumber nyata
  memakai feed RSS publik `https://trends.google.com/trending/rss?geo=ID`; ada juga `MockCollector`.
  Kalau Google mengubah feed, atau nanti ingin pindah ke API resmi / API berbayar, **cukup
  tambah satu kelas collector baru**, bagian lain tidak berubah.
- `app/db.py` — satu baris per (negara, keyword, hari). Di sini status dihitung:
  `baru`, `muncul lagi`, `naik`, `turun`, `tetap`.
- `app/main.py` — endpoint `/api/overview`, `/api/history`, `/api/top`, `/api/collect`, `/api/demo-seed`.

## Keterbatasan yang perlu dicatat di laporan

- Feed RSS hanya memuat sekitar 10–20 tren teratas per negara dan hanya tren **saat ini**.
  Riwayat dibangun sendiri dengan menjalankan pengambilan data setiap hari.
- Traffic berupa perkiraan dari Google (misalnya `50K+`), bukan volume pencarian pasti.
- Ini bukan API resmi. Google API Trends resmi masih alpha dan berbasis pendaftaran; pytrends
  (library tidak resmi) sudah diarsipkan. Karena itu collector dibuat mudah diganti.

## Ide pengembangan

- Penjadwal harian (cron / APScheduler) untuk mengambil data otomatis.
- Ekspor CSV/Excel untuk laporan.
- Grafik minat per keyword yang dipilih sendiri (butuh sumber data interest-over-time).
- Notifikasi saat ada keyword baru yang traffic-nya besar.
