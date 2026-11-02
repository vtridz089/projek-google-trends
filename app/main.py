"""API + penyaji dashboard.

Jalankan:  python -m uvicorn app.main:app --reload
Buka:      http://127.0.0.1:8000
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db
from .collector import DEMO_GEO, MockCollector, get_collector, target_geo
from .exporter import rows_to_csv
from .timeutil import today_local

log = logging.getLogger("trendwatch.api")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DEFAULT_GEO = os.environ.get("TRENDWATCH_GEO", "ID")
DEFAULT_SOURCE = os.environ.get("TRENDWATCH_SOURCE", "rss")  # 'rss' atau 'mock'

# Jeda minimal (detik) antar pengambilan dari Google per negara, agar tidak
# membanjiri sumber data (dan terkena pembatasan 429).
COLLECT_MIN_INTERVAL = float(os.environ.get("TRENDWATCH_COLLECT_MIN_INTERVAL", "300"))
_last_collect: dict[str, float] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    csv_path = os.environ.get("TRENDWATCH_CSV", "data/snapshots.csv")
    if os.path.exists(csv_path):
        try:
            imported, skipped = db.import_csv(csv_path)
            log.info("Riwayat dimuat dari %s: %d baris (%d dilewati)", csv_path, imported, skipped)
        except Exception:  # CSV rusak tidak boleh mencegah aplikasi menyala
            log.exception("Gagal memuat %s", csv_path)
    yield


app = FastAPI(title="TrendWatch", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Login sederhana (HTTP Basic), aktif hanya jika TRENDWATCH_PASSWORD diisi.
    Dipakai saat dashboard dipublikasikan. Kata sandi dikirim tanpa enkripsi
    kecuali lewat HTTPS, jadi pakai hosting yang menyediakan HTTPS."""
    password = os.environ.get("TRENDWATCH_PASSWORD")
    if not password or request.url.path == "/api/health":
        return await call_next(request)

    username = os.environ.get("TRENDWATCH_USER", "admin")
    header = request.headers.get("authorization", "")
    if header[:6].lower() == "basic ":
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            if secrets.compare_digest(user.encode(), username.encode()) and \
               secrets.compare_digest(pwd.encode(), password.encode()):
                return await call_next(request)
        except Exception:
            pass
    return Response("Autentikasi diperlukan", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="TrendWatch"'})


def _geo(value: str) -> str:
    value = value.strip().upper()
    if not (len(value) == 2 and value.isalpha()):
        raise HTTPException(400, "Kode negara harus 2 huruf, contoh: ID")
    return value


@app.get("/api/health")
def health():
    return {"status": "ok", "source": DEFAULT_SOURCE, "geo": DEFAULT_GEO}


@app.get("/api/overview")
def api_overview(geo: str = DEFAULT_GEO):
    return db.overview(_geo(geo))


@app.get("/api/history")
def api_history(keyword: str, geo: str = DEFAULT_GEO, days: int = Query(30, ge=1, le=365)):
    return {"keyword": keyword, "points": db.history(_geo(geo), keyword, days)}


@app.get("/api/top")
def api_top(geo: str = DEFAULT_GEO, days: int = Query(7, ge=1, le=90),
            limit: int = Query(10, ge=1, le=50)):
    return {"days": days, "items": db.top_keywords(_geo(geo), days, limit)}


@app.get("/api/export.csv")
def api_export(geo: str = DEFAULT_GEO, days: int = Query(30, ge=1, le=3650)):
    """Unduh riwayat sebagai CSV (UTF-8 dengan BOM agar rapi di Excel)."""
    geo = _geo(geo)
    text = rows_to_csv(db.export_rows(geo=geo, days=days), sanitize=True)
    return Response(
        content="﻿" + text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="trendwatch-{geo}-{today_local().isoformat()}.csv"'},
    )


@app.post("/api/collect")
def api_collect(geo: str = DEFAULT_GEO, source: str = DEFAULT_SOURCE):
    """Ambil data terbaru dari sumber, simpan ke database untuk hari ini.
    Sumber 'mock' selalu menulis ke geo demo (XX)."""
    try:
        collector = get_collector(source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    geo = target_geo(_geo(geo), source)

    if source == "rss":
        wait = COLLECT_MIN_INTERVAL - (time.monotonic() - _last_collect.get(geo, -1e9))
        if wait > 0:
            raise HTTPException(429, f"Terlalu sering mengambil data. Coba lagi dalam {int(wait) + 1} detik.")
        _last_collect[geo] = time.monotonic()

    day = today_local()
    try:
        items = collector.fetch(geo, day)
    except Exception as exc:  # jaringan terblokir, feed berubah, dsb.
        log.warning("Pengambilan %s dari '%s' gagal: %s", geo, source, exc)
        raise HTTPException(502, f"Gagal mengambil data dari sumber '{source}': {exc}") from exc
    saved = db.save_items(geo, day, items)
    return {"geo": geo, "source": source, "saved": saved}


@app.post("/api/demo-seed")
def api_demo_seed(days: int = Query(14, ge=1, le=60)):
    """Isi riwayat N hari ke belakang dengan data contoh di geo demo (XX),
    terpisah dari data nyata."""
    collector = MockCollector()
    today = today_local()
    total = 0
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        total += db.save_items(DEMO_GEO, day, collector.fetch(DEMO_GEO, day))
    return {"geo": DEMO_GEO, "days": days, "saved": total}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
