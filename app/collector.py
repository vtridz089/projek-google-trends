"""Lapisan pengambil data (collector).

Semua sumber data mengikuti antarmuka yang sama: ``fetch(geo, day) -> list[TrendItem]``.
Dashboard tidak peduli datanya dari mana, jadi kalau sumber berubah (RSS rusak,
pindah ke API berbayar, atau API resmi Google sudah terbuka), cukup tambah
kelas baru di sini.
"""
from __future__ import annotations

import random
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

# Namespace XML yang dipakai feed "Daily Search Trends" Google Trends.
HT_NS = {"ht": "https://trends.google.com/trending/rss"}
RSS_URL = "https://trends.google.com/trending/rss?geo={geo}"


@dataclass
class TrendItem:
    keyword: str
    traffic_text: str = ""      # contoh: "50K+" (apa adanya dari Google)
    traffic_num: int = 0        # perkiraan angka dari traffic_text
    pub_date: str = ""          # ISO 8601 jika bisa diparse
    news_title: str = ""
    news_url: str = ""
    news_source: str = ""


class Collector(Protocol):
    def fetch(self, geo: str, day: date) -> list[TrendItem]: ...


def parse_traffic(text: str | None) -> int:
    """'50K+' -> 50000, '2M+' -> 2000000, '500+' -> 500, tidak dikenal -> 0."""
    if not text:
        return 0
    m = re.match(r"\s*([\d.,]+)\s*([KkMmBb]?)", text)
    if not m:
        return 0
    number = m.group(1).replace(",", "")
    try:
        value = float(number)
    except ValueError:
        return 0
    factor = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2).lower()]
    return int(value * factor)


def _text(node: ET.Element, path: str) -> str:
    found = node.find(path, HT_NS)
    return (found.text or "").strip() if found is not None else ""


def parse_rss(xml_bytes: bytes) -> list[TrendItem]:
    """Ubah XML feed Google Trends menjadi daftar TrendItem."""
    root = ET.fromstring(xml_bytes)
    items: list[TrendItem] = []
    for item in root.iter("item"):
        keyword = (item.findtext("title") or "").strip()
        if not keyword:
            continue
        traffic_text = _text(item, "ht:approx_traffic")

        pub_iso = ""
        pub_raw = (item.findtext("pubDate") or "").strip()
        if pub_raw:
            try:
                pub_iso = parsedate_to_datetime(pub_raw).isoformat()
            except (TypeError, ValueError):
                pub_iso = pub_raw

        news = item.find("ht:news_item", HT_NS)
        items.append(
            TrendItem(
                keyword=keyword,
                traffic_text=traffic_text,
                traffic_num=parse_traffic(traffic_text),
                pub_date=pub_iso,
                news_title=_text(news, "ht:news_item_title") if news is not None else "",
                news_url=_text(news, "ht:news_item_url") if news is not None else "",
                news_source=_text(news, "ht:news_item_source") if news is not None else "",
            )
        )
    return items


class RssCollector:
    """Sumber nyata: feed RSS publik 'Daily Search Trends' Google Trends.

    Catatan: feed ini hanya memuat sekitar 10-20 tren teratas per negara,
    dan hanya menampilkan tren saat ini (bukan riwayat). Riwayat dibangun
    sendiri dengan menjalankan collector secara berkala.
    """

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def fetch(self, geo: str, day: date) -> list[TrendItem]:
        url = RSS_URL.format(geo=geo.upper())
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; TrendWatch-PKL/1.0)",
                "Accept": "application/rss+xml, application/xml, text/xml",
                "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return parse_rss(resp.read())


_MOCK_POOL = [
    "timnas indonesia", "harga emas hari ini", "jadwal liga 1", "cuaca bandung",
    "hasil pertandingan semalam", "bmkg gempa terkini", "harga bbm", "pengumuman cpns",
    "kurs dollar hari ini", "film bioskop terbaru", "jadwal kereta api", "ujian nasional",
    "ramalan zodiak", "resep kue", "update iphone terbaru", "beasiswa lpdp",
    "lowongan kerja bandung", "harga tiket konser", "banjir jakarta", "promo akhir tahun",
    "hasil undian", "jadwal imsakiyah", "penerimaan mahasiswa baru", "berita viral hari ini",
    "tutorial python",
]
_MOCK_TRAFFIC = ["200+", "500+", "1K+", "2K+", "5K+", "10K+", "20K+", "50K+", "100K+", "200K+"]


class MockCollector:
    """Data contoh untuk pengembangan tanpa internet.

    Deterministik per (geo, tanggal) supaya hasilnya bisa diuji, dan
    kata kuncinya sengaja saling tumpang-tindih antarhari agar status
    'baru', 'naik', 'turun' muncul di dashboard.
    """

    def fetch(self, geo: str, day: date) -> list[TrendItem]:
        rng = random.Random(f"{geo}-{day.isoformat()}")
        picks = rng.sample(_MOCK_POOL, 10)
        items = []
        for kw in picks:
            traffic = rng.choice(_MOCK_TRAFFIC)
            items.append(
                TrendItem(
                    keyword=kw,
                    traffic_text=traffic,
                    traffic_num=parse_traffic(traffic),
                    pub_date=datetime(day.year, day.month, day.day, 8, 0).isoformat(),
                    news_title=f"Contoh berita tentang {kw}",
                    news_url="",
                    news_source="Data contoh",
                )
            )
        return items


def get_collector(source: str) -> Collector:
    if source == "rss":
        return RssCollector()
    if source == "mock":
        return MockCollector()
    raise ValueError(f"Sumber data tidak dikenal: {source!r} (pilih 'rss' atau 'mock')")
