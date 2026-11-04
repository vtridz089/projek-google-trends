# Kerangka Laporan PKL: TrendWatch

Kerangka ini hanya titik awal. Sesuaikan penomoran dan istilah dengan pedoman laporan PKL dari
kampus/sekolah dan instansi Anda. Bagian bertanda **[ISI]** adalah hal yang hanya Anda yang tahu.

## Bab I. Pendahuluan
- **Latar belakang:** pentingnya memantau topik yang sedang dicari masyarakat (konten, pemasaran,
  layanan publik). **[ISI]** kaitkan dengan kebutuhan instansi tempat PKL.
- **Rumusan masalah:** bagaimana membangun dashboard yang menampilkan keyword yang sedang naik
  dari Google Trends secara otomatis dan mudah dibaca?
- **Tujuan dan manfaat.**
- **Batasan masalah:** hanya tren harian per negara; traffic berupa perkiraan; bukan API resmi
  (lihat bagian keterbatasan di `README.md`).
- **Waktu dan tempat PKL.** **[ISI]**

## Bab II. Landasan Teori / Tinjauan Pustaka
- Google Trends dan arti angka minat pencarian (relatif, bukan volume pasti).
- REST API dan FastAPI.
- Basis data relasional dan SQLite.
- Pengambilan data lewat RSS; penjadwalan dengan GitHub Actions (CI/CD).
- Visualisasi data (Chart.js).

## Bab III. Analisis dan Perancangan
- **Analisis kebutuhan** (fungsional: cari topik lalu tampilkan top 10 yang sedang booming, jelajah keyword dan kategori dari waktu ke waktu, tren harian, filter, unduh CSV; nonfungsional:
  tahan gangguan sumber data, mudah diganti, aman bila dipublikasikan). **[ISI]** kebutuhan dari pembimbing.
- **Perbandingan sumber data** (API resmi alpha, pytrends yang sudah diarsipkan, API berbayar seperti SerpApi,
  endpoint internal Google, RSS tren harian) dan alasan memilih kombinasi endpoint internal + SerpApi cadangan + RSS.
- **Arsitektur sistem, rancangan database, alur harian:** ambil dari [arsitektur.md](arsitektur.md).
- **Rancangan antarmuka.** **[ISI]** tambahkan tangkapan layar.

## Bab IV. Implementasi dan Pengujian
- Struktur proyek dan penjelasan tiap modul (`collector`, `explore`, `explore_service`, `db`, `main`, `cli`, halaman di `static/`).
- Analisis tambahan (`insight.py`): skor peluang, maksud pencarian, pengelompokan, dan riwayat; jelaskan sebagai aturan heuristik, bukan data Google, beserta rumus dan batasannya.
- Pemantauan harian (`discover.yml`, `cmd_discover`): penyimpanan riwayat sebagai CSV di Git, aturan kegagalan (semua gagal = merah, sebagian = peringatan), dan penolakan cache lama.
- Cara kerja "Riset topik" (`merge_research`, `run_research`): penggabungan 4 rentang waktu, pola baru/konsisten, dan turunan tingkat dua.
- Cara kerja peringkat "booming" (`rank_booming`): Breakout, persentase kenaikan, dan pengisian dengan pencarian populer.
- Implementasi penanganan kegagalan (percobaan ulang, pesan error, job Actions merah).
- **Pengujian:** hasil `python -m pytest`, uji manual tiap tombol dan filter, uji pengambilan data
  nyata. **[ISI]** tangkapan layar hasil tes dan dashboard.
- **Pembahasan:** data yang terkumpul selama PKL, contoh keyword yang naik, dan apakah relevan
  bagi instansi. **[ISI]**

## Bab V. Penutup
- Kesimpulan.
- **Keterbatasan:** hanya 10–20 tren teratas dan tren saat ini; traffic perkiraan; sumber tidak resmi
  sehingga bisa berubah sewaktu-waktu; belum ada pemantauan keyword pilihan sendiri.
- **Saran pengembangan:** sumber interest-over-time, notifikasi, ekspor Excel.

## Lampiran
- Kode sumber (tautan repositori GitHub).
- Tangkapan layar dashboard dan hasil tes.
- Contoh berkas `data/snapshots.csv`.
