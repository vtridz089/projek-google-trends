"""Berita terbaru per platform dari Google Berita (RSS resmi, gratis, tanpa kunci API).

Setiap platform dicari dengan kata yang ditentukan di ``platforms.py``. Hasil disimpan sementara (bawaan 3 jam)
supaya halaman cepat dan Google tidak dibanjiri, tetapi selalu diperbarui lagi sehingga berita hari ini tampil.
Bila Google gagal dihubungi, berita tersimpan terakhir tetap ditampilkan dan ditandai usang.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

from . import db

FEED_URL = "https://news.google.com/rss/search"
USER_AGENT = "Mozilla/5.0 (compatible; Cerekey/1.0)"
MAX_ITEMS = 12
MAX_BYTES = 2_000_000
Fetcher = Callable[[str], bytes]


class NewsError(Exception):
    """Berita tidak bisa diambil dan tidak ada cadangan."""


def ttl_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("TRENDWATCH_NEWS_TTL_HOURS", "3"))) * 3600
    except ValueError:
        return 3 * 3600


def init() -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_cache (
                term       TEXT PRIMARY KEY,
                fetched_at REAL NOT NULL,
                payload    TEXT NOT NULL
            )""")


def feed_url(term: str, recent: bool = True) -> str:
    q = f'"{term}"' if " " in term.strip() else term.strip()
    if recent:
        q += " when:7d"
    return FEED_URL + "?" + urllib.parse.urlencode({"q": q, "hl": "id", "gl": "ID", "ceid": "ID:id"})


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.read(MAX_BYTES)
    except urllib.error.HTTPError as exc:
        raise NewsError(f"Google Berita menolak permintaan (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NewsError(f"Tidak bisa terhubung ke Google Berita: {exc}") from exc


def _safe_url(value: str) -> str:
    value = (value or "").strip()
    return value if value.lower().startswith(("http://", "https://")) else ""


def parse_feed(body: bytes) -> list[dict]:
    """RSS Google Berita -> daftar {title, link, source, published}. Terbaru lebih dulu, tanpa duplikat."""
    head = body[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:  # umpan Google tidak memakainya; tolak agar aman dari XML berbahaya
        raise NewsError("Format umpan berita tidak dikenali.")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise NewsError("Umpan berita tidak bisa dibaca.") from exc
    items, seen = [], set()
    for node in root.iter("item"):
        title = re.sub(r"\s+", " ", (node.findtext("title") or "")).strip()
        link = _safe_url(node.findtext("link") or "")
        src_node = node.find("source")
        source = ((src_node.text or "") if src_node is not None else "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].rstrip()
        if not title or not link:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        published = ""
        try:
            dt = parsedate_to_datetime(node.findtext("pubDate") or "")
            published = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
        items.append({"title": title[:300], "link": link, "source": source[:80], "published": published})
    items.sort(key=lambda x: x["published"], reverse=True)
    return items[:MAX_ITEMS]


def fetch_news(term: str, fetcher: Fetcher | None = None) -> list[dict]:
    fetcher = fetcher or _fetch
    items = parse_feed(fetcher(feed_url(term, recent=True)))
    if not items:  # tidak ada berita 7 hari terakhir: longgarkan
        items = parse_feed(fetcher(feed_url(term, recent=False)))
    return items


def _read(term: str) -> tuple[float, list[dict]] | None:
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT fetched_at, payload FROM news_cache WHERE term = ?", (term,)).fetchone()
    if not row:
        return None
    try:
        return float(row["fetched_at"]), json.loads(row["payload"])
    except (ValueError, TypeError):
        return None


def _write(term: str, at: float, items: list[dict]) -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO news_cache (term, fetched_at, payload) VALUES (?, ?, ?)",
                     (term, at, json.dumps(items, ensure_ascii=False)))


def get_news(term: str, *, refresh: bool = False, fetcher: Fetcher | None = None, now: float | None = None) -> dict:
    """Berita untuk satu kata. Dari cache bila masih segar; kalau tidak, ambil baru. Gagal + ada cache lama -> pakai cache lama."""
    now = time.time() if now is None else now
    cached = _read(term)
    if cached and not refresh and now - cached[0] < ttl_seconds():
        return {"term": term, "items": cached[1], "fetched_at": cached[0], "cached": True, "stale": False, "note": ""}
    try:
        items = fetch_news(term, fetcher)
    except NewsError as exc:
        if cached:
            return {"term": term, "items": cached[1], "fetched_at": cached[0], "cached": True, "stale": True,
                    "note": f"Berita belum bisa diperbarui ({exc}). Ditampilkan berita tersimpan terakhir."}
        raise
    _write(term, now, items)
    return {"term": term, "items": items, "fetched_at": now, "cached": False, "stale": False, "note": ""}
