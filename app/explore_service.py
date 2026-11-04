"""Logika halaman Booming dan Jelajah di luar HTTP: validasi masukan, cache, dan pemilihan sumber.

Dipisah dari main.py agar bisa diuji tanpa FastAPI.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date

from . import db
from .categories import CATEGORY_NAMES
from .explore import GPROPS, PARTS, ExploreError, get_provider

KEYWORD_LIMIT = 5
KEYWORD_MAX_LEN = 100
BOOMING_DEFAULT_LIMIT = 10
BREAKOUT_VALUE = 5000  # Google menandai kenaikan > 5.000% sebagai "Breakout"

_TIME_RE = re.compile(r"^(now \d{1,2}-[Hd]|today \d{1,2}-[mMy]|all|\d{4}-\d{2}-\d{2} \d{4}-\d{2}-\d{2})$")
_GEO_RE = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")


def normalize_params(keywords, geo: str = "", category=0, time_range: str = "today 12-m",
                     gprop: str = "", parts=None) -> dict:
    """Validasi dan rapikan masukan. Melempar ValueError berpesan Indonesia bila tidak valid."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in keywords or []:
        kw = re.sub(r"\s+", " ", str(raw).replace(",", " ")).strip()
        if not kw:
            continue
        if len(kw) > KEYWORD_MAX_LEN:
            raise ValueError(f"Keyword terlalu panjang (maksimal {KEYWORD_MAX_LEN} karakter).")
        if any(ord(c) < 32 for c in kw):
            raise ValueError("Keyword mengandung karakter yang tidak diperbolehkan.")
        if kw.lower() not in seen:
            seen.add(kw.lower())
            cleaned.append(kw)
    if not cleaned:
        raise ValueError("Masukkan minimal satu keyword.")
    if len(cleaned) > KEYWORD_LIMIT:
        raise ValueError(f"Maksimal {KEYWORD_LIMIT} keyword sekali cari (Google Trends membatasi perbandingan).")

    geo = (geo or "").strip().upper()
    if geo and not _GEO_RE.match(geo):
        raise ValueError("Kode wilayah tidak valid. Contoh: ID, US, atau ID-JK; kosongkan untuk seluruh dunia.")

    try:
        category = int(category)
    except (TypeError, ValueError):
        raise ValueError("Kategori harus berupa angka.") from None
    if not 0 <= category <= 9999:
        raise ValueError("ID kategori tidak valid.")

    time_range = (time_range or "").strip()
    if not _TIME_RE.match(time_range):
        raise ValueError("Rentang waktu tidak valid. Contoh: 'today 12-m', 'today 5-y', 'all', atau '2025-01-01 2025-06-30'.")
    if re.match(r"\d{4}-", time_range):
        start, end = time_range.split()
        try:
            start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            raise ValueError("Tanggal tidak valid.") from None
        if start_d > end_d:
            raise ValueError("Tanggal awal tidak boleh setelah tanggal akhir.")

    gprop = (gprop or "").strip().lower()
    if gprop not in GPROPS:
        raise ValueError("Jenis pencarian tidak valid. Pilihan: web, images, news, youtube, froogle.")

    if parts is None:
        parts = PARTS
    elif isinstance(parts, str):
        parts = [p.strip() for p in parts.split(",") if p.strip()]
    wanted = {p for p in parts if p in PARTS} | {"timeline"}  # grafik selalu ada
    ordered = [p for p in PARTS if p in wanted]

    return {"keywords": cleaned, "geo": geo, "category": category, "time": time_range,
            "gprop": gprop, "parts": ordered}


def normalize_booming(topic, geo: str = "ID", category=0, time_range: str = "now 7-d",
                      gprop: str = "", limit=BOOMING_DEFAULT_LIMIT) -> dict:
    """Validasi masukan halaman Booming: satu topik, bukan daftar keyword."""
    topic = str(topic or "").strip()
    if "," in topic or "\n" in topic:
        raise ValueError("Isi satu topik per pencarian (tanpa koma). "
                         "Untuk membandingkan beberapa keyword, pakai halaman Jelajah.")
    base = normalize_params([topic], geo, category, time_range, gprop, None)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError("Jumlah hasil harus berupa angka.") from None
    if not 1 <= limit <= 20:
        raise ValueError("Jumlah hasil harus antara 1 dan 20.")
    return {"kind": "booming", "topic": base["keywords"][0], "keywords": base["keywords"],
            "geo": base["geo"], "category": base["category"], "time": base["time"],
            "gprop": base["gprop"], "parts": ["related"], "limit": limit}


# ------------------------------------------------------------------ cache


def _ttl_seconds() -> float:
    try:
        return float(os.environ.get("TRENDWATCH_EXPLORE_TTL_HOURS", "6")) * 3600
    except ValueError:
        return 6 * 3600


def _booming_ttl_seconds() -> float:
    try:
        return float(os.environ.get("TRENDWATCH_BOOMING_TTL_MINUTES", "60")) * 60
    except ValueError:
        return 3600.0


def _cache_key(params: dict) -> str:
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _decorate(result: dict, *, cached: bool, stale: bool, age: float | None = None) -> dict:
    out = dict(result)
    out["category_name"] = CATEGORY_NAMES.get(int(out.get("category", 0)), f"Kategori {out.get('category')}")
    out["cached"] = cached
    out["stale"] = stale
    out["cache_age_seconds"] = None if age is None else max(0, int(age))
    return out


def _cached(params: dict, ttl: float, fetch, *, demo: bool, refresh: bool, now: float) -> dict:
    """Ambil dari cache bila masih segar; jika tidak, panggil ``fetch``.

    Bila sumber gagal tetapi ada cache lama, cache lama dikembalikan dengan stale=True
    (lebih berguna daripada layar error). Mode demo tidak memakai cache.
    """
    key = cached = None
    if not demo:
        key = _cache_key(params)
        cached = db.cache_get(key)
        if cached and not refresh and now - cached[1] < ttl:
            return _decorate(cached[0], cached=True, stale=False, age=now - cached[1])
    try:
        result = fetch()
    except ExploreError as exc:
        if cached:
            out = _decorate(cached[0], cached=True, stale=True, age=now - cached[1])
            out["notes"] = list(out.get("notes", [])) + [
                f"Gagal memperbarui dari sumber ({exc}). Menampilkan data tersimpan."]
            return out
        raise
    if key is not None:
        db.cache_put(key, params, result, now)
    return _decorate(result, cached=False, stale=False)


# ------------------------------------------------------------------ Jelajah


def run(params: dict, *, demo: bool = False, refresh: bool = False, provider=None,
        now: float | None = None) -> dict:
    now = time.time() if now is None else now
    provider = provider or get_provider(demo)

    def fetch():
        return provider.explore(params["keywords"], params["geo"], params["category"],
                                params["time"], params["gprop"], tuple(params["parts"]))

    return _cached(params, _ttl_seconds(), fetch, demo=demo, refresh=refresh, now=now)


# ------------------------------------------------------------------ Booming


def _is_breakout(r: dict) -> bool:
    return str(r.get("label", "")).strip().lower() == "breakout" or r.get("value", 0) >= BREAKOUT_VALUE


def rank_booming(rel: dict, topic: str, limit: int = BOOMING_DEFAULT_LIMIT) -> list[dict]:
    """Susun daftar 'sedang booming' dari query terkait sebuah topik.

    1. Query yang *naik* (rising): Breakout dulu (urutan dari Google dipertahankan), lalu
       menurut persentase kenaikan terbesar.
    2. Bila kurang dari ``limit``, sisanya diisi query yang paling sering dicari (top),
       ditandai 'populer' agar tidak disangka ikut naik.
    Query yang sama dengan topik atau duplikat dibuang.
    """
    seen = {topic.strip().lower()}
    items: list[dict] = []

    def add(r: dict, kind: str) -> None:
        q = str(r.get("query", "")).strip()
        if not q or q.lower() in seen or len(items) >= limit:
            return
        seen.add(q.lower())
        value = int(r.get("value", 0) or 0)
        breakout = kind == "naik" and _is_breakout(r)
        label = "Breakout" if breakout else (str(r.get("label") or "") or
                                             (f"+{value}%" if kind == "naik" else str(value)))
        items.append({"rank": len(items) + 1, "query": q, "kind": kind, "label": label,
                      "value": value, "breakout": breakout})

    rising = list(enumerate(rel.get("rising") or []))
    rising.sort(key=lambda t: (0, 0, t[0]) if _is_breakout(t[1]) else (1, -int(t[1].get("value", 0) or 0), t[0]))
    for _, r in rising:
        add(r, "naik")
    for r in rel.get("top") or []:
        add(r, "populer")
    return items


def run_booming(params: dict, *, demo: bool = False, refresh: bool = False, provider=None,
                now: float | None = None) -> dict:
    """Top-N pencarian yang sedang booming untuk satu topik (hasil di-cache ~1 jam)."""
    now = time.time() if now is None else now
    provider = provider or get_provider(demo)
    topic, limit = params["topic"], params["limit"]

    def fetch():
        res = provider.explore([topic], params["geo"], params["category"], params["time"],
                               params["gprop"], ("related",))
        related = res.get("related") or {}
        rel = related.get(topic) or next(iter(related.values()), None)
        if not rel:
            raise ExploreError(
                "Google tidak memberi daftar pencarian terkait untuk topik ini.",
                hint="Coba topik yang lebih umum, rentang waktu lebih panjang (30 atau 90 hari), atau wilayah lebih luas.")
        items = rank_booming(rel, topic, limit)
        if not items:
            raise ExploreError(
                "Belum ada pencarian terkait yang cukup besar untuk topik ini pada periode tersebut.",
                hint="Coba topik yang lebih umum, rentang waktu lebih panjang (30 atau 90 hari), atau wilayah lebih luas.")

        notes = list(res.get("notes", []))
        rising_count = sum(1 for i in items if i["kind"] == "naik")
        if rising_count == 0:
            notes.append("Tidak ada pencarian yang tercatat 'naik' pada periode ini; "
                         "yang ditampilkan adalah pencarian yang paling sering dicari.")
        elif rising_count < len(items):
            notes.append(f"Hanya {rising_count} pencarian yang tercatat 'naik'; "
                         "sisanya diisi pencarian terpopuler (ditandai 'Populer').")
        return {
            "topic": topic, "geo": params["geo"], "category": params["category"],
            "time": params["time"], "gprop": params["gprop"], "limit": limit,
            "source": res.get("source", ""), "fetched_at": res.get("fetched_at", ""),
            "items": items, "rising_count": rising_count, "notes": notes,
        }

    out = _cached(params, _booming_ttl_seconds(), fetch, demo=demo, refresh=refresh, now=now)
    out["today"] = db.today_matches(params["geo"], topic)  # selalu terbaru, tidak ikut cache
    return out


# ---------------------------------------------------------------------------
# Riset topik: menjalankan beberapa rentang waktu sekaligus lalu menggabungkannya.
# Ini menggantikan pekerjaan manual di Google Trends (ganti rentang waktu satu per satu,
# salin hasilnya). Semua angka tetap berasal dari "related queries" Google Trends.
# ---------------------------------------------------------------------------
RESEARCH_WINDOWS: list[tuple[str, str]] = [
    ("now 1-d", "24 jam"), ("now 7-d", "7 hari"), ("today 1-m", "30 hari"), ("today 3-m", "90 hari"),
]
RESEARCH_PER_WINDOW = 20
RESEARCH_MAX_EXPAND = 5
CHILD_LIMIT = 8
_SHORT_WINDOWS = ("now 1-d", "now 7-d")


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def merge_research(topic: str, per_window: dict[str, list[dict]]) -> list[dict]:
    """Gabungkan hasil beberapa rentang waktu menjadi satu daftar keyword.

    Pola:
      - ``baru``: naik hanya di rentang pendek (24 jam / 7 hari), belum terlihat di 30/90 hari.
      - ``konsisten``: naik di tiga rentang atau lebih.
      - ``naik``: naik di sebagian rentang lainnya.
      - ``populer``: hanya sering dicari, tidak tercatat naik di rentang mana pun.
    ``turunan`` bernilai True bila semua kata topik ada di keyword (mis. "bumn 2026" untuk "bumn").
    """
    topic_tokens = _tokens(topic)
    merged: dict[str, dict] = {}
    for window, items in per_window.items():
        for it in items:
            key = it["query"].strip().lower()
            row = merged.setdefault(key, {"query": it["query"], "windows": {}})
            row["windows"][window] = {"kind": it["kind"], "label": it["label"],
                                      "value": it["value"], "breakout": it["breakout"]}
    out: list[dict] = []
    for row in merged.values():
        rising = [w for w, c in row["windows"].items() if c["kind"] == "naik"]
        max_value = max((c["value"] for w, c in row["windows"].items() if c["kind"] == "naik"), default=0)
        breakout = any(c["breakout"] for c in row["windows"].values())
        if not rising:
            pattern = "populer"
        elif len(rising) >= 3:
            pattern = "konsisten"
        elif all(w in _SHORT_WINDOWS for w in rising):
            pattern = "baru"
        else:
            pattern = "naik"
        row.update(rising_in=rising, rising_count=len(rising), max_value=max_value, breakout=breakout,
                   pattern=pattern, turunan=bool(topic_tokens) and topic_tokens <= _tokens(row["query"]),
                   children=[])
        out.append(row)
    out.sort(key=lambda r: (r["rising_count"] == 0, not r["breakout"], -r["rising_count"], -r["max_value"],
                            r["query"].lower()))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def run_research(topic, geo: str = "ID", category=0, *, expand: int = 3, demo: bool = False,
                 refresh: bool = False, provider=None, now: float | None = None) -> dict:
    """Riset satu topik: 4 rentang waktu + turunan tingkat dua untuk keyword teratas.

    Tiap rentang memakai cache Booming, jadi mengulang riset yang sama tidak menembak sumber lagi.
    Rentang atau turunan yang gagal dicatat di ``errors`` tanpa menggagalkan seluruh riset;
    bila semua rentang gagal, error pertama dilempar.
    """
    expand = max(0, min(int(expand), RESEARCH_MAX_EXPAND))
    per_window: dict[str, list[dict]] = {}
    errors: list[dict] = []
    first_error: ExploreError | None = None
    meta: dict = {}
    notes: list[str] = []

    def one(time_range: str, query: str, limit: int) -> dict:
        params = normalize_booming(query, geo, category, time_range, "", limit)
        return run_booming(params, demo=demo, refresh=refresh, provider=provider, now=now)

    for window, label in RESEARCH_WINDOWS:
        try:
            res = one(window, topic, RESEARCH_PER_WINDOW)
        except ExploreError as exc:
            first_error = first_error or exc
            errors.append({"scope": label, "error": str(exc)})
            continue
        per_window[window] = res["items"]
        meta = meta or {"topic": res["topic"], "geo": res["geo"], "category": res["category"],
                        "source": res.get("source", "")}
        notes += [n for n in res.get("notes", []) if n not in notes]
        meta["stale"] = meta.get("stale", False) or bool(res.get("stale"))
    if not per_window:
        raise first_error or ExploreError("Riset gagal.")

    rows = merge_research(meta["topic"], per_window)
    expandable = [r for r in rows if r["rising_count"] > 0][:expand]
    for r in expandable:
        try:
            res = one("now 7-d", r["query"], 10)
        except ExploreError as exc:
            errors.append({"scope": f"turunan “{r['query']}”", "error": str(exc)})
            continue
        r["children"] = [{"query": c["query"], "label": c["label"], "breakout": c["breakout"]}
                         for c in res["items"] if c["kind"] == "naik"][:CHILD_LIMIT]
    return {
        **meta, "windows": [{"value": v, "name": n, "ok": v in per_window} for v, n in RESEARCH_WINDOWS],
        "items": rows, "rising_total": sum(1 for r in rows if r["rising_count"] > 0),
        "errors": errors, "notes": notes, "expanded": len(expandable),
    }
