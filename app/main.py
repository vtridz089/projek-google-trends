"""API + penyaji dashboard.

Jalankan:  uvicorn app.main:app --reload
Buka:      http://127.0.0.1:8000
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .collector import MockCollector, get_collector

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DEFAULT_GEO = os.environ.get("TRENDWATCH_GEO", "ID")
DEFAULT_SOURCE = os.environ.get("TRENDWATCH_SOURCE", "rss")  # 'rss' atau 'mock'


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="TrendWatch", lifespan=lifespan)


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


@app.post("/api/collect")
def api_collect(geo: str = DEFAULT_GEO, source: str = DEFAULT_SOURCE):
    """Ambil data terbaru dari sumber, simpan ke database untuk hari ini."""
    geo = _geo(geo)
    try:
        collector = get_collector(source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        items = collector.fetch(geo, date.today())
    except Exception as exc:  # jaringan terblokir, feed berubah, dsb.
        raise HTTPException(502, f"Gagal mengambil data dari sumber '{source}': {exc}") from exc
    saved = db.save_items(geo, date.today(), items)
    return {"geo": geo, "source": source, "saved": saved}


@app.post("/api/demo-seed")
def api_demo_seed(geo: str = DEFAULT_GEO, days: int = Query(14, ge=1, le=60)):
    """Isi riwayat N hari ke belakang dengan data contoh (untuk demo/pengembangan)."""
    geo = _geo(geo)
    collector = MockCollector()
    today = date.today()
    total = 0
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        total += db.save_items(geo, day, collector.fetch(geo, day))
    return {"geo": geo, "days": days, "saved": total}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
