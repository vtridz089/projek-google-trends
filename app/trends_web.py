"""Membaca jawaban antarmuka Google Trends yang baru (permintaan ``fXqlme`` pada ``batchexecute``).

Antarmuka ini memberi sampai 30 baris Rising dan 30 baris Top untuk satu kata, masing-masing dengan angka perubahan
(kolom "Change" di situs), jauh lebih banyak daripada jalur lama (``widgetdata/relatedsearches``: 3 sampai 8 Rising).
Jalur ini internal dan tidak didokumentasikan: bentuknya bisa berubah, jadi parser menolak (bukan menebak) bila bentuk
jawabannya tidak dikenali.

Bentuk jawaban: ``[[term, rising, top]]`` dengan
  rising = [[query, perubahan_persen, minat], ...]   (urut perubahan, terbesar dulu)
  top    = [[query, minat, perubahan_persen], ...]   (urut minat, terbesar dulu)
"""
from __future__ import annotations

import json

RPC = "fXqlme"


class WebFormatError(ValueError):
    pass


def _chunks(body: str):
    """Potongan JSON dari jawaban batchexecute (awalan ``)]}'``, lalu panjang dan JSON bergantian)."""
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("[["):
            try:
                yield json.loads(line)
            except ValueError:
                continue


def parse_fxqlme(body: str) -> dict:
    """Jawaban mentah ``fXqlme`` -> {"term", "rising": [...], "top": [...]}. Galat WebFormatError bila tak dikenali."""
    for chunk in _chunks(body):
        for item in chunk:
            if isinstance(item, list) and len(item) > 2 and item[0] == "wrb.fr" and item[1] == RPC and item[2]:
                try:
                    inner = json.loads(item[2])
                    term, rising, top = inner[0][0][0], inner[0][0][1], inner[0][0][2]
                    return {
                        "term": term,
                        "rising": [{"query": r[0], "change": int(r[1]), "interest": int(r[2])} for r in rising],
                        "top": [{"query": r[0], "interest": int(r[1]), "change": int(r[2])} for r in top],
                    }
                except (ValueError, IndexError, TypeError, KeyError) as exc:
                    raise WebFormatError(f"bentuk jawaban fXqlme tidak dikenali: {exc}") from exc
    raise WebFormatError("jawaban fXqlme tidak ditemukan")


def format_change(value: int) -> str:
    """Seperti kolom Change di situs: +130%."""
    return f"{value:+d}%"


# --------------------------------------------------------------------------- berkas hasil (data/trends_web.json)
# {"versi": 1, "item": {"<platform>|<seed>": {"24j": {"rising": [...], "top": [...], "diambil": 1794...}, "7h": ..., "90h": ...}}}
# Diisi oleh tools/trends_full.py (browser sungguhan di komputer pengguna), dibaca aplikasi untuk panel Trends.

import os
import threading

_lock = threading.Lock()
_cache: dict = {"mtime": None, "path": None, "data": {}}
BREAKOUT_CHANGE = 5000   # situs Trends menyebut kenaikan >= 5.000% sebagai "Breakout"


def path() -> str:
    return os.environ.get("TRENDWATCH_TRENDS_WEB", "data/trends_web.json")


def load() -> dict:
    """Isi berkas (dengan cache menurut waktu ubah berkas). Berkas hilang atau rusak = kosong."""
    p = path()
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] == mtime and _cache["path"] == p:
            return _cache["data"]
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            data = raw.get("item") if isinstance(raw, dict) and isinstance(raw.get("item"), dict) else {}
        except (OSError, ValueError):
            data = {}
        _cache.update(mtime=mtime, path=p, data=data)
        return data


def write(items: dict, p: str | None = None) -> None:
    p = p or path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"versi": 1, "item": items}, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, p)


def key(platform: str, seed: str) -> str:
    return f"{platform}|{seed.lower()}"


def panel_rows(parsed: dict) -> list[dict]:
    """Jawaban fXqlme -> baris panel Trends, persis urutan Google. Rising dulu, lalu Top."""
    rows = []
    for r in parsed["rising"]:
        c = r["change"]
        rows.append({"query": r["query"], "kind": "naik", "value": c, "interest": r["interest"],
                     "breakout": c >= BREAKOUT_CHANGE, "label": "Breakout" if c >= BREAKOUT_CHANGE else format_change(c)})
    for r in parsed["top"]:
        rows.append({"query": r["query"], "kind": "populer", "value": r["interest"], "interest": r["interest"],
                     "breakout": False, "label": str(r["interest"]), "change": r["change"]})
    return rows


def rows_for(platform: str, seed: str) -> tuple[dict, float | None]:
    """({kurun: baris panel}, waktu pengambilan terbaru) untuk satu topik, atau ({}, None) bila belum ada."""
    entry = load().get(key(platform, seed)) or {}
    out, stamps = {}, []
    for w, d in entry.items():
        try:
            out[w] = panel_rows({"rising": d["rising"], "top": d["top"]})
            stamps.append(float(d.get("diambil") or 0))
        except (KeyError, TypeError, ValueError):
            continue
    return out, (max(stamps) if stamps else None)
