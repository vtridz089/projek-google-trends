"""Data 'Jelajah': minat pencarian dari waktu ke waktu untuk keyword & kategori pilihan sendiri.

Beda dengan collector.py (tren harian dari RSS), di sini pengguna menentukan keyword,
kategori, negara, dan rentang waktu. Hasilnya: grafik waktu, query terkait, dan minat per wilayah.

Semua sumber memakai antarmuka yang sama::

    explore(keywords, geo, category, time_range, gprop="", parts=PARTS) -> dict

Sumber yang tersedia:

* GoogleDirectProvider : memanggil endpoint internal trends.google.com (tidak resmi, gratis,
                         bisa diblokir/berubah sewaktu-waktu).
* SerpApiProvider      : lewat SerpApi (resmi dari pihak ketiga, perlu SERPAPI_KEY, kuota terbatas).
* MockExploreProvider  : data contoh untuk mencoba dashboard tanpa jaringan.
* ChainProvider        : coba beberapa sumber berurutan (Google dulu, SerpApi sebagai cadangan).

Catatan data: Google Trends memberi minat RELATIF (0-100, 100 = puncak pada rentang & wilayah
yang dipilih), bukan jumlah pencarian.
"""
from __future__ import annotations

import http.cookiejar
import json
import logging
import math
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

log = logging.getLogger("trendwatch.explore")

PARTS = ("timeline", "related", "regions")
GPROPS = ("", "images", "news", "youtube", "froogle")

_XSSI = ")]}'"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")

BLOCK_HINT = ("Google sering membatasi permintaan dari alamat IP server/datacenter (termasuk "
              "Codespaces). Coba jalankan di laptop sendiri, tunggu beberapa menit, atau isi "
              "SERPAPI_KEY agar memakai SerpApi.")
KEY_HINT = "Isi SERPAPI_KEY (akun gratis di serpapi.com) untuk memakai SerpApi sebagai sumber."

_MONTHS_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


class ExploreError(Exception):
    """Pengambilan data Jelajah gagal. ``hint`` berisi saran tindakan untuk pengguna."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class ExploreProvider(Protocol):
    name: str

    def explore(self, keywords: list[str], geo: str, category: int, time_range: str,
                gprop: str = "", parts: tuple[str, ...] = PARTS) -> dict: ...


# --------------------------------------------------------------------------- umum


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "").replace("%", "").replace("+", "").strip()))
    except (TypeError, ValueError):
        return default


def _label_id(d: date) -> str:
    return f"{d.day} {_MONTHS_ID[d.month - 1]} {d.year}"


def summarize(timeline: list[dict], keywords: list[str]) -> list[dict]:
    """Ringkasan per keyword: rata-rata, puncak, nilai terbaru, dan perubahan awal vs akhir periode.

    Titik terakhir yang masih 'partial' (periode belum selesai) tidak dihitung supaya
    tidak terlihat seolah-olah minat anjlok.
    """
    calc = timeline[:-1] if len(timeline) > 1 and timeline[-1].get("partial") else timeline
    n = len(calc)
    out = []
    for i, kw in enumerate(keywords):
        series = [p["values"][i] if i < len(p["values"]) else 0 for p in calc]
        if not series:
            out.append({"keyword": kw, "average": 0, "peak": 0, "peak_label": "",
                        "latest": 0, "change_pct": None})
            continue
        peak = max(series)
        pi = series.index(peak)
        k = max(1, n // 4)
        first = sum(series[:k]) / k
        last = sum(series[-k:]) / k
        out.append({
            "keyword": kw,
            "average": round(sum(series) / n, 1),
            "peak": peak,
            "peak_label": calc[pi].get("label", ""),
            "latest": series[-1],
            "change_pct": None if first == 0 else round((last - first) / first * 100),
        })
    return out


def build_result(source: str, keywords: list[str], geo: str, category: int, time_range: str,
                 gprop: str, timeline: list[dict], related: dict, regions: list[dict],
                 notes: list[str]) -> dict:
    return {
        "keywords": keywords,
        "geo": geo,
        "category": category,
        "time": time_range,
        "gprop": gprop,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timeline": timeline,
        "summary": summarize(timeline, keywords),
        "related": related,
        "regions": regions,
        "notes": notes,
    }


def _pad(values: list[int], n: int) -> list[int]:
    return (values + [0] * n)[:n]


def _sort_regions(rows: list[dict]) -> list[dict]:
    rows = [r for r in rows if r["name"] and any(r["values"])]
    rows.sort(key=lambda r: (-sum(r["values"]), r["name"]))
    return rows


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


# --------------------------------------------------------------------------- Google (langsung)


def parse_google_json(text: str):
    """Buang awalan anti-XSSI ``)]}'`` lalu parse JSON."""
    text = text.lstrip()
    if text.startswith(_XSSI):
        text = text[len(_XSSI):].lstrip(" ,\r\n")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ExploreError(
            "Balasan Google bukan JSON yang valid (kemungkinan halaman pemblokiran/captcha).",
            hint=BLOCK_HINT,
        ) from exc


def parse_timeline_google(data: dict, n: int) -> list[dict]:
    rows = (data.get("default") or {}).get("timelineData") or []
    out = []
    for r in rows:
        raw = r.get("value") or []
        has = r.get("hasData") or [True] * len(raw)
        values = [_to_int(v) if h else 0 for v, h in zip(raw, has)]
        t = _to_int(r.get("time"), -1)
        out.append({
            "t": t if t >= 0 else None,
            "label": r.get("formattedAxisTime") or r.get("formattedTime") or "",
            "values": _pad(values, n),
            "partial": bool(r.get("isPartial")),
        })
    return out


def _ranked(items) -> list[dict]:
    out = []
    for x in items or []:
        q = x.get("query") or (x.get("topic") or {}).get("title") or ""
        if q:
            out.append({"query": q, "value": _to_int(x.get("value")),
                        "label": str(x.get("formattedValue") or x.get("value") or "")})
    return out


def parse_related_google(data: dict) -> dict:
    lists = (data.get("default") or {}).get("rankedList") or []
    return {
        "top": _ranked(lists[0].get("rankedKeyword")) if len(lists) > 0 else [],
        "rising": _ranked(lists[1].get("rankedKeyword")) if len(lists) > 1 else [],
    }


def parse_regions_google(data: dict, n: int) -> list[dict]:
    rows = (data.get("default") or {}).get("geoMapData") or []
    out = []
    for r in rows:
        has = r.get("hasData") or [True] * len(r.get("value") or [])
        vals = [_to_int(v) if h else 0 for v, h in zip(r.get("value") or [], has)]
        out.append({"code": r.get("geoCode") or "", "name": r.get("geoName") or "",
                    "values": _pad(vals, n)})
    return _sort_regions(out)


def _widget_keyword(widget: dict):
    try:
        return widget["request"]["restriction"]["complexKeywordsRestriction"]["keyword"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return None


class GoogleDirectProvider:
    """Memanggil endpoint internal Google Trends (explore -> widgetdata).

    Tidak resmi: Google bisa mengubah atau memblokirnya kapan saja, terutama dari IP
    datacenter. Karena itu ada jeda antar-permintaan, percobaan ulang, dan semua hasil
    di-cache oleh explore_service.
    """

    name = "google"
    BASE = "https://trends.google.com/trends"
    SESSION_TTL = 20 * 60

    def __init__(self, timeout: int = 20, retries: int = 3, backoff: float = 2.0,
                 min_interval: float | None = None, opener=None, sleep=time.sleep,
                 clock=time.monotonic, hl: str = "id", tz_minutes: int | None = None):
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff = backoff
        self.min_interval = (float(os.environ.get("TRENDWATCH_GOOGLE_MIN_INTERVAL", "1.0"))
                             if min_interval is None else min_interval)
        self._fixed_opener = opener
        self._opener = opener
        self._session_started: float | None = None
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None
        self._lock = threading.Lock()  # satu penjelajahan pada satu waktu
        self.hl = hl
        self.tz = str(tz_minutes if tz_minutes is not None
                      else os.environ.get("TRENDWATCH_TZ_MINUTES", "-420"))

    # -- jaringan

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = self.min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def _ensure_session(self) -> None:
        """Buka sesi baru + 'pemanasan' agar Google memberi cookie."""
        if self._fixed_opener is not None:
            return
        now = self._clock()
        if (self._opener is not None and self._session_started is not None
                and now - self._session_started < self.SESSION_TTL):
            return
        jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self._session_started = now
        self._get("/explore", {"hl": self.hl}, warm=True)

    def _get(self, path: str, params: dict | None = None, warm: bool = False) -> str:
        url = self.BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        last: Exception | None = None
        rate_limited = False
        for attempt in range(1, self.retries + 1):
            self._throttle()
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
                "Accept": "application/json, text/plain, */*",
                "Referer": self.BASE + "/explore",
            })
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                if warm:
                    return ""  # pemanasan boleh 429; cookie tetap diterima
                if exc.code == 429 or exc.code >= 500:
                    last = exc
                    rate_limited = rate_limited or exc.code == 429
                else:
                    raise ExploreError(f"Google menolak permintaan (HTTP {exc.code}).",
                                       hint=BLOCK_HINT) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            if attempt < self.retries:
                delay = self.backoff * 2 ** (attempt - 1) + random.uniform(0, 0.3)
                log.warning("Google percobaan %d/%d gagal (%s); ulangi dalam %.1f detik",
                            attempt, self.retries, last, delay)
                self._sleep(delay)
        raise ExploreError(f"Gagal mengambil data dari Google setelah {self.retries} percobaan: {getattr(last, 'reason', last)}",
                           hint=BLOCK_HINT if rate_limited else "Periksa koneksi internet, lalu coba lagi.")

    def _widget_call(self, kind: str, widget: dict, extra: dict | None = None):
        request = dict(widget["request"])
        request.update(extra or {})
        return parse_google_json(self._get(f"/api/widgetdata/{kind}", {
            "hl": self.hl, "tz": self.tz,
            "req": json.dumps(request, separators=(",", ":")),
            "token": widget["token"],
        }))

    # -- antarmuka

    def explore(self, keywords, geo, category, time_range, gprop="", parts=PARTS) -> dict:
        with self._lock:
            return self._explore(list(keywords), geo, category, time_range, gprop, tuple(parts))

    def _explore(self, keywords, geo, category, time_range, gprop, parts) -> dict:
        self._ensure_session()
        req = {
            "comparisonItem": [{"keyword": k, "geo": geo, "time": time_range} for k in keywords],
            "category": category,
            "property": gprop,
        }
        data = parse_google_json(self._get("/api/explore", {
            "hl": self.hl, "tz": self.tz, "req": json.dumps(req, separators=(",", ":")),
        }))
        widgets = data.get("widgets") or []
        ts = next((w for w in widgets if w.get("id") == "TIMESERIES"), None)
        n = len(keywords)
        timeline: list[dict] = []
        if "timeline" in parts:  # halaman Booming hanya butuh 'related', jadi grafik boleh dilewati
            if ts is None:
                raise ExploreError(
                    "Respons Google tidak memuat data grafik (format berubah atau permintaan ditolak).",
                    hint=BLOCK_HINT)
            timeline = parse_timeline_google(self._widget_call("multiline", ts), n)
            if not timeline:
                raise ExploreError(
                    "Google tidak mengembalikan data untuk kombinasi ini "
                    "(biasanya karena volume pencarian terlalu rendah).",
                    hint="Coba keyword yang lebih umum, wilayah yang lebih luas, atau rentang waktu lebih panjang.")
        elif not widgets:
            raise ExploreError("Respons Google kosong (format berubah atau permintaan ditolak).",
                               hint=BLOCK_HINT)

        notes: list[str] = []
        related: dict = {}
        if "related" in parts:
            rel_widgets = [w for w in widgets if str(w.get("id", "")).startswith("RELATED_QUERIES")]
            for i, w in enumerate(rel_widgets):
                wk = _widget_keyword(w)
                kw = next((k for k in keywords if wk and k.lower() == wk.lower()),
                          keywords[i] if i < n else wk or f"#{i + 1}")
                try:
                    related[kw] = parse_related_google(self._widget_call("relatedsearches", w))
                except ExploreError as exc:
                    notes.append(f"Query terkait untuk '{kw}' tidak tersedia: {exc}")

        regions: list[dict] = []
        if "regions" in parts:
            gw = next((w for w in widgets if w.get("id") == "GEO_MAP"), None) or \
                next((w for w in widgets if str(w.get("id", "")).startswith("GEO_MAP")), None)
            if gw is None:
                notes.append("Data wilayah tidak tersedia untuk pencarian ini.")
            else:
                resolution = "COUNTRY" if not geo else ("REGION" if len(geo) == 2 else "CITY")
                try:
                    regions = parse_regions_google(
                        self._widget_call("comparedgeo", gw, {
                            "resolution": resolution, "includeLowSearchVolumeGeos": False}), n)
                except ExploreError as exc:
                    notes.append(f"Data wilayah tidak tersedia: {exc}")

        return build_result(self.name, keywords, geo, category, time_range, gprop,
                            timeline, related, regions, notes)


# --------------------------------------------------------------------------- SerpApi


def _serp_value(v: dict) -> int:
    return _to_int(v.get("extracted_value", v.get("value")))


def _serp_values(row_values: list, keywords: list[str]) -> list[int]:
    by_query = {str(v.get("query", "")).lower(): _serp_value(v) for v in row_values}
    out = []
    for i, kw in enumerate(keywords):
        if kw.lower() in by_query:
            out.append(by_query[kw.lower()])
        else:
            out.append(_serp_value(row_values[i]) if i < len(row_values) else 0)
    return out


def parse_serpapi_timeline(data: dict, keywords: list[str]) -> list[dict]:
    rows = (data.get("interest_over_time") or {}).get("timeline_data") or []
    out = []
    for r in rows:
        ts = r.get("timestamp")
        out.append({
            "t": _to_int(ts) if ts not in (None, "") else None,
            "label": r.get("date") or "",
            "values": _serp_values(r.get("values") or [], keywords),
            "partial": bool(r.get("partial_data")),
        })
    return out


def _serp_ranked(items) -> list[dict]:
    return [{"query": x.get("query", ""), "value": _serp_value(x), "label": str(x.get("value", ""))}
            for x in items or [] if x.get("query")]


def parse_serpapi_related(data: dict) -> dict:
    rq = data.get("related_queries") or {}
    return {"top": _serp_ranked(rq.get("top")), "rising": _serp_ranked(rq.get("rising"))}


def parse_serpapi_regions(data: dict, keywords: list[str]) -> list[dict]:
    out = []
    compared = data.get("compared_breakdown_by_region")
    if compared is not None:
        for r in compared:
            out.append({"code": r.get("geo") or "", "name": r.get("location") or "",
                        "values": _serp_values(r.get("values") or [], keywords)})
    else:
        for r in data.get("interest_by_region") or []:
            out.append({"code": r.get("geo") or "", "name": r.get("location") or "",
                        "values": _pad([_serp_value(r)], len(keywords))})
    return _sort_regions(out)


class SerpApiProvider:
    """Google Trends lewat SerpApi (engine=google_trends). Perlu API key; paket gratis
    ±250 pencarian/bulan. Satu penjelajahan penuh memakai hingga 3 pencarian
    (grafik + query terkait keyword pertama + wilayah); hasilnya di-cache."""

    name = "serpapi"
    URL = "https://serpapi.com/search.json"

    def __init__(self, api_key: str, timeout: int = 40, opener=None):
        self.api_key = api_key
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def _call(self, params: dict) -> dict:
        query = dict(params, engine="google_trends", api_key=self.api_key)
        req = urllib.request.Request(self.URL + "?" + urllib.parse.urlencode(query),
                                     headers={"User-Agent": "TrendWatch-PKL/1.0"})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace")).get("error", "")
            except Exception:
                pass
            raise ExploreError(_redact(f"SerpApi menolak permintaan (HTTP {exc.code}). {detail}".strip(),
                                       self.api_key)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ExploreError(_redact(f"Gagal terhubung ke SerpApi: {getattr(exc, 'reason', exc)}", self.api_key)) from None
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise ExploreError("Balasan SerpApi bukan JSON yang valid.") from exc
        if isinstance(data, dict) and data.get("error"):
            raise ExploreError(_redact(f"SerpApi: {data['error']}", self.api_key))
        return data

    def explore(self, keywords, geo, category, time_range, gprop="", parts=PARTS) -> dict:
        keywords = list(keywords)
        base = {"hl": "id", "date": time_range, "cat": category}
        if geo:
            base["geo"] = geo
        if gprop:
            base["gprop"] = gprop

        timeline: list[dict] = []
        if "timeline" in parts:
            timeline = parse_serpapi_timeline(
                self._call(dict(base, q=",".join(keywords), data_type="TIMESERIES")), keywords)
            if not timeline:
                raise ExploreError("SerpApi tidak mengembalikan data untuk kombinasi ini.",
                                   hint="Coba keyword yang lebih umum atau rentang waktu lebih panjang.")

        notes: list[str] = []
        related: dict = {}
        if "related" in parts:
            try:
                related[keywords[0]] = parse_serpapi_related(
                    self._call(dict(base, q=keywords[0], data_type="RELATED_QUERIES")))
                if len(keywords) > 1:
                    notes.append("Query terkait lewat SerpApi hanya diambil untuk keyword pertama (hemat kuota).")
            except ExploreError as exc:
                notes.append(f"Query terkait tidak tersedia: {exc}")

        regions: list[dict] = []
        if "regions" in parts:
            try:
                regions = parse_serpapi_regions(self._call(dict(
                    base, q=",".join(keywords), region="COUNTRY" if not geo else "REGION",
                    data_type="GEO_MAP" if len(keywords) > 1 else "GEO_MAP_0")), keywords)
            except ExploreError as exc:
                notes.append(f"Data wilayah tidak tersedia: {exc}")

        return build_result(self.name, keywords, geo, category, time_range, gprop,
                            timeline, related, regions, notes)


# --------------------------------------------------------------------------- Data contoh

_RANGE_POINTS = {"now 7-d": (7, 1), "today 1-m": (30, 1), "today 3-m": (90, 1),
                 "today 12-m": (53, 7), "today 5-y": (260, 7), "all": (250, 30)}

_PROVINCES = [
    "DKI Jakarta", "Jawa Barat", "Jawa Tengah", "Jawa Timur", "Banten", "DI Yogyakarta", "Bali",
    "Sumatera Utara", "Sumatera Barat", "Sumatera Selatan", "Riau", "Kepulauan Riau", "Lampung",
    "Aceh", "Kalimantan Selatan", "Kalimantan Timur", "Kalimantan Barat", "Sulawesi Selatan",
    "Sulawesi Utara", "Nusa Tenggara Barat", "Nusa Tenggara Timur", "Papua", "Maluku", "Jambi",
]


def _mock_shape(time_range: str) -> tuple[int, int]:
    if time_range in _RANGE_POINTS:
        return _RANGE_POINTS[time_range]
    m = re.match(r"(\d{4})-(\d{2})-(\d{2}) (\d{4})-(\d{2})-(\d{2})$", time_range)
    if m:
        try:
            days = (date(*map(int, m.group(4, 5, 6))) - date(*map(int, m.group(1, 2, 3)))).days + 1
            return max(2, min(days, 400)), 1
        except ValueError:
            pass
    return 24, 1


class MockExploreProvider:
    """Data contoh (deterministik) agar dashboard bisa dicoba tanpa jaringan.
    Bukan data Google; selalu ditandai sumber 'mock' di antarmuka."""

    name = "mock"

    def explore(self, keywords, geo, category, time_range, gprop="", parts=PARTS) -> dict:
        keywords = list(keywords)
        n, step = _mock_shape(time_range)
        end = datetime.now(timezone.utc).date()

        raw: list[list[float]] = []
        for kw in keywords:
            rng = random.Random(f"{kw.lower()}|{geo}|{category}|{time_range}")
            base = rng.uniform(25, 70)
            drift = rng.uniform(-25, 30)
            cycles = rng.choice([1, 2, 4])
            amp = rng.uniform(5, 18)
            phase = rng.random()
            spike_at = rng.randrange(n) if rng.random() < 0.6 else -10
            spike = rng.uniform(15, 35)
            series = []
            for i in range(n):
                v = (base + drift * i / max(1, n - 1)
                     + amp * math.sin(2 * math.pi * (cycles * i / n + phase))
                     + (spike if abs(i - spike_at) <= 1 else 0) + rng.gauss(0, 4))
                series.append(max(0.0, v))
            raw.append(series)
        peak = max((max(s) for s in raw), default=1) or 1
        scaled = [[round(v / peak * 100) for v in s] for s in raw]

        timeline = []
        for i in range(n):
            d = end - timedelta(days=step * (n - 1 - i))
            midnight = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            timeline.append({"t": int(midnight.timestamp()), "label": _label_id(d),
                             "values": [s[i] for s in scaled], "partial": False})

        related: dict = {}
        if "related" in parts:
            for kw in keywords:
                top = [(f"{kw} {s}", v) for s, v in
                       zip(["terbaru", "harga", "cara", "adalah", "indonesia", str(end.year)],
                           [100, 74, 55, 41, 30, 22])]
                rising = [(f"{kw} {end.year}", "Breakout"), (f"{kw} hari ini", "Breakout"),
                          (f"{kw} gratis", "+450%"), (f"{kw} online", "+230%"),
                          (f"{kw} promo", "+120%"), (f"{kw} berita", "+80%")]
                related[kw] = {
                    "top": [{"query": q, "value": v, "label": str(v)} for q, v in top],
                    "rising": [{"query": q, "value": 5000 if lab == "Breakout" else _to_int(lab),
                                "label": lab} for q, lab in rising],
                }

        regions: list[dict] = []
        if "regions" in parts:
            names = _PROVINCES if geo in ("ID", "") else [f"Wilayah {chr(65 + i)}" for i in range(10)]
            rows = []
            for name in names:
                vals = [random.Random(f"{kw.lower()}|{name}|{geo}").randint(5, 100) for kw in keywords]
                rows.append({"code": "", "name": name, "values": vals})
            top = max((v for r in rows for v in r["values"]), default=1) or 1
            for r in rows:
                r["values"] = [round(v / top * 100) for v in r["values"]]
            regions = _sort_regions(rows)

        return build_result(self.name, keywords, geo, category, time_range, gprop,
                            timeline if "timeline" in parts else [],
                            related, regions, ["Data contoh: dibuat otomatis, bukan dari Google Trends."])


# --------------------------------------------------------------------------- Pemilihan sumber


class ChainProvider:
    """Coba beberapa sumber berurutan; berhenti di yang pertama berhasil."""

    def __init__(self, providers: list):
        self.providers = providers

    @property
    def name(self) -> str:
        return "+".join(p.name for p in self.providers)

    def explore(self, keywords, geo, category, time_range, gprop="", parts=PARTS) -> dict:
        errors, hints = [], []
        for p in self.providers:
            try:
                return p.explore(keywords, geo, category, time_range, gprop, parts)
            except ExploreError as exc:
                log.warning("Sumber %s gagal: %s", p.name, exc)
                errors.append(f"{p.name}: {exc}")
                if exc.hint:
                    hints.append(exc.hint)
        raise ExploreError(" | ".join(errors), hint=hints[0] if hints else "")


_providers: dict[str, object] = {}


def get_provider(demo: bool = False):
    """Pilih sumber berdasarkan TRENDWATCH_EXPLORE_SOURCE: auto (default), google, serpapi, mock.

    'auto' = Google langsung, lalu SerpApi jika SERPAPI_KEY terisi. Instansi dipakai ulang
    supaya sesi cookie dan jeda antar-permintaan tetap berlaku."""
    mode = "mock" if demo else os.environ.get("TRENDWATCH_EXPLORE_SOURCE", "auto").strip().lower()
    key = os.environ.get("SERPAPI_KEY", "").strip()
    cache_key = f"{mode}|{key}"
    if cache_key in _providers:
        return _providers[cache_key]

    if mode == "mock":
        provider = MockExploreProvider()
    elif mode == "google":
        provider = GoogleDirectProvider()
    elif mode == "serpapi":
        if not key:
            raise ExploreError("TRENDWATCH_EXPLORE_SOURCE=serpapi tetapi SERPAPI_KEY belum diisi.",
                               hint=KEY_HINT)
        provider = SerpApiProvider(key)
    elif mode == "auto":
        chain = [GoogleDirectProvider()] + ([SerpApiProvider(key)] if key else [])
        provider = chain[0] if len(chain) == 1 else ChainProvider(chain)
    else:
        raise ExploreError(f"TRENDWATCH_EXPLORE_SOURCE tidak dikenal: {mode!r}")
    _providers[cache_key] = provider
    return provider
