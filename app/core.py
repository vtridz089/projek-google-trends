"""Keyword inti per platform: daftar tetap yang dipantau lewat Google Trends (minat 0-100 per hari, 90 hari).

Satu keyword = satu permintaan ke Google Trends (garis waktu harian). Hasilnya disimpan di tabel ``core_trends``
sehingga halaman tidak perlu memanggil Google setiap dibuka. Pembaruan dijalankan lewat tombol di dashboard atau
``python -m app.cli core-refresh`` (cocok untuk dijadwalkan harian). Pembaruan yang gagal tidak menimpa data lama.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import closing

from . import db
from .explore import ExploreError, get_provider

STALE_SECONDS = 24 * 3600
WINDOW = "today 3-m"          # 90 hari, per hari
MIN_POINTS = 14               # kurang dari ini: data terlalu sedikit
MAX_CONSECUTIVE_FAILS = 2     # berhenti lebih awal bila Google menolak berturut-turut
GEO = "ID"

CORE: dict[str, tuple[str, ...]] = {
    "jadiasn": ("CPNS 2026", "SOAL CPNS", "BIMBEL CPNS", "TRYOUT CPNS", "FORMASI CPNS", "BUKU CPNS",
                "SELEKSI CPNS", "CPNS 2027", "REKRUTMEN CPNS", "SYARAT CPNS", "GAJI PNS"),
    "jadipppk": ("TES PPPK", "SOAL PPPK", "BIMBEL PPPK", "TRYOUT PPPK", "FORMASI PPPK", "GAJI PPPK"),
    "jadibumn": ("RBB BUMN", "SOAL BUMN", "BIMBEL BUMN", "TRYOUT BUMN", "LOKER BUMN", "BUKU BUMN",
                 "SELEKSI BUMN", "PSIKOTES BUMN"),
    "jadipcpm": ("PCPM BI", "SOAL PCPM BI", "BIMBEL PCPM BI", "TRYOUT PCPM BI", "GAJI PCPM BI"),
    "jadiojk": ("PCAM OJK", "SOAL OJK", "BIMBEL OJK", "TRYOUT OJK", "JURUSAN OJK"),
    "psikoteskerja": ("PSIKOTES KERJA", "SOAL PSIKOTES KERJA", "BIMBEL PSIKOTES KERJA",
                      "TRYOUT PSIKOTES KERJA", "TIPS PSIKOTES KERJA"),
    "jadicerebrum": ("UTBK", "SOAL UTBK", "BIMBEL UTBK", "TRYOUT UTBK", "JURUSAN KULIAH", "BUKU UTBK",
                     "SNBT", "SNBP", "TKA"),
    "toeflacademy": ("TES TOEFL", "SOAL TOEFL", "SERTIFIKAT TOEFL", "KURSUS TOEFL", "SIMULASI TOEFL"),
    "jadibeasiswa": ("LPDP", "SOAL LPDP", "BIMBEL LPDP", "TRYOUT LPDP", "TIPS LPDP", "SELEKSI LPDP"),
    "jadipolisi": ("PENERIMAAN POLRI", "SOAL POLRI", "BIMBEL POLRI", "TRYOUT POLRI", "JURUSAN POLRI",
                   "BUKU TES TNI POLRI", "TES POLISI", "REKRUTMEN POLISI"),
    "jadiprajurit": ("REKRUTMEN TNI", "SOAL TES TNI", "BIMBEL TNI", "TRYOUT TES TNI", "JURUSAN TNI",
                     "BUKU TES TNI POLRI"),
    "jadisekdin": ("SEKOLAH KEDINASAN", "SOAL SKD KEDINASAN", "BIMBEL SEKOLAH KEDINASAN",
                   "TRYOUT SKD KEDINASAN", "JURUSAN SEKDIN"),
    "jagotpa": ("TPA BAPPENAS", "SOAL TPA BAPPENAS", "BIMBEL TPA BAPPENAS", "TRYOUT TPA BAPPENAS",
                "UJIAN TPA BAPPENAS"),
    "jadippg": ("PPG", "SOAL PPG", "BIMBEL PPG", "TRYOUT PPG", "TIPS LOLOS PPG"),
    "jagoukp": ("UKP", "UJIAN KOMPETENSI PERPAJAKAN", "JADWAL UKP", "SOAL UKP", "LATIHAN SOAL UKP",
                "TRYOUT UKP", "BIMBEL UKP"),
}

_refresh_lock = threading.Lock()


class CoreBusy(Exception):
    """Pembaruan lain sedang berjalan."""


def keywords(slug: str) -> tuple[str, ...]:
    return CORE.get(slug, ())


def init() -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS core_trends (
                platform   TEXT NOT NULL,
                keyword    TEXT NOT NULL,
                geo        TEXT NOT NULL,
                status     TEXT NOT NULL,
                interest   INTEGER,
                ch_1d      TEXT,
                ch_7d      TEXT,
                ch_90d     TEXT,
                series     TEXT NOT NULL DEFAULT '[]',
                last_day   TEXT NOT NULL DEFAULT '',
                source     TEXT NOT NULL DEFAULT '',
                error      TEXT NOT NULL DEFAULT '',
                fetched_at REAL NOT NULL,
                PRIMARY KEY (platform, keyword, geo)
            )""")


# --------------------------------------------------------------------------- perhitungan


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def change(new: float, old: float) -> dict | None:
    """Perubahan baru terhadap lama dalam persen. Lama 0 dan baru > 0 = 'baru muncul' (breakout)."""
    if old == 0:
        return {"value": None, "label": "Baru muncul", "breakout": True} if new > 0 else None
    pct = round((new - old) / old * 100)
    return {"value": pct, "label": f"{pct:+d}%", "breakout": False}


def metrics(timeline: list[dict]) -> dict:
    """Garis waktu harian Trends -> minat terbaru dan perubahan kemarin / 7 hari / 90 hari.

    Titik terakhir yang masih 'partial' (hari belum selesai) dibuang supaya tidak terlihat anjlok.
    - kemarin : hari lengkap terakhir dibanding hari sebelumnya
    - 7 hari  : rata-rata 7 hari terakhir dibanding 7 hari sebelumnya
    - 90 hari : rata-rata 7 hari terakhir dibanding 7 hari pertama dalam jendela 90 hari
    """
    rows = timeline[:-1] if len(timeline) > 1 and timeline[-1].get("partial") else timeline
    series = [int((r["values"] or [0])[0]) for r in rows]
    if len(series) < MIN_POINTS or sum(series[-MIN_POINTS:]) == 0:
        return {"status": "sedikit", "series": series[-90:], "interest": None, "ch_1d": None, "ch_7d": None,
                "ch_90d": None, "last_day": rows[-1].get("label", "") if rows else ""}
    last7, prev7, first7 = series[-7:], series[-14:-7], series[:7]
    return {
        "status": "ok", "series": series[-90:], "interest": series[-1], "last_day": rows[-1].get("label", ""),
        "ch_1d": change(series[-1], series[-2]),
        "ch_7d": change(_mean(last7), _mean(prev7)),
        "ch_90d": change(_mean(last7), _mean(first7)),
    }


# --------------------------------------------------------------------------- simpan dan baca


def _dump(v) -> str | None:
    return None if v is None else json.dumps(v)


def _load(v):
    return None if v is None else json.loads(v)


def _existing(conn, slug: str, kw: str, geo: str):
    return conn.execute("SELECT * FROM core_trends WHERE platform = ? AND keyword = ? AND geo = ?",
                        (slug, kw, geo)).fetchone()


def _save_ok(conn, slug, kw, geo, m, source, now):
    conn.execute("""INSERT OR REPLACE INTO core_trends
        (platform, keyword, geo, status, interest, ch_1d, ch_7d, ch_90d, series, last_day, source, error, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)""",
                 (slug, kw, geo, m["status"], m["interest"], _dump(m["ch_1d"]), _dump(m["ch_7d"]), _dump(m["ch_90d"]),
                  json.dumps(m["series"]), m["last_day"], source, now))


def _save_fail(conn, slug, kw, geo, message, now):
    old = _existing(conn, slug, kw, geo)
    if old and old["status"] == "ok":  # data lama yang baik tidak ditimpa
        conn.execute("UPDATE core_trends SET error = ? WHERE platform = ? AND keyword = ? AND geo = ?",
                     (message[:300], slug, kw, geo))
    else:
        conn.execute("""INSERT OR REPLACE INTO core_trends
            (platform, keyword, geo, status, series, source, error, fetched_at) VALUES (?, ?, ?, 'gagal', '[]', '', ?, ?)""",
                     (slug, kw, geo, message[:300], now))


def refresh(slug: str, *, geo: str = GEO, demo: bool = False, provider=None, now: float | None = None) -> dict:
    """Ambil ulang semua keyword inti satu platform. Gagal pada satu keyword tidak menghentikan yang lain,
    kecuali Google menolak beberapa kali berturut-turut (berhenti agar tidak memperparah pembatasan)."""
    kws = keywords(slug)
    if not kws:
        raise KeyError(slug)
    if not _refresh_lock.acquire(blocking=False):
        raise CoreBusy("Pembaruan lain sedang berjalan. Coba lagi sebentar.")
    try:
        provider = provider or get_provider(demo=demo)
        now = time.time() if now is None else now
        done = failed = skipped = 0
        errors: list[str] = []
        streak = 0
        with closing(db.connect()) as conn:
            for i, kw in enumerate(kws):
                if streak >= MAX_CONSECUTIVE_FAILS:
                    skipped = len(kws) - i
                    break
                try:
                    res = provider.explore([kw], geo, 0, WINDOW, "", parts=("timeline",))
                    m = metrics(res.get("timeline") or [])
                    with conn:
                        _save_ok(conn, slug, kw, geo, m, res.get("source") or provider.name, now)
                    done += 1; streak = 0
                except ExploreError as exc:
                    failed += 1; streak += 1
                    errors.append(f"{kw}: {exc}")
                    with conn:
                        _save_fail(conn, slug, kw, geo, str(exc), now)
        return {"diperbarui": done, "gagal": failed, "dilewati": skipped, "errors": errors[:5]}
    finally:
        _refresh_lock.release()


def listing(slug: str, geo: str = GEO, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with closing(db.connect()) as conn:
        rows = {r["keyword"]: r for r in conn.execute(
            "SELECT * FROM core_trends WHERE platform = ? AND geo = ?", (slug, geo))}
    items, stamps, sources = [], [], set()
    for kw in keywords(slug):
        r = rows.get(kw)
        if r is None:
            items.append({"keyword": kw, "status": "belum"}); continue
        stamps.append(r["fetched_at"]); sources.add(r["source"])
        items.append({"keyword": kw, "status": r["status"], "interest": r["interest"], "ch_1d": _load(r["ch_1d"]),
                      "ch_7d": _load(r["ch_7d"]), "ch_90d": _load(r["ch_90d"]), "series": json.loads(r["series"]),
                      "last_day": r["last_day"], "source": r["source"], "error": r["error"], "fetched_at": r["fetched_at"]})
    updated = max(stamps) if stamps else None
    sources.discard("")
    return {"items": items, "meta": {
        "total": len(items), "ok": sum(1 for i in items if i["status"] == "ok"),
        "updated_at": updated, "stale": updated is None or now - updated > STALE_SECONDS,
        "sample": bool(sources) and sources <= {"mock"}, "sources": sorted(sources)}}
