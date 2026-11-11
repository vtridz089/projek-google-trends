"""Jadwal harian: riset ulang semua keyword inti (15 platform) sekali sehari, otomatis selama aplikasi menyala.

- ``TRENDWATCH_CORE_SCHEDULE``: jam jalan ``HH:MM`` (bawaan ``06:00``); ``off`` untuk mematikan.
- ``TRENDWATCH_TZ``: zona waktu jadwal (bawaan ``Asia/Jakarta``).
Bila aplikasi baru menyala sesudah jam itu dan hari ini belum dijalankan, riset langsung dijalankan (mengejar).
Tanggal terakhir yang selesai disimpan di database, jadi restart tidak mengulang riset di hari yang sama.
Hanya berjalan bila aplikasinya hidup: pada hosting yang tidur, jadwal ini tidak jalan.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import closing
from datetime import date, datetime, time as dtime, timedelta, timezone

from . import core, db, platforms

log = logging.getLogger("cerekey.scheduler")
SETTING = "core_last_run_date"
CHECK_EVERY = 300  # detik


def schedule_time() -> dtime | None:
    raw = os.environ.get("TRENDWATCH_CORE_SCHEDULE", "06:00").strip().lower()
    if raw in ("", "off", "0", "false", "no"):
        return None
    try:
        hh, mm = raw.split(":")
        return dtime(int(hh), int(mm))
    except ValueError:
        log.warning("TRENDWATCH_CORE_SCHEDULE '%s' tidak valid (pakai HH:MM); jadwal dimatikan.", raw)
        return None


def tzinfo():
    name = os.environ.get("TRENDWATCH_TZ", "Asia/Jakarta")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - tzdata tidak ada di sebagian image
        return timezone(timedelta(hours=7)) if name == "Asia/Jakarta" else timezone.utc


def label() -> str | None:
    t = schedule_time()
    return None if t is None else f"{t:%H:%M} " + ("WIB" if os.environ.get("TRENDWATCH_TZ", "Asia/Jakarta") == "Asia/Jakarta" else os.environ.get("TRENDWATCH_TZ", "Asia/Jakarta"))


_DDL = "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"


def get_last() -> str:
    with closing(db.connect()) as conn, conn:
        conn.execute(_DDL)
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING,)).fetchone()
    return row[0] if row else ""


def set_last(day: str) -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute(_DDL)
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (SETTING, day))


def due(now: datetime, at: dtime | None, last: str) -> bool:
    """now: waktu lokal (berzona). Jalan bila sudah lewat jam jadwal dan hari ini belum selesai dijalankan."""
    if at is None:
        return False
    return now.time() >= at and last != now.date().isoformat()


def run_all(runner=None, demo: bool = False) -> dict:
    """Riset ulang semua platform berurutan. Gagal di satu platform tidak menghentikan platform lain."""
    total = {"diperbarui": 0, "gagal": 0, "dilewati": 0, "platform": 0}
    for p in platforms.PLATFORMS:
        try:
            r = core.refresh(p.slug, runner=runner, demo=demo)
        except core.CoreBusy:
            log.info("Pembaruan manual sedang berjalan; riset harian dicoba lagi pada pemeriksaan berikutnya.")
            total["sibuk"] = True
            return total
        except Exception:  # noqa: BLE001
            log.exception("Riset keyword inti %s gagal", p.slug)
            total["gagal"] += len(core.keywords(p.slug))
            continue
        total["platform"] += 1
        for k in ("diperbarui", "gagal", "dilewati"):
            total[k] += r[k]
        log.info("Keyword inti %s: %d diperbarui, %d gagal, %d dilewati", p.slug, r["diperbarui"], r["gagal"], r["dilewati"])
    return total


def tick(now: datetime | None = None, runner=None) -> bool:
    """Satu putaran pemeriksaan jadwal. True bila riset harian dijalankan."""
    now = now or datetime.now(tzinfo())
    if not due(now, schedule_time(), get_last()):
        return False
    log.info("Menjalankan riset harian keyword inti (%s)", now.date().isoformat())
    result = run_all(runner=runner)
    if result.get("sibuk"):
        return False
    set_last(now.date().isoformat())
    log.info("Riset harian selesai: %s", result)
    return True


def start(stop: threading.Event) -> threading.Thread | None:
    if schedule_time() is None:
        log.info("Jadwal harian keyword inti dimatikan (TRENDWATCH_CORE_SCHEDULE=off).")
        return None

    def loop():
        while not stop.is_set():
            try:
                tick()
            except Exception:  # noqa: BLE001 - jadwal tidak boleh mati karena satu galat
                log.exception("Pemeriksaan jadwal gagal")
            stop.wait(CHECK_EVERY)

    t = threading.Thread(target=loop, name="core-schedule", daemon=True)
    t.start()
    log.info("Jadwal harian keyword inti aktif: %s", label())
    return t
