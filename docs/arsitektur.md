# Arsitektur TrendWatch

Diagram di bawah memakai Mermaid, jadi tampil otomatis di GitHub. Untuk laporan, buka halaman ini
di GitHub lalu ambil tangkapan layar, atau tempel kode diagram ke https://mermaid.live untuk diekspor
sebagai gambar.

## 1. Alur data

```mermaid
flowchart LR
    G[Google Trends<br/>RSS Daily Search Trends] -->|fetch + retry| C[collector.py]
    C --> D[(SQLite<br/>snapshots)]
    A[GitHub Actions<br/>setiap hari 07.30 WIB] -->|python -m app.cli collect| C
    A -->|commit| F[data/snapshots.csv]
    F -->|impor saat start| D
    D --> API[FastAPI<br/>main.py]
    API --> UI[Dashboard<br/>static/index.html]
    API -->|/api/export.csv| X[Unduhan CSV / Excel]
```

## 2. Rancangan database

Satu tabel. Satu baris mewakili satu keyword pada satu hari di satu negara
(`UNIQUE (geo, keyword, day)`), sehingga pengambilan ulang di hari yang sama
hanya memperbarui nilainya.

```mermaid
erDiagram
    SNAPSHOTS {
        int id PK
        text geo "kode negara 2 huruf; XX = data demo"
        text keyword "tidak peka huruf besar/kecil"
        text day "YYYY-MM-DD"
        text traffic_text "mis. 50K+"
        int traffic_num "perkiraan angka dari traffic_text"
        text pub_date
        text news_title
        text news_url
        text news_source
        text collected_at
    }
```

## 3. Urutan pengambilan harian

```mermaid
sequenceDiagram
    participant Cron as GitHub Actions
    participant CLI as app.cli collect
    participant DB as SQLite
    participant G as Google Trends RSS
    participant Git as Repo (data/snapshots.csv)

    Cron->>CLI: jalankan (07.30 WIB)
    CLI->>DB: muat riwayat dari CSV
    CLI->>G: GET /trending/rss?geo=ID
    alt sukses
        G-->>CLI: daftar tren
        CLI->>DB: simpan (upsert per hari)
        CLI->>Git: tulis CSV, lalu commit dan push
    else gagal (jaringan, 403, feed kosong)
        G-->>CLI: error
        CLI-->>Cron: kode keluar 1 (job merah, ada notifikasi)
    end
```

## 4. Aturan status keyword

Status dihitung dengan membandingkan hari terakhir yang ada datanya dengan hari sebelumnya:

| Status | Arti |
|---|---|
| `baseline` | Baru ada data satu hari, belum ada pembanding |
| `baru` | Belum pernah muncul di data sebelumnya |
| `muncul lagi` | Pernah muncul, tetapi tidak ada di hari sebelumnya |
| `naik` / `turun` / `tetap` | Perkiraan traffic dibanding hari sebelumnya |

## 5. Keputusan desain

- **Collector berantarmuka tetap.** Sumber data tidak resmi dan bisa berubah; mengganti sumber
  tidak boleh menyentuh API maupun dashboard.
- **Riwayat disimpan sebagai CSV di Git.** Server gratis sering menghapus database saat restart;
  CSV membuat riwayat tahan lama dan perubahannya bisa diperiksa.
- **Data demo dipisah (geo `XX`)** agar tidak mencemari data nyata atau CSV.
- **Kegagalan dibuat terlihat.** Pengambilan yang gagal membuat job Actions merah, bukan sukses palsu.
- **Pembatasan pengambilan** (jeda minimal per negara) agar tidak membanjiri sumber data.
