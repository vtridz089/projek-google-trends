"""Keyword research: satu seed -> ratusan ide keyword dengan maksud, tahap funnel, tren, dan kelompok topik.

Sumber data (semuanya dari Google, tanpa API berbayar):
  - Google Autocomplete (``suggest.py``): ide keyword turunan dari seed.
  - Google Trends "pencarian terkait" (``explore_service.run_booming``): mana yang sedang naik dan yang populer.

TIDAK tersedia di sini: volume pencarian bulanan dan tingkat kesulitan (KD). Google tidak memberi angka itu
secara gratis; dibutuhkan API berbayar (mis. DataForSEO) atau akun Google Ads. Kolom "permintaan" adalah angka
RELATIF 0-100 dari Google Trends, bukan jumlah pencarian. Skor di bawah adalah aturan sederhana buatan kami.
"""
from __future__ import annotations

import math
import os
import re
import time

from . import db, explore_service, insight, suggest
from .explore import ExploreError

# (rentang waktu, nama kolom): 7 hari = momentum, 90 hari = permintaan yang bertahan.
WINDOWS = (("now 7-d", "naik_7h"), ("today 3-m", "naik_90h"))
DEPTHS = ("cepat", "lengkap")
WEIGHTS = {"momentum": 35, "permintaan": 30, "longtail": 15, "relevan": 10, "multisumber": 10}
_QUESTION_START = {"apa", "apakah", "bagaimana", "cara", "kenapa", "mengapa", "kapan", "siapa", "dimana",
                   "berapa", "mana"}
SOURCE_LABELS = {"saran": "Saran Google", "youtube": "Saran YouTube", "bing": "Saran Bing", "naik": "Sedang naik",
                 "populer": "Populer di Trends"}
SUGGEST_SOURCES = {"saran", "youtube", "bing"}
EXTRA_CHANNELS = ("youtube", "bing")


def _ttl_seconds() -> float:
    return float(os.environ.get("TRENDWATCH_KEYWORD_TTL_HOURS", "6")) * 3600


PARTIAL_TTL_SECONDS = 600  # hasil sebagian (ada sumber yang gagal) cepat kedaluwarsa agar mudah dicoba ulang


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def is_question(keyword: str) -> bool:
    w = _words(keyword)
    return bool(w) and (w[0] in _QUESTION_START or keyword.lower().startswith(("di mana ", "yang mana ")))


def score(row: dict, seed_words: set[str]) -> dict:
    """Skor keyword 0-100 yang transparan (bukan volume pencarian):

    - momentum (maks 35): kenaikan tertinggi di 7 atau 90 hari (Breakout = penuh, selain itu skala logaritmik).
    - permintaan (maks 30): angka relatif Trends 0-100 bila ada; bila tidak, dari urutan di saran Google (1 = tertinggi).
    - longtail (maks 15): makin panjang makin spesifik (4 kata atau lebih = penuh); biasanya lebih mudah diperebutkan.
    - relevan (maks 10): memuat semua kata seed.
    - multisumber (maks 10): muncul di saran pencarian (Google/YouTube/Bing) DAN di data Trends, atau di dua saluran saran.
    """
    gains = [g for g in (row["naik_7h"], row["naik_90h"]) if g]
    if any(g["breakout"] for g in gains):
        momentum = WEIGHTS["momentum"]
    elif gains:
        momentum = WEIGHTS["momentum"] * math.log10(1 + max(g["value"] for g in gains)) / math.log10(5001)
    else:
        momentum = 0
    if row["permintaan"] is not None:
        demand = WEIGHTS["permintaan"] * row["permintaan"] / 100
    elif row["ac_rank"]:
        demand = 8 + 12 * max(0.0, 1 - (row["ac_rank"] - 1) / 9)
    else:
        demand = 0
    n = len(_words(row["query"]))
    longtail = {1: 0, 2: 5, 3: 10}.get(n, WEIGHTS["longtail"])
    relevan = WEIGHTS["relevan"] if seed_words <= set(_words(row["query"])) else 0
    sug_n = len(row["sumber"] & SUGGEST_SOURCES)
    multi = WEIGHTS["multisumber"] if sug_n and (row["sumber"] & {"naik", "populer"} or sug_n >= 2) else 0
    parts = {"momentum": round(momentum), "permintaan": round(demand), "longtail": longtail,
             "relevan": relevan, "multisumber": multi}
    total = min(100, sum(parts.values()))
    return {"skor": total, "level": "tinggi" if total >= 65 else "sedang" if total >= 40 else "rendah", "bagian": parts}


def _gain(item: dict) -> dict:
    return {"label": item["label"], "value": item["value"], "breakout": bool(item["breakout"])}


def build_rows(seed: str, suggestions: list[dict], trends: dict[str, list[dict]]) -> list[dict]:
    """Gabungkan saran Autocomplete dan item Trends per keyword (tanpa huruf besar/kecil), lalu beri atribut."""
    seed_key = seed.strip().lower()
    merged: dict[str, dict] = {}

    def row_for(q: str) -> dict:
        return merged.setdefault(q.strip().lower(), {
            "query": q.strip(), "ac_rank": None, "via": None, "naik_7h": None, "naik_90h": None,
            "permintaan": None, "sumber": set()})

    for s in suggestions:
        if s["query"].strip().lower() == seed_key:
            continue
        r = row_for(s["query"])
        r["sumber"].add(s.get("channel") or "saran")
        if r["ac_rank"] is None or s["rank"] < r["ac_rank"]:
            r["ac_rank"], r["via"] = s["rank"], s.get("via")
    for column, items in trends.items():
        for it in items:
            if it["query"].strip().lower() == seed_key:
                continue
            r = row_for(it["query"])
            if it["kind"] == "naik":
                r[column] = _gain(it)
                r["sumber"].add("naik")
            else:
                r["sumber"].add("populer")
                r["permintaan"] = max(r["permintaan"] or 0, int(it["value"]))

    seed_words = set(_words(seed))
    out = []
    for r in merged.values():
        i5 = insight.intent5(r["query"])
        sc = score(r, seed_words)
        n = len(_words(r["query"]))
        out.append({
            "query": r["query"], "words": n, "pertanyaan": is_question(r["query"]), "longtail": n >= 4,
            "maksud": i5, "maksud_label": insight.INTENT5[i5], "tahap": insight.FUNNEL[i5],
            "format": insight.CONTENT_FORMAT[i5],
            "sumber": sorted(r["sumber"], key=list(SOURCE_LABELS).index), "ac_rank": r["ac_rank"], "via": r["via"],
            "naik_7h": r["naik_7h"], "naik_90h": r["naik_90h"], "permintaan": r["permintaan"],
            "relevan": seed_words <= set(_words(r["query"])),
            "skor": sc["skor"], "level": sc["level"], "skor_bagian": sc["bagian"],
            "peluang": {"skor": sc["skor"]},  # agar bisa dipakai ``insight.clusters``
        })
    out.sort(key=lambda r: (-r["skor"], r["query"].lower()))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def summarize(items: list[dict]) -> dict:
    count = lambda pred: sum(1 for r in items if pred(r))  # noqa: E731
    return {
        "total": len(items),
        "naik": count(lambda r: r["naik_7h"] or r["naik_90h"]),
        "pertanyaan": count(lambda r: r["pertanyaan"]),
        "longtail": count(lambda r: r["longtail"]),
        "maksud": {k: count(lambda r, k=k: r["maksud"] == k) for k in insight.INTENT5},
        "tahap": {k: count(lambda r, k=k: r["tahap"] == k) for k in ("TOFU", "MOFU", "BOFU")},
    }


def normalize_channels(channels) -> tuple[str, ...]:
    """Saluran saran tambahan ("youtube", "bing"); string dipisah koma. Google selalu dipakai."""
    if isinstance(channels, str):
        channels = [c for c in re.split(r"[,\s]+", channels.lower()) if c]
    bad = [c for c in channels or () if c not in EXTRA_CHANNELS]
    if bad:
        raise ValueError(f"Saluran tidak dikenal: {', '.join(bad)}. Pilihan: {', '.join(EXTRA_CHANNELS)}.")
    return tuple(c for c in EXTRA_CHANNELS if c in (channels or ()))


def normalize(seed, geo: str = "ID", depth: str = "cepat") -> tuple[str, str, str]:
    params = explore_service.normalize_booming(seed, geo, 0, "now 7-d", "", 20)  # validasi seed dan negara
    if depth not in DEPTHS:
        raise ValueError("Kedalaman harus 'cepat' atau 'lengkap'.")
    return params["topic"], params["geo"], depth


def run_keywords(seed, geo: str = "ID", depth: str = "cepat", *, with_trends: bool = True, demo: bool = False,
                 refresh: bool = False, suggester=None, provider=None, now: float | None = None,
                 channels=()) -> dict:
    """Riset keyword untuk satu seed. Sumber yang gagal dicatat di ``errors`` tanpa menggagalkan semuanya;
    hanya bila TIDAK ADA satu pun data, ExploreError dilempar."""
    seed, geo, depth = normalize(seed, geo, depth)
    channels = normalize_channels(channels)
    now = time.time() if now is None else now
    params = {"kind": "keyword", "seed": seed, "geo": geo, "depth": depth, "trends": bool(with_trends)}
    if channels:  # hanya bila dipakai, agar kunci cache lama tidak berubah
        params["channels"] = list(channels)
    key = None
    if not demo:
        key = explore_service._cache_key(params)
        hit = db.cache_get(key)
        if hit and not refresh:
            ttl = PARTIAL_TTL_SECONDS if hit[0].get("partial") else _ttl_seconds()
            if now - hit[1] < ttl:
                return explore_service._decorate(hit[0], cached=True, stale=False, age=now - hit[1])

    errors: list[dict] = []
    notes: list[str] = []
    sug = {"items": [], "requests": 0, "failed": 0, "source": ""}
    try:
        sug = (suggester or suggest.get_suggest(demo)).expand(seed, geo, depth)
        notes += sug.get("notes", [])
    except ExploreError as exc:
        errors.append({"scope": "Saran Google (Autocomplete)", "error": str(exc) + (f" Saran: {exc.hint}" if exc.hint else "")})
    for ch in channels:  # saluran tambahan selalu "cepat" (±17 permintaan) agar tidak membebani
        try:
            extra = suggest.get_suggest(demo, ch).expand(seed, geo, "cepat")
        except ExploreError as exc:
            errors.append({"scope": SOURCE_LABELS[ch], "error": str(exc) + (f" Saran: {exc.hint}" if exc.hint else "")})
            continue
        sug["items"] = sug["items"] + [{**it, "channel": ch} for it in extra["items"]]
        sug["requests"] += extra.get("requests", 0)
        sug["failed"] += extra.get("failed", 0)
        notes += [n for n in extra.get("notes", []) if n not in notes]

    trends: dict[str, list[dict]] = {}
    trend_source = ""
    if with_trends:
        for time_range, column in WINDOWS:
            try:
                res = explore_service.run_booming(
                    explore_service.normalize_booming(seed, geo, 0, time_range, "", 20),
                    demo=demo, refresh=refresh, provider=provider, now=now)
            except ExploreError as exc:
                errors.append({"scope": f"Google Trends ({'7 hari' if time_range == 'now 7-d' else '90 hari'})",
                               "error": str(exc)})
                continue
            trends[column] = res["items"]
            trend_source = res.get("source", "")
            notes += [n for n in res.get("notes", []) if n not in notes and "populer" not in n.lower()]

    if not sug["items"] and not any(trends.values()):
        first = errors[0]["error"] if errors else "Tidak ada keyword yang ditemukan untuk seed ini."
        raise ExploreError(first, hint="Coba seed yang lebih umum, atau periksa koneksi.")

    items = build_rows(seed, sug["items"], trends)
    groups = insight.clusters(items, seed)
    by_name = {}
    for g in groups:
        for q in g["keywords"]:
            by_name[q] = g["nama"]
    for r in items:
        r["kelompok"] = by_name.get(r["query"], "Lainnya")
    result = {
        "seed": seed, "geo": geo, "depth": depth, "category": 0, "with_trends": bool(with_trends),
        "items": items, "kelompok": groups, "ringkasan": summarize(items),
        "sumber": {"saran": sug.get("source") or None, "trends": trend_source or None},
        "kanal": list(channels),
        "requests": {"saran": sug.get("requests", 0), "saran_gagal": sug.get("failed", 0),
                     "trends": len(trends)},
        "errors": errors, "notes": notes, "partial": bool(errors) or bool(sug.get("failed")),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now)),
    }
    if key is not None:
        db.cache_put(key, params, result, now)
    return explore_service._decorate(result, cached=False, stale=False)
