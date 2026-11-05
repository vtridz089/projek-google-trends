"""Saran pencarian Google (Autocomplete) sebagai sumber ide keyword.

Endpoint ``suggestqueries.google.com`` TIDAK resmi: bisa berubah, dibatasi, atau diblokir kapan saja,
sama seperti endpoint Google Trends yang dipakai di ``explore.py``. Hanya pustaka standar.

Satu seed diperluas menjadi puluhan permintaan (awalan kata tanya, akhiran pembanding, dan huruf a-z)
karena Autocomplete hanya mengembalikan ±10 saran per permintaan.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .explore import ExploreError

log = logging.getLogger("trendwatch.suggest")

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
BING_URL = "https://api.bing.com/osjson.aspx"
# Saluran saran: semuanya endpoint publik tanpa kunci (tidak resmi). "google" = web, "youtube" = pencarian video,
# "bing" = mesin pencari Microsoft. Nama label tampilan ada di keyword_research.SOURCE_LABELS.
CHANNELS = ("google", "youtube", "bing")
QUESTION_PREFIXES = ["apa itu", "cara", "kenapa", "bagaimana", "berapa", "kapan", "di mana", "siapa"]
MODIFIERS = ["terbaik", "harga", "review", "vs", "alternatif", "gratis", "online", "terbaru"]
LETTERS = list("abcdefghijklmnopqrstuvwxyz")
MAX_RESULTS = 200
_LANG = {"ID": "id", "MY": "ms", "JP": "ja", "": "en"}


def variants(seed: str, depth: str = "cepat") -> list[tuple[str, str]]:
    """(teks yang dikirim, label asal) untuk satu seed. ``cepat`` ±17 permintaan, ``lengkap`` ±43."""
    out = [(seed, "seed")]
    out += [(f"{p} {seed}", p) for p in QUESTION_PREFIXES]
    out += [(f"{seed} {m}", m) for m in MODIFIERS]
    if depth == "lengkap":
        out += [(f"{seed} {c}", c) for c in LETTERS]
    return out


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def parse(body: str) -> list[str]:
    """Isi balasan Autocomplete (format ``["kueri", ["saran1", "saran2"]]``) menjadi daftar saran."""
    try:
        data = json.loads(body)
    except ValueError:
        raise ValueError("balasan bukan JSON") from None
    if not (isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list)):
        raise ValueError("bentuk balasan tidak dikenali")
    return [s.strip() for s in data[1] if isinstance(s, str) and s.strip()]


def _collect(seed: str, raw: list[tuple[str, str, list[str]]]) -> list[dict]:
    """Gabungkan hasil: buang duplikat, dan buang saran yang tidak memuat semua kata seed
    (Autocomplete sering melenceng ke topik lain, mis. "bumd" untuk "bumn")."""
    need = set(_words(seed))
    seen = {seed.strip().lower()}
    items: list[dict] = []
    for _, via, suggestions in raw:
        for rank, s in enumerate(suggestions, 1):
            key = s.lower()
            if key in seen or not need <= set(_words(s)):
                continue
            seen.add(key)
            items.append({"query": s, "via": via, "rank": rank})
            if len(items) >= MAX_RESULTS:
                return items
    return items


_CHANNEL_NAMES = {"google": "Google Autocomplete", "youtube": "YouTube (saran pencarian)", "bing": "Bing (saran pencarian)"}


class GoogleSuggest:
    name = "google"

    def __init__(self, min_interval: float = 0.25, workers: int = 4, timeout: float = 8.0, channel: str = "google"):
        if channel not in CHANNELS:
            raise ValueError(f"Saluran saran tidak dikenal: {channel}")
        self.channel, self.label = channel, _CHANNEL_NAMES[channel]
        self.min_interval, self.workers, self.timeout = min_interval, workers, timeout
        self._lock, self._last, self._blocked = threading.Lock(), 0.0, False

    def _wait(self) -> None:
        with self._lock:
            delay = self._last + self.min_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()

    def fetch(self, text: str, geo: str) -> list[str]:
        self._wait()
        lang = _LANG.get(geo.upper(), "en")
        if self.channel == "bing":
            url = BING_URL
            params = {"query": text, "mkt": f"{lang}-{geo.upper()}" if geo else "en-US"}
        else:
            url = SUGGEST_URL
            params = {"client": "firefox", "q": text, "hl": lang}
            if geo:
                params["gl"] = geo.lower()
            if self.channel == "youtube":
                params["ds"] = "yt"
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={
            "User-Agent": "Mozilla/5.0 (compatible; TrendWatch/1.0)", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return parse(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 503):
                self._blocked = True  # berhenti; terus menembak hanya memperburuk pembatasan
            raise ExploreError(f"{self.label} menolak permintaan (HTTP {exc.code}).",
                               hint="Tunggu beberapa menit, atau matikan saluran ini dan pakai sumber lain.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ExploreError(f"Gagal menghubungi {self.label}: {getattr(exc, 'reason', exc)}",
                               hint="Periksa koneksi internet, lalu coba lagi.") from exc
        except ValueError as exc:
            raise ExploreError(f"Balasan {self.label} tidak dikenali ({exc}).",
                               hint="Format endpoint tidak resmi ini mungkin berubah.") from exc

    def expand(self, seed: str, geo: str = "ID", depth: str = "cepat") -> dict:
        todo = variants(seed, depth)
        results: list[tuple[str, str, list[str]] | None] = [None] * len(todo)
        errors: list[ExploreError] = []

        def work(i: int) -> None:
            if self._blocked:
                return
            text, via = todo[i]
            try:
                results[i] = (text, via, self.fetch(text, geo))
            except ExploreError as exc:
                errors.append(exc)

        self._blocked = False
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            list(pool.map(work, range(len(todo))))
        ok = [r for r in results if r is not None]
        if not ok:
            raise errors[0] if errors else ExploreError(f"{self.label} tidak mengembalikan apa pun.")
        notes = []
        if errors:
            notes.append(f"{len(errors)} dari {len(todo)} permintaan {self.label} gagal; hasil saran mungkin tidak lengkap.")
        return {"items": _collect(seed, ok), "requests": len(todo), "failed": len(errors), "notes": notes,
                "source": self.name, "channel": self.channel}


class MockSuggest:
    """Saran contoh (deterministik) untuk mencoba tampilan tanpa jaringan. Bukan data Google."""

    name = "mock"

    def __init__(self, channel: str = "google"):
        self.channel = channel

    def expand(self, seed: str, geo: str = "ID", depth: str = "cepat") -> dict:
        rng = random.Random(f"{seed.lower()}|{depth}" + ("" if self.channel == "google" else f"|{self.channel}"))
        raw = []
        for text, via in variants(seed, depth):
            tails = ["2026", "terbaru", "adalah", "hari ini", "indonesia", "untuk pemula", "terbaik", "gratis"]
            rng.shuffle(tails)
            base = text if text != seed else seed
            raw.append((text, via, [f"{base} {t}" for t in tails[:3]] + [base]))
        return {"items": _collect(seed, raw), "requests": len(raw), "failed": 0,
                "notes": ["Saran contoh: dibuat otomatis, bukan dari Google."], "source": self.name,
                "channel": self.channel}


def get_suggest(demo: bool = False, channel: str = "google"):
    return MockSuggest(channel) if demo else GoogleSuggest(channel=channel)
