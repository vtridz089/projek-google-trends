"""Daftar kategori Google Trends (parameter ``cat``) dan rentang waktu untuk halaman Jelajah.

ID kategori dicocokkan dengan daftar resmi yang dipublikasikan SerpApi dan SearchApi
(per November 2026). Google punya ratusan subkategori; di sini hanya tingkat teratas
ditambah beberapa yang sering dipakai. Untuk menambah, cukup tambah baris baru.
"""
from __future__ import annotations

CATEGORIES: list[tuple[int, str]] = [
    (0, "Semua kategori"),
    (3, "Seni & Hiburan"),
    (47, "Otomotif & Kendaraan"),
    (44, "Kecantikan & Kebugaran"),
    (22, "Buku & Sastra"),
    (12, "Bisnis & Industri"),
    (5, "Komputer & Elektronik"),
    (31, "  Pemrograman"),
    (7, "Keuangan"),
    (107, "  Investasi"),
    (71, "Makanan & Minuman"),
    (8, "Game"),
    (45, "Kesehatan"),
    (65, "Hobi & Waktu Luang"),
    (11, "Rumah & Taman"),
    (13, "Internet & Telekomunikasi"),
    (958, "Pekerjaan & Pendidikan"),
    (19, "Hukum & Pemerintahan"),
    (16, "Berita"),
    (299, "Komunitas Online"),
    (14, "Masyarakat & Orang"),
    (66, "Hewan Peliharaan"),
    (29, "Properti"),
    (533, "Referensi"),
    (174, "Sains"),
    (18, "Belanja"),
    (20, "Olahraga"),
    (67, "Perjalanan"),
]

CATEGORY_NAMES: dict[int, str] = {cid: name.strip() for cid, name in CATEGORIES}

# (nilai untuk Google, nama tampilan)
TIMEFRAMES: list[tuple[str, str]] = [
    ("now 1-d", "24 jam terakhir"),
    ("now 7-d", "7 hari terakhir"),
    ("today 1-m", "30 hari terakhir"),
    ("today 3-m", "90 hari terakhir"),
    ("today 12-m", "12 bulan terakhir"),
    ("today 5-y", "5 tahun terakhir"),
    ("all", "Sejak 2004"),
]

GPROP_LABELS: dict[str, str] = {
    "": "Pencarian web",
    "images": "Google Gambar",
    "news": "Google Berita",
    "youtube": "YouTube",
    "froogle": "Google Belanja",
}
