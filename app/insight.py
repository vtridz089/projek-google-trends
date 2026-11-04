"""Analisis tambahan di atas data Google Trends: skor peluang, maksud pencarian, dan kelompok keyword.

Semuanya aturan sederhana yang bisa dijelaskan, BUKAN data dari Google dan bukan volume pencarian.
Tujuannya mengisi hal yang tidak disediakan Google Trends: prioritas, pengelompokan, dan riwayat.
"""
from __future__ import annotations

import math
import re
from collections import Counter

# ----------------------------------------------------------------------------- maksud pencarian
# Urutan penting: yang pertama cocok menang. Frasa dicocokkan sebagai kata utuh.
_INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("transaksi", ("beli", "harga", "murah", "promo", "diskon", "jual", "order", "pesan", "tiket", "cicil",
                   "kredit", "sewa", "kode promo", "gratis ongkir", "daftar harga", "lowongan")),
    ("navigasi", ("login", "masuk akun", "website", "situs", "situs resmi", "portal", "aplikasi", "app",
                  "unduh", "download", "link", "pendaftaran", "registrasi", "akun")),
    ("informasi", ("apa itu", "adalah", "cara", "syarat", "jadwal", "pengumuman", "hasil", "pengertian",
                   "tutorial", "panduan", "sejarah", "berapa", "kapan", "siapa", "mengapa", "kenapa",
                   "contoh", "daftar", "struktur", "visi misi", "gaji", "tugas", "fungsi")),
    ("berita", ("hari ini", "terbaru", "terkini", "berita", "update", "viral", "kasus", "demo", "live")),
]
INTENT_LABELS = {"transaksi": "Mau beli / daftar harga", "navigasi": "Cari situs / aplikasi",
                 "informasi": "Cari informasi", "berita": "Cari kabar terbaru", "umum": "Umum"}


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def intent(keyword: str) -> str:
    """Tebak maksud pencarian dari kata-kata di keyword (aturan heuristik, bukan data Google)."""
    padded = " " + " ".join(_words(keyword)) + " "
    for name, terms in _INTENT_RULES:
        if any(f" {t} " in padded for t in terms):
            return name
    return "umum"


# ----------------------------------------------------------------------------- skor peluang
WEIGHTS = {"pertumbuhan": 40, "konsistensi": 25, "dini": 15, "baru": 10, "relevan": 10}


def opportunity(row: dict) -> dict:
    """Skor prioritas 0-100 yang transparan. ``bagian`` menunjukkan asal tiap poin.

    - pertumbuhan (maks 40): Breakout penuh; selain itu naik secara logaritmik terhadap persentase.
    - konsistensi (maks 25): berapa dari 4 rentang waktu keyword ini tercatat naik.
    - dini (maks 15): naik hanya di rentang pendek = sinyal awal sebelum ramai.
    - baru (maks 10): belum pernah tersimpan di Daily Discovery (hanya bila ada riwayat).
    - relevan (maks 10): memuat semua kata topik.
    """
    if row["rising_count"] == 0:
        parts = {k: 0 for k in WEIGHTS}
    else:
        growth = WEIGHTS["pertumbuhan"] if row["breakout"] else min(
            WEIGHTS["pertumbuhan"], WEIGHTS["pertumbuhan"] * math.log10(1 + row["max_value"]) / math.log10(5001))
        parts = {
            "pertumbuhan": round(growth),
            "konsistensi": round(WEIGHTS["konsistensi"] * row["rising_count"] / 4),
            "dini": WEIGHTS["dini"] if row["pattern"] == "baru" else 0,
            "baru": WEIGHTS["baru"] if row.get("belum_pernah") is True else 0,
            "relevan": WEIGHTS["relevan"] if row.get("turunan") else 0,
        }
    total = min(100, sum(parts.values()))
    level = "tinggi" if total >= 70 else "sedang" if total >= 40 else "rendah"
    return {"skor": total, "level": level, "bagian": parts}


def opportunity_one_period(item: dict, topic: str, time_range: str) -> dict:
    """Skor peluang bila hanya satu periode yang diperiksa (halaman Booming).

    Memakai rumus yang sama dengan ``opportunity``; rentang lain dianggap belum terbukti naik, jadi skornya bisa
    lebih rendah daripada skor di Riset topik (yang memeriksa 4 rentang). Periode "now ..." (jam/hari) dihitung
    sebagai sinyal dini; periode bulan/tahun tidak."""
    topic_words = set(_words(topic))
    return opportunity({
        "rising_count": 1, "max_value": int(item.get("value") or 0), "breakout": bool(item.get("breakout")),
        "pattern": "baru" if str(time_range).startswith("now ") else "naik", "belum_pernah": None,
        "turunan": bool(topic_words) and topic_words <= set(_words(item["query"])),
    })


# ----------------------------------------------------------------------------- kelompok keyword
_STOP = {"yang", "di", "dan", "ke", "dari", "untuk", "dengan", "ini", "itu", "atau", "pada", "the", "of", "a"}


def clusters(rows: list[dict], topic: str) -> list[dict]:
    """Kelompokkan keyword menurut kata pembeda (kata di luar topik) yang dipakai bersama.

    Tiap keyword masuk ke kata pembeda yang paling sering dipakai di antara keyword lain
    (minimal 2 keyword). Sisanya ke "Lainnya". Contoh: "bumn 2026" dan "bumn 2026 gratis" ke kelompok "2026".
    """
    topic_words = set(_words(topic))
    mods: dict[str, list[str]] = {}
    for r in rows:
        ws = [w for w in dict.fromkeys(_words(r["query"])) if w not in topic_words and w not in _STOP]
        mods[r["query"]] = ws
    freq = Counter(w for ws in mods.values() for w in ws)
    groups: dict[str, list[dict]] = {}
    for r in rows:
        shared = [w for w in mods[r["query"]] if freq[w] >= 2]
        key = max(shared, key=lambda w: (freq[w], -mods[r["query"]].index(w))) if shared else ""
        groups.setdefault(key, []).append(r)
    out = []
    for key, members in groups.items():
        out.append({"nama": key or "Lainnya", "ukuran": len(members),
                    "skor_terbaik": max(m["peluang"]["skor"] for m in members),
                    "keywords": [m["query"] for m in members]})
    out.sort(key=lambda g: (g["nama"] == "Lainnya", -g["skor_terbaik"], -g["ukuran"], g["nama"]))
    return out


def enrich(result: dict) -> dict:
    """Tambahkan maksud, skor peluang, dan kelompok ke hasil riset."""
    for r in result["items"]:
        r["maksud"] = intent(r["query"])
        r["peluang"] = opportunity(r)
    result["kelompok"] = clusters(result["items"], result["topic"])
    for g in result["kelompok"]:
        for q in g["keywords"]:
            next(r for r in result["items"] if r["query"] == q)["kelompok"] = g["nama"]
    return result
