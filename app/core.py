"""Keyword inti per platform: topik-topik inti yang diriset otomatis untuk mencari keyword turunannya.

Setiap keyword inti diperlakukan sebagai seed riset: aplikasi menjalankan riset keyword yang sama dengan tab
Keyword research (saran pencarian Google + kenaikan Google Trends 24 jam / 7 hari / 90 hari) lalu menyimpan hasil
terakhirnya per platform. Halaman tinggal membaca hasil tersimpan itu, jadi cepat dan tidak memanggil Google setiap dibuka.

Pembaruan dijalankan lewat tombol di dashboard (di latar belakang, ada progres) atau
``python -m app.cli core-refresh`` (cocok dijadwalkan harian). Pembaruan yang gagal tidak menimpa hasil lama.
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import closing

from . import db, explore, keyword_research
from .explore import ExploreError

STALE_SECONDS = 24 * 3600
MAX_ITEMS = 80                # keyword yang disimpan per seed (yang sedang naik berada di atas)
MAX_CONSECUTIVE_FAILS = 2     # berhenti lebih awal bila Google/sumber gagal berturut-turut
GEO = "ID"
CHANNELS = ("youtube", "bing")  # saran YouTube dan Bing ikut dicari, selain Google
TREND_PER_SEED = 8              # keyword turunan teratas yang dicek langsung ke Google Trends (garis waktu harian)
TREND_WINDOW = "today 3-m"      # 90 hari, per hari
TREND_MIN_POINTS = 14
TREND_FAIL_STREAK = 3
KEEP = ("query", "rank", "words", "pertanyaan", "longtail", "maksud", "maksud_label", "sumber",
        "naik_24j", "naik_7h", "naik_90h", "skor", "format")

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
_job_lock = threading.Lock()
_job: dict = {}
_job_thread: threading.Thread | None = None


class CoreBusy(Exception):
    """Pembaruan lain sedang berjalan."""


def keywords(slug: str) -> tuple[str, ...]:
    return CORE.get(slug, ())


def init() -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("DROP TABLE IF EXISTS core_trends")  # rancangan awal (angka minat per keyword), tidak dipakai lagi
        conn.execute("""
            CREATE TABLE IF NOT EXISTS core_research (
                platform    TEXT NOT NULL,
                seed        TEXT NOT NULL,
                geo         TEXT NOT NULL,
                status      TEXT NOT NULL,
                items       TEXT NOT NULL DEFAULT '[]',
                all_queries TEXT NOT NULL DEFAULT '[]',
                summary     TEXT NOT NULL DEFAULT '{}',
                source      TEXT NOT NULL DEFAULT '',
                error       TEXT NOT NULL DEFAULT '',
                fetched_at  REAL NOT NULL,
                PRIMARY KEY (platform, seed, geo)
            )""")


# --------------------------------------------------------------------------- satu seed


def _change(new: float, old: float) -> dict | None:
    """Perubahan dalam persen. Lama 0 dan baru > 0 = 'baru muncul' (breakout)."""
    if old == 0:
        return {"label": "Baru muncul", "value": 0, "breakout": True} if new > 0 else None
    pct = round((new - old) / old * 100)
    return {"label": f"{pct:+d}%", "value": pct, "breakout": False} if pct > 0 else None  # hanya yang naik


def timeline_gains(timeline: list[dict]) -> dict | None:
    """Garis waktu harian Google Trends -> kenaikan 24 jam / 7 hari / 90 hari (hanya yang naik).

    Hari terakhir yang belum selesai dibuang. 24 jam = hari lengkap terakhir vs hari sebelumnya; 7 hari = rata-rata
    7 hari terakhir vs 7 hari sebelumnya; 90 hari = rata-rata 7 hari terakhir vs 7 hari pertama dalam 90 hari.
    ``None`` bila datanya terlalu sedikit untuk dipercaya."""
    rows = timeline[:-1] if len(timeline) > 1 and timeline[-1].get("partial") else timeline
    s = [int((r["values"] or [0])[0]) for r in rows]
    if len(s) < TREND_MIN_POINTS or sum(s[-TREND_MIN_POINTS:]) == 0:
        return None
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    return {"naik_24j": _change(s[-1], s[-2]), "naik_7h": _change(mean(s[-7:]), mean(s[-14:-7])),
            "naik_90h": _change(mean(s[-7:]), mean(s[:7]))}


def _rank_key(r: dict):
    g = keyword_research.best_gain(r)
    return (g is None, -(g if g is not None else 0), -(r.get("skor") or 0), r["query"])


def enrich_with_trends(result: dict, geo: str, demo: bool = False, provider=None, limit: int = TREND_PER_SEED) -> dict:
    """Cek keyword turunan teratas yang belum punya data kenaikan langsung ke Google Trends (satu permintaan per keyword),
    lalu urutkan ulang agar yang naik di atas. Pencarian terkait Google untuk topik kecil sering kosong; garis waktu
    per keyword memberi angka nyata. Kegagalan dicatat di ``errors`` tanpa menggagalkan riset."""
    items = result.get("items") or []
    todo = [r for r in items if keyword_research.best_gain(r) is None][:limit]
    if not todo:
        return result
    provider = provider or explore.get_provider(demo)
    streak = got = 0
    for r in todo:
        try:
            res = provider.explore([r["query"]], geo, 0, TREND_WINDOW, "", parts=("timeline",))
            streak = 0
        except ExploreError as exc:
            streak += 1
            if streak >= TREND_FAIL_STREAK:
                result.setdefault("errors", []).append({"scope": "Google Trends (kenaikan per keyword)", "error": str(exc)})
                break
            continue
        g = timeline_gains(res.get("timeline") or [])
        if g and any(g.values()):
            r.update({k: v for k, v in g.items() if v})
            r["sumber"] = sorted(set(r.get("sumber") or []) | {"naik"}, key=list(keyword_research.SOURCE_LABELS).index)
            got += 1
    result["trends_cek"] = {"dicek": len(todo), "naik": got}
    items.sort(key=_rank_key)
    return result


def trends_related(seed: str, geo: str, demo: bool = False) -> dict:
    """Pencarian terkait Google Trends per kurun waktu persis seperti di trends.google.com (Top dan Naik),
    diambil dari cache hasil riset yang baru saja dijalankan (tanpa permintaan baru ke Google bila masih segar).
    Kurun yang kosong dicatat sebagai daftar kosong (Google memang tidak memberi data untuk topik kecil)."""
    from . import explore_service
    out: dict[str, list[dict]] = {}
    for time_range, column in keyword_research.WINDOWS:
        key = column.replace("naik_", "")
        try:
            res = explore_service.run_booming(explore_service.normalize_booming(seed, geo, 0, time_range, "", 20), demo=demo)
            out[key] = [{"query": i["query"], "kind": i["kind"], "label": i["label"], "value": i["value"],
                         "breakout": bool(i.get("breakout"))} for i in res["items"]]
        except (ExploreError, ValueError):
            out[key] = []
    return out


def _default_runner(seed: str, geo: str, demo: bool = False) -> dict:
    result = keyword_research.run_keywords(seed, geo, "cepat", demo=demo, refresh=True, channels=CHANNELS)
    result["trends_terkait"] = trends_related(seed, geo, demo)
    return enrich_with_trends(result, geo, demo=demo)


def _has_gain(r: dict) -> bool:
    return bool(r.get("naik_24j") or r.get("naik_7h") or r.get("naik_90h"))


def _existing(conn, slug: str, seed: str, geo: str):
    return conn.execute("SELECT * FROM core_research WHERE platform = ? AND seed = ? AND geo = ?",
                        (slug, seed, geo)).fetchone()


def _save_ok(conn, slug: str, seed: str, geo: str, result: dict, now: float) -> None:
    items = result.get("items") or []
    old = _existing(conn, slug, seed, geo)
    prev = set(json.loads(old["all_queries"])) if old and old["status"] == "ok" else None
    kept = []
    for r in items[:MAX_ITEMS]:
        row = {k: r.get(k) for k in KEEP}
        row["baru"] = prev is not None and r["query"].lower() not in prev   # tidak ada di pembaruan sebelumnya
        kept.append(row)
    sumber = result.get("sumber") or {}
    source = "mock" if "mock" in (sumber.get("saran"), sumber.get("trends")) else "google"
    errors = result.get("errors") or []
    summary = {"total": len(items), "naik": sum(1 for r in items if _has_gain(r)),
               "naik_24j": sum(1 for r in items if r.get("naik_24j")),
               "baru": sum(1 for r in kept if r["baru"]), "ditampilkan": len(kept),
               "youtube": sum(1 for r in items if "youtube" in (r.get("sumber") or [])),
               "bing": sum(1 for r in items if "bing" in (r.get("sumber") or [])),
               "trends_cek": (result.get("trends_cek") or {}).get("dicek", 0),
               "trends_naik": (result.get("trends_cek") or {}).get("naik", 0),
               "terkait": result.get("trends_terkait") or {}}
    conn.execute("""INSERT OR REPLACE INTO core_research
        (platform, seed, geo, status, items, all_queries, summary, source, error, fetched_at)
        VALUES (?, ?, ?, 'ok', ?, ?, ?, ?, ?, ?)""",
                 (slug, seed, geo, json.dumps(kept, ensure_ascii=False),
                  json.dumps([r["query"].lower() for r in items], ensure_ascii=False), json.dumps(summary), source,
                  (errors[0]["error"] if errors else "")[:300], now))


def _save_fail(conn, slug: str, seed: str, geo: str, message: str, now: float) -> None:
    old = _existing(conn, slug, seed, geo)
    if old and old["status"] == "ok":  # hasil lama yang baik tidak ditimpa
        conn.execute("UPDATE core_research SET error = ? WHERE platform = ? AND seed = ? AND geo = ?",
                     (message[:300], slug, seed, geo))
    else:
        conn.execute("""INSERT OR REPLACE INTO core_research (platform, seed, geo, status, error, fetched_at)
                        VALUES (?, ?, ?, 'gagal', ?, ?)""", (slug, seed, geo, message[:300], now))


def refresh(slug: str, *, geo: str = GEO, demo: bool = False, runner=None, now: float | None = None,
            progress=None) -> dict:
    """Riset ulang semua keyword inti satu platform, berurutan. Gagal pada satu seed tidak menghentikan yang lain,
    kecuali gagal berturut-turut (berhenti agar tidak memperparah pembatasan)."""
    seeds = keywords(slug)
    if not seeds:
        raise KeyError(slug)
    if not _refresh_lock.acquire(blocking=False):
        raise CoreBusy("Pembaruan lain sedang berjalan. Coba lagi sebentar.")
    try:
        runner = runner or _default_runner
        now = time.time() if now is None else now
        done = failed = skipped = 0
        errors: list[str] = []
        streak = 0
        with closing(db.connect()) as conn:
            for i, seed in enumerate(seeds):
                if streak >= MAX_CONSECUTIVE_FAILS:
                    skipped = len(seeds) - i
                    break
                if progress:
                    progress(i, len(seeds), seed)
                try:
                    result = runner(seed, geo, demo=demo)
                    with conn:
                        _save_ok(conn, slug, seed, geo, result, now)
                    done += 1; streak = 0
                except ExploreError as exc:
                    failed += 1; streak += 1
                    errors.append(f"{seed}: {exc}")
                    with conn:
                        _save_fail(conn, slug, seed, geo, str(exc), now)
        if progress:
            progress(len(seeds), len(seeds), "")
        return {"diperbarui": done, "gagal": failed, "dilewati": skipped, "errors": errors[:5]}
    finally:
        _refresh_lock.release()


# --------------------------------------------------------------------------- latar belakang + progres


def start_refresh(slug: str, *, geo: str = GEO, demo: bool = False, runner=None) -> dict:
    """Mulai pembaruan di latar belakang (±10-20 detik per keyword inti dari Google). Cek ``job_state`` untuk progres."""
    global _job_thread
    if not keywords(slug):
        raise KeyError(slug)
    with _job_lock:
        if _refresh_lock.locked() or _job.get("running"):
            raise CoreBusy("Pembaruan lain sedang berjalan. Coba lagi sebentar.")
        _job.clear()
        _job.update({"platform": slug, "running": True, "done": 0, "total": len(keywords(slug)), "current": "",
                     "result": None, "error": "", "started": time.time()})

    def progress(i, total, seed):
        _job.update({"done": i, "total": total, "current": seed})

    def work():
        try:
            _job["result"] = refresh(slug, geo=geo, demo=demo, runner=runner, progress=progress)
        except Exception as exc:  # noqa: BLE001 - apa pun yang gagal harus terlihat di antarmuka
            _job["error"] = str(exc) or exc.__class__.__name__
        finally:
            _job["running"] = False

    _job_thread = threading.Thread(target=work, name="core-refresh", daemon=True)
    _job_thread.start()
    return dict(_job)


def job_state(slug: str) -> dict | None:
    return dict(_job) if _job.get("platform") == slug else None


# --------------------------------------------------------------------------- baca


def listing(slug: str, geo: str = GEO, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with closing(db.connect()) as conn:
        rows = {r["seed"]: r for r in conn.execute(
            "SELECT * FROM core_research WHERE platform = ? AND geo = ?", (slug, geo))}
    items, stamps, sources = [], [], set()
    for seed in keywords(slug):
        r = rows.get(seed)
        if r is None:
            items.append({"seed": seed, "status": "belum", "items": [], "summary": None}); continue
        if r["status"] == "ok":
            stamps.append(r["fetched_at"]); sources.add(r["source"])
        items.append({"seed": seed, "status": r["status"], "items": json.loads(r["items"]),
                      "summary": json.loads(r["summary"]) if r["status"] == "ok" else None,
                      "source": r["source"], "error": r["error"], "fetched_at": r["fetched_at"]})
    updated = max(stamps) if stamps else None
    return {"items": items, "meta": {
        "total": len(items), "ok": sum(1 for i in items if i["status"] == "ok"),
        "naik": sum((i["summary"] or {}).get("naik", 0) for i in items),
        "updated_at": updated, "stale": updated is None or now - updated > STALE_SECONDS,
        "sample": bool(sources) and sources <= {"mock"}, "job": job_state(slug), **_schedule_meta()}}


def _schedule_meta() -> dict:
    from . import scheduler  # impor lambat: scheduler memakai core
    return {"jadwal": scheduler.label(), "jadwal_terakhir": scheduler.get_last() or None}


# --------------------------------------------------------------------------- berkas hasil (supaya langsung terisi saat dibuka)

SNAPSHOT_COLS = ("platform", "seed", "geo", "status", "items", "all_queries", "summary", "source", "error", "fetched_at")


def snapshot_path() -> str:
    return os.environ.get("TRENDWATCH_CORE_JSON", "data/core_research.json")


def export_json(path: str | None = None) -> int:
    """Tulis hasil riset yang baik (status ok, data asli) ke berkas JSON agar ikut tersimpan di repo."""
    path = path or snapshot_path()
    with closing(db.connect()) as conn:
        rows = conn.execute("SELECT * FROM core_research WHERE status = 'ok' AND source != 'mock' "
                            "ORDER BY platform, seed, geo").fetchall()
    data = [{c: (json.loads(r[c]) if c in ("items", "all_queries", "summary") else r[c]) for c in SNAPSHOT_COLS} for r in rows]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    return len(data)


def import_json(path: str | None = None) -> int:
    """Muat berkas hasil riset. Hanya mengisi yang belum ada atau lebih lama; hasil yang lebih baru tidak ditimpa."""
    path = path or snapshot_path()
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list)
    except (OSError, ValueError, AssertionError):
        return 0
    loaded = 0
    with closing(db.connect()) as conn, conn:
        for d in data:
            try:
                slug, seed, geo = d["platform"], d["seed"], d["geo"]
                if seed not in CORE.get(slug, ()) or d["status"] != "ok":
                    continue
                old = _existing(conn, slug, seed, geo)
                if old and old["status"] == "ok" and old["fetched_at"] >= float(d["fetched_at"]):
                    continue
                conn.execute("""INSERT OR REPLACE INTO core_research
                    (platform, seed, geo, status, items, all_queries, summary, source, error, fetched_at)
                    VALUES (?, ?, ?, 'ok', ?, ?, ?, ?, ?, ?)""",
                             (slug, seed, geo, json.dumps(d["items"], ensure_ascii=False),
                              json.dumps(d["all_queries"], ensure_ascii=False), json.dumps(d["summary"]),
                              str(d.get("source", "google")), str(d.get("error", ""))[:300], float(d["fetched_at"])))
                loaded += 1
            except (KeyError, TypeError, ValueError):
                continue
    return loaded


def missing(geo: str = GEO) -> int:
    """Jumlah keyword inti yang belum punya hasil riset (status ok)."""
    with closing(db.connect()) as conn:
        have = {(r["platform"], r["seed"]) for r in conn.execute(
            "SELECT platform, seed FROM core_research WHERE status = 'ok' AND geo = ?", (geo,))}
    return sum(1 for slug, seeds in CORE.items() for s in seeds if (slug, s) not in have)
