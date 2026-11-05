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

from . import db, explore_service, keyword_research
from .categories import CATEGORIES, GPROP_LABELS, TIMEFRAMES
from .collector import DEMO_GEO, MockCollector, get_collector, target_geo
from .exporter import rows_to_csv
from .explore import ExploreError
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
    db.discovery_init()
    csv_path = os.environ.get("TRENDWATCH_CSV", "data/snapshots.csv")
    if os.path.exists(csv_path):
        try:
            imported, skipped = db.import_csv(csv_path)
            log.info("Riwayat dimuat dari %s: %d baris (%d dilewati)", csv_path, imported, skipped)
        except Exception:  # CSV rusak tidak boleh mencegah aplikasi menyala
            log.exception("Gagal memuat %s", csv_path)
    try:
        loaded = db.discovery_import_csv(os.environ.get("TRENDWATCH_WATCHLIST_CSV", "data/watchlist.csv"),
                                         os.environ.get("TRENDWATCH_DISCOVERY_CSV", "data/discovery.csv"))
        if loaded["watchlist"] or loaded["discovery"]:
            log.info("Discovery dimuat dari CSV: %s", loaded)
    except Exception:  # CSV rusak tidak boleh mencegah aplikasi menyala
        log.exception("Gagal memuat CSV discovery")
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


@app.get("/api/categories")
def api_categories():
    """Pilihan untuk formulir Jelajah: kategori, rentang waktu, jenis pencarian."""
    return {
        "categories": [{"id": cid, "name": name} for cid, name in CATEGORIES],
        "timeframes": [{"value": v, "name": n} for v, n in TIMEFRAMES],
        "gprops": [{"value": v, "name": n} for v, n in GPROP_LABELS.items()],
        "limit": explore_service.KEYWORD_LIMIT,
    }


@app.get("/api/explore")
def api_explore(
    q: list[str] = Query(default=[]),
    geo: str = DEFAULT_GEO,
    cat: int = 0,
    period: str = Query("today 12-m", alias="time"),
    gprop: str = "",
    parts: str = "timeline,related,regions",
    demo: bool = False,
    refresh: bool = False,
):
    """Minat pencarian dari waktu ke waktu untuk keyword/kategori pilihan sendiri.

    geo kosong = seluruh dunia. demo=true memakai data contoh (bukan dari Google)."""
    try:
        params = explore_service.normalize_params(q, geo, cat, period, gprop, parts)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        return explore_service.run(params, demo=demo, refresh=refresh)
    except ExploreError as exc:
        log.warning("Jelajah gagal: %s", exc)
        detail = str(exc) + (f" Saran: {exc.hint}" if exc.hint else "")
        raise HTTPException(502, detail) from exc


@app.get("/api/explore/recent")
def api_explore_recent(limit: int = Query(8, ge=1, le=20)):
    return {"items": db.recent_searches(limit)}


@app.get("/api/booming")
def api_booming(
    topic: str = "",
    geo: str = DEFAULT_GEO,
    cat: int = 0,
    period: str = Query("now 7-d", alias="time"),
    gprop: str = "",
    limit: int = explore_service.BOOMING_DEFAULT_LIMIT,
    rising_only: bool = True,
    sub: bool = False,
    demo: bool = False,
    refresh: bool = False,
):
    """Pencarian yang sedang naik untuk satu topik (mis. 'bumn'), lengkap dengan persen kenaikan dan skor peluang.

    Diambil dari 'pencarian terkait yang sedang naik' Google Trends. Secara bawaan HANYA yang tercatat naik
    (jumlahnya bisa kurang dari ``limit``, bahkan nol); rising_only=false mengisi sisanya dengan yang populer.
    sub=true menandai pencarian turunan dari panel keyword (tidak masuk "Pencarian terakhir").
    demo=true memakai data contoh (bukan dari Google)."""
    try:
        params = explore_service.normalize_booming(topic, geo, cat, period, gprop, limit,
                                                   rising_only=rising_only, sub=sub)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        return explore_service.run_booming(params, demo=demo, refresh=refresh)
    except ExploreError as exc:
        log.warning("Booming gagal: %s", exc)
        detail = str(exc) + (f" Saran: {exc.hint}" if exc.hint else "")
        raise HTTPException(502, detail) from exc


@app.get("/api/keywords")
def api_keywords(
    seed: str = "",
    geo: str = DEFAULT_GEO,
    depth: str = "cepat",
    trends: bool = True,
    demo: bool = False,
    refresh: bool = False,
):
    """Riset keyword: satu seed menjadi puluhan sampai ratusan ide (saran Google + Google Trends) lengkap dengan
    maksud pencarian, tahap funnel, tren, skor, dan kelompok topik. Tanpa volume pencarian/KD (tidak tersedia gratis).

    depth=cepat memakai ±17 permintaan saran Google, depth=lengkap ±43. trends=false melewatkan Google Trends."""
    try:
        keyword_research.normalize(seed, geo, depth)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        return keyword_research.run_keywords(seed, geo, depth, with_trends=trends, demo=demo, refresh=refresh)
    except ExploreError as exc:
        log.warning("Riset keyword gagal: %s", exc)
        detail = str(exc) + (f" Saran: {exc.hint}" if exc.hint else "")
        raise HTTPException(502, detail) from exc


@app.get("/api/research")
def api_research(
    topic: str = "",
    geo: str = DEFAULT_GEO,
    cat: int = 0,
    expand: int = Query(3, ge=0, le=explore_service.RESEARCH_MAX_EXPAND),
    demo: bool = False,
    refresh: bool = False,
):
    """Riset topik: gabungan 4 rentang waktu (24 jam sampai 90 hari) dan turunan keyword teratas.

    Otomatis pekerjaan manual di Google Trends. Satu riset memakai sekitar 4 + ``expand`` permintaan
    ke sumber data (hasil di-cache, jadi pengulangan cepat)."""
    try:
        explore_service.normalize_booming(topic, geo, cat, "now 7-d", "", 10)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        result = explore_service.run_research(topic, geo, cat, expand=expand, demo=demo, refresh=refresh)
    except ExploreError as exc:
        log.warning("Riset gagal: %s", exc)
        detail = str(exc) + (f" Saran: {exc.hint}" if exc.hint else "")
        raise HTTPException(502, detail) from exc
    if result.get("source") == "mock" or not result.get("geo"):
        return explore_service.finalize_research(result, {}, 0)
    counts, days = db.discovery_history_counts(result["topic"], result["geo"], result["category"],
                                               today_local().isoformat())
    return explore_service.finalize_research(result, counts, days)


@app.post("/api/research/save")
async def api_research_save(request: Request):
    """Simpan keyword hasil riset ke Daily Discovery (dan tambahkan topiknya ke Watchlist)."""
    body = await request.json()
    topic = str(body.get("topic", "")).strip()
    if not topic or len(topic) > 100:
        raise HTTPException(400, "Topik wajib diisi (maksimal 100 karakter).")
    if body.get("source") == "mock":
        raise HTTPException(400, "Data contoh tidak disimpan ke Daily Discovery.")
    if not str(body.get("geo", "")).strip():
        raise HTTPException(400, "Pilih satu negara (bukan seluruh dunia) untuk menyimpan ke Daily Discovery.")
    geo = _geo(str(body.get("geo")))
    try:
        category = int(body.get("category", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "Kategori tidak valid.") from None
    items = body.get("items")
    if not isinstance(items, list) or not 0 < len(items) <= 200:
        raise HTTPException(400, "Kirim 1 sampai 200 keyword untuk disimpan.")
    rows = []
    for it in items:
        if not isinstance(it, dict):
            raise HTTPException(400, "Format keyword tidak valid.")
        keyword = str(it.get("keyword", "")).strip()
        if not keyword or len(keyword) > 200:
            continue
        try:
            score = int(it.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        rising = bool(it.get("rising"))
        try:
            opp = max(0.0, min(100.0, float(it.get("opportunity"))))
        except (TypeError, ValueError):
            opp = None
        rows.append({"keyword": keyword, "source": "research",
                     "trend_category": "trending" if it.get("breakout") else ("emerging" if rising else "new_candidate"),
                     "interest_score": score if rising else None,
                     "trend_change": str(it.get("label") or "")[:40], "opportunity_score": opp})
    if not rows:
        raise HTTPException(400, "Tidak ada keyword yang valid untuk disimpan.")
    watch = db.watchlist_add(topic, geo, category)
    saved = db.discovery_save(watch["id"], today_local().isoformat(), rows)
    return {"topic_id": watch["id"], "topic": watch["topic"], "saved": saved}


@app.get("/api/booming/recent")
def api_booming_recent(limit: int = Query(8, ge=1, le=20)):
    return {"items": db.recent_searches(limit, kind="booming")}



# ------------------------------------------------------------------
# Daily Keyword Discovery
# ------------------------------------------------------------------

@app.get("/api/watchlist")
def api_watchlist():
    return {"items": db.watchlist_list()}


@app.post("/api/watchlist")
async def api_watchlist_add(request: Request):
    body = await request.json()
    topic = str(body.get("topic", "")).strip()
    if not topic or len(topic) > 100:
        raise HTTPException(400, "Topik wajib diisi (maksimal 100 karakter).")
    geo = _geo(str(body.get("geo", DEFAULT_GEO)))
    try:
        category = int(body.get("category", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "Kategori tidak valid.") from None
    return db.watchlist_add(topic, geo, category)


@app.patch("/api/watchlist/{topic_id}")
async def api_watchlist_toggle(topic_id: int, request: Request):
    body = await request.json()
    if not db.watchlist_set_active(topic_id, bool(body.get("active", True))):
        raise HTTPException(404, "Topik tidak ditemukan.")
    return {"ok": True}


@app.delete("/api/watchlist/{topic_id}")
def api_watchlist_delete(topic_id: int):
    if not db.watchlist_delete(topic_id):
        raise HTTPException(404, "Topik tidak ditemukan.")
    return {"ok": True}


@app.get("/api/discovery")
def api_discovery(limit: int = Query(200, ge=1, le=500)):
    return {"items": db.discovery_list(limit)}


@app.patch("/api/discovery/{candidate_id}")
async def api_discovery_status(candidate_id: int, request: Request):
    body = await request.json()
    status = str(body.get("status", "New"))
    allowed = {"New", "Saved", "Writing", "Published", "Rejected"}
    if status not in allowed:
        raise HTTPException(400, "Status tidak valid.")
    if not db.discovery_set_status(candidate_id, status):
        raise HTTPException(404, "Keyword tidak ditemukan.")
    return {"ok": True}


@app.post("/api/discovery/run")
def api_discovery_run(refresh: bool = False):
    """Run discovery now for active watchlist topics; partial topic failures are retained."""
    from datetime import date
    topics = [x for x in db.watchlist_list() if x["is_active"]]
    if not topics:
        raise HTTPException(400, "Tambahkan minimal satu topik ke Watchlist terlebih dahulu.")
    report = {"topics_checked": 0, "saved": 0, "errors": []}
    day = today_local().isoformat()
    for item in topics:
        report["topics_checked"] += 1
        try:
            params = explore_service.normalize_booming(
                item["topic"], item["geo"], item["category"], "now 7-d", "", 20
            )
            result = explore_service.run_booming(params, refresh=refresh)
            rows = []
            for candidate in result.get("items", []):
                rising = candidate.get("kind") == "naik"
                breakout = bool(candidate.get("breakout"))
                rows.append({
                    "keyword": candidate["query"],
                    "source": "related_queries",
                    "trend_category": "trending" if breakout else ("emerging" if rising else "new_candidate"),
                    "interest_score": candidate.get("value"),
                    "trend_change": candidate.get("label"),
                    "opportunity_score": None,
                })
            report["saved"] += db.discovery_save(item["id"], day, rows)
        except Exception as exc:
            log.warning("Discovery topik '%s' gagal: %s", item["topic"], exc)
            report["errors"].append({"topic": item["topic"], "error": str(exc)})
    return report


@app.get("/riset")
def research_page():
    return FileResponse(STATIC_DIR / "research.html")


@app.get("/discovery")
def discovery_page():
    return FileResponse(STATIC_DIR / "discovery.html")


@app.get("/explore")
def explore_page():
    return FileResponse(STATIC_DIR / "explore.html")


@app.get("/hari-ini")
def today_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "keyword.html")


@app.get("/booming")
def booming_page():
    return FileResponse(STATIC_DIR / "booming.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
