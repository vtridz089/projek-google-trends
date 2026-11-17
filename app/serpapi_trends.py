"""Rising/Top queries dari Google Trends lewat SerpApi (serpapi.com), berjalan di server tanpa Chrome dan tanpa login.

Kuota gratis SerpApi: 250 pencarian per bulan (satu kata + satu rentang waktu = satu pencarian). Karena itu secara bawaan
hanya satu rentang (7 hari) yang diambil, dan ada batas pemakaian per jalan (``--limit``) agar kuota tidak habis.
Kunci API dibaca dari variabel lingkungan ``SERPAPI_KEY`` (jangan ditulis di berkas).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .trends_runner import RunnerError

ENDPOINT = "https://serpapi.com/search.json"
BREAKOUT = 5000


class QuotaReached(RunnerError):
    pass


def api_keys() -> list[str]:
    """Satu atau beberapa API key di ``SERPAPI_KEY``, dipisah koma atau baris baru. Yang kuotanya habis dilewati."""
    raw = os.environ.get("SERPAPI_KEY", "")
    keys = [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]
    if not keys:
        raise RunnerError("SERPAPI_KEY belum diisi. Buat akun gratis di serpapi.com, salin API key, lalu isi variabel SERPAPI_KEY "
                          "(beberapa key dipisah koma).")
    return keys


def api_key() -> str:
    return api_keys()[0]


def _num(item: dict) -> int:
    ev = item.get("extracted_value")
    if isinstance(ev, (int, float)):
        return int(ev)
    text = str(item.get("value", "")).strip().lower()
    if "breakout" in text or "pesat" in text:
        return BREAKOUT
    digits = "".join(c for c in text if c.isdigit())
    return int(digits) if digits else 0


def parse(payload: dict) -> dict:
    """Jawaban SerpApi -> {"rising": [...], "top": [...]} dengan bentuk yang sama seperti ``trends_web.parse_fxqlme``."""
    rq = payload.get("related_queries")
    if not isinstance(rq, dict):
        return {"term": "", "rising": [], "top": []}
    return {
        "term": "",
        "rising": [{"query": r["query"], "change": _num(r), "interest": 0} for r in rq.get("rising", []) if r.get("query")],
        "top": [{"query": r["query"], "interest": _num(r), "change": 0} for r in rq.get("top", []) if r.get("query")],
    }


class Fetcher:
    def __init__(self, geo: str = "ID", limit: int = 100, opener=urllib.request.urlopen, key: str | None = None):
        self.geo, self.limit, self.used, self._open = geo, limit, 0, opener
        self.errors: dict[str, int] = {}   # alasan kegagalan -> jumlah, untuk laporan
        self.keys = [key] if key else api_keys()

    def _err(self, why: str) -> None:
        self.errors[why] = self.errors.get(why, 0) + 1

    def fetch(self, term: str, time_range: str) -> dict | None:
        if self.used >= self.limit:
            raise QuotaReached(f"Batas {self.limit} pencarian untuk jalan ini tercapai; sisanya dilanjutkan pada jalan berikutnya.")
        while self.keys:
            try:
                return self._call(term, time_range, self.keys[0])
            except QuotaReached:
                self.keys.pop(0)  # kuota key ini habis: pakai key berikutnya
        raise QuotaReached("Kuota semua API key SerpApi bulan ini habis.")

    def _call(self, term: str, time_range: str, key: str) -> dict | None:
        self.used += 1
        qs = urllib.parse.urlencode({"engine": "google_trends", "q": term, "data_type": "RELATED_QUERIES", "geo": self.geo,
                                     "date": time_range, "hl": "en", "api_key": key})
        try:
            with self._open(f"{ENDPOINT}?{qs}", timeout=60) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._err(f"HTTP {exc.code}")
            if exc.code in (401, 403):
                raise RunnerError("API key SerpApi ditolak. Periksa SERPAPI_KEY.") from exc
            if exc.code == 429:
                raise QuotaReached("Kuota SerpApi bulan ini habis.") from exc
            return None
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            self._err(type(exc).__name__)
            return None
        if payload.get("error"):
            msg = str(payload["error"])
            if "run out" in msg.lower():
                raise QuotaReached("Kuota SerpApi bulan ini habis.")
            if "any results" in msg.lower() or "no results" in msg.lower():
                return {"term": "", "rising": [], "top": []}  # Google memang tidak punya data: bukan kegagalan, jangan diulang terus
            self._err(msg[:120])
            return None
        return parse(payload)
