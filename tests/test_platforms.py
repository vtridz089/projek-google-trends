"""Tes platform, berita Google Berita, dan riwayat keyword (Dipakai/Tidak)."""
from pathlib import Path

import pytest

from app import db, history, news, platforms


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "p.db"))
    db.init_db()
    history.init()
    news.init()


# --------------------------------------------------------------------------- daftar platform


def test_fifteen_platforms_with_requested_news_terms():
    assert [(p.name, p.news) for p in platforms.PLATFORMS] == [
        ("JagoUKP", "UKP"), ("JadiASN", "ASN"), ("JadiBUMN", "BUMN"), ("JadiCerebrum", "Cerebrum"), ("JadiPPPK", "PPPK"),
        ("JadiPRAJURIT", "PRAJURIT"), ("JadiPOLISI", "POLISI"), ("JadiPCPM", "PCPM"), ("JagoTPA", "TPA"),
        ("JadiBEASISWA", "BEASISWA"), ("JadiSEKDIN", "SEKDIN"), ("Psikoteskerja", "Psikotes kerja"),
        ("ToeflAcademy", "TOEFL"), ("JadiOJK", "OJK"), ("JadiPPG", "PPG")]
    assert len({p.slug for p in platforms.PLATFORMS}) == 15 and all(p.slug == p.name.lower() for p in platforms.PLATFORMS)


def test_get_is_case_insensitive_and_rejects_unknown():
    assert platforms.get("JadiASN").name == "JadiASN" and platforms.get(" toeflacademy ").news == "TOEFL"
    assert platforms.get("tidak-ada") is None and platforms.get("") is None


# --------------------------------------------------------------------------- berita

FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Seleksi ASN 2027 dibuka - Kompas.com</title><link>https://news.google.com/rss/articles/a</link>
 <pubDate>Mon, 09 Nov 2026 10:00:00 GMT</pubDate><source url="https://kompas.com">Kompas.com</source></item>
<item><title>Jadwal ASN terbaru - Detik</title><link>https://news.google.com/rss/articles/b</link>
 <pubDate>Tue, 10 Nov 2026 02:30:00 GMT</pubDate><source url="https://detik.com">Detik</source></item>
<item><title>Seleksi ASN 2027 dibuka - Lain</title><link>https://news.google.com/rss/articles/c</link>
 <pubDate>Mon, 09 Nov 2026 11:00:00 GMT</pubDate><source url="https://lain.id">Lain</source></item>
<item><title>Link berbahaya</title><link>javascript:alert(1)</link><pubDate>Mon, 09 Nov 2026 12:00:00 GMT</pubDate></item>
<item><title></title><link>https://x.id</link></item>
</channel></rss>"""
EMPTY = b'<?xml version="1.0"?><rss><channel></channel></rss>'


def test_parse_feed_cleans_sorts_dedupes_and_blocks_bad_links():
    items = news.parse_feed(FEED)
    assert [i["title"] for i in items] == ["Jadwal ASN terbaru", "Seleksi ASN 2027 dibuka"]  # terbaru dulu; suffix sumber dibuang; duplikat dan javascript: hilang
    assert items[0]["source"] == "Detik" and items[0]["published"].startswith("2026-11-10T02:30")
    assert all(i["link"].startswith("https://") for i in items)


def test_parse_feed_rejects_entities_and_garbage():
    with pytest.raises(news.NewsError):
        news.parse_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>')
    with pytest.raises(news.NewsError):
        news.parse_feed(b"<html>bukan rss")


def test_feed_url_quotes_phrases_and_limits_to_recent():
    asn, psi = news.feed_url("ASN"), news.feed_url("Psikotes kerja")
    assert "q=ASN+when%3A7d" in asn and "ceid=ID%3Aid" in asn and "hl=id" in asn
    assert "q=%22Psikotes+kerja%22+when%3A7d" in psi
    assert "when" not in news.feed_url("ASN", recent=False)


def test_get_news_caches_then_refreshes_after_ttl(monkeypatch):
    calls = []
    fetcher = lambda url: (calls.append(url), FEED)[1]
    a = news.get_news("ASN", fetcher=fetcher, now=1000.0)
    assert not a["cached"] and len(a["items"]) == 2 and len(calls) == 1
    b = news.get_news("ASN", fetcher=fetcher, now=1000.0 + 60)
    assert b["cached"] and not b["stale"] and len(calls) == 1
    news.get_news("ASN", fetcher=fetcher, now=1000.0 + 60, refresh=True)
    assert len(calls) == 2
    news.get_news("ASN", fetcher=fetcher, now=1000.0 + 4 * 3600)  # lewat TTL 3 jam
    assert len(calls) == 3


def test_get_news_falls_back_to_old_cache_but_raises_without_one():
    news.get_news("OJK", fetcher=lambda url: FEED, now=0.0)

    def down(url):
        raise news.NewsError("jaringan mati")
    stale = news.get_news("OJK", fetcher=down, now=10 * 3600.0)
    assert stale["stale"] and stale["items"] and "jaringan mati" in stale["note"]
    with pytest.raises(news.NewsError):
        news.get_news("PPG", fetcher=down, now=0.0)


def test_get_news_widens_search_when_last_week_is_empty():
    urls = []

    def fetcher(url):
        urls.append(url)
        return EMPTY if "when" in url else FEED
    out = news.get_news("TPA", fetcher=fetcher, now=0.0)
    assert len(urls) == 2 and "when" not in urls[1] and len(out["items"]) == 2


def test_news_cache_is_per_term():
    news.get_news("ASN", fetcher=lambda u: FEED, now=0.0)
    other = news.get_news("BUMN", fetcher=lambda u: EMPTY, now=1.0)
    assert not other["cached"] and other["items"] == []


# --------------------------------------------------------------------------- riwayat keyword


def test_history_set_replace_clear_and_case_insensitive():
    a = history.set_status("jadiasn", "Jadwal CPNS 2027", "dipakai", seed="cpns", geo="id", user_id=1, user_name="Budi")
    assert a["status"] == "dipakai" and a["geo"] == "ID" and a["user"] == "Budi"
    b = history.set_status("jadiasn", "  jadwal   cpns 2027 ", "tidak", user_id=2, user_name="Sari")
    assert b["id"] == a["id"] and b["status"] == "tidak" and b["user"] == "Sari"  # satu baris, status diganti
    assert history.statuses("jadiasn") == {"jadwal cpns 2027": "tidak"}
    assert history.clear("jadiasn", "JADWAL CPNS 2027") is True and history.clear("jadiasn", "jadwal cpns 2027") is False
    assert history.statuses("jadiasn") == {}


def test_history_is_separate_per_platform():
    history.set_status("jadiasn", "cpns 2027", "dipakai")
    history.set_status("jadibumn", "cpns 2027", "tidak")
    assert history.statuses("jadiasn") == {"cpns 2027": "dipakai"} and history.statuses("jadibumn") == {"cpns 2027": "tidak"}
    history.clear("jadiasn", "cpns 2027")
    assert history.statuses("jadibumn") == {"cpns 2027": "tidak"}


def test_history_validation():
    for bad in ("", "   ", "x" * 201):
        with pytest.raises(ValueError):
            history.set_status("jadiasn", bad, "dipakai")
    with pytest.raises(ValueError):
        history.set_status("jadiasn", "cpns", "mungkin")
    with pytest.raises(ValueError):
        history.listing("jadiasn", status="mungkin")


def test_history_listing_filters_counts_and_escapes_like():
    for kw, st in [("jadwal cpns", "dipakai"), ("info cpns", "tidak"), ("diskon 50% asn", "dipakai"), ("a_b", "tidak")]:
        history.set_status("jadiasn", kw, st)
    history.set_status("jadibumn", "lain", "dipakai")
    out = history.listing("jadiasn")
    assert out["counts"] == {"dipakai": 2, "tidak": 2} and len(out["items"]) == 4
    assert [i["keyword"] for i in history.listing("jadiasn", status="tidak", q="CPNS")["items"]] == ["info cpns"]
    assert [i["keyword"] for i in history.listing("jadiasn", q="50%")["items"]] == ["diskon 50% asn"]
    assert [i["keyword"] for i in history.listing("jadiasn", q="a_b")["items"]] == ["a_b"]
    assert history.listing("jadiasn", q="%")["items"][0]["keyword"] == "diskon 50% asn" and len(history.listing("jadiasn", q="%")["items"]) == 1


# --------------------------------------------------------------------------- halaman: kolom yang dihapus


def test_dashboard_has_no_demand_score_or_stage_columns():
    html = (Path(__file__).resolve().parent.parent / "static" / "dashboard.html").read_text(encoding="utf-8")
    for gone in ("Permintaan", "Skor", "Tahap", "TOFU", "MOFU", "BOFU", "skor_bagian", "r.permintaan"):
        assert gone not in html, gone
    assert "Dipakai" in html and "Berita terbaru" in html and "Pindah platform" in html


# --------------------------------------------------------------------------- API


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c


def test_api_pages_and_platform_list(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/platform"
    assert client.get("/platform").status_code == 200
    items = client.get("/api/platforms").json()["items"]
    assert len(items) == 15 and items[1] == {"slug": "jadiasn", "name": "JadiASN", "news": "ASN"}
    assert "pswitch" in client.get("/p/jadiasn").text
    gone = client.get("/p/tidak-ada", follow_redirects=False)
    assert gone.status_code == 303 and gone.headers["location"] == "/platform"


def test_api_news_uses_platform_term_and_reports_errors(client, monkeypatch):
    urls = []
    monkeypatch.setattr(news, "_fetch", lambda url: (urls.append(url), FEED)[1])
    r = client.get("/api/platforms/psikoteskerja/berita")
    assert r.status_code == 200 and r.json()["term"] == "Psikotes kerja" and len(r.json()["items"]) == 2
    assert "%22Psikotes+kerja%22" in urls[0]
    assert client.get("/api/platforms/xx/berita").status_code == 404

    def down(url):
        raise news.NewsError("mati")
    monkeypatch.setattr(news, "_fetch", down)
    assert client.get("/api/platforms/jadiojk/berita").status_code == 502


def test_api_history_flow(client):
    base = "/api/platforms/jadiasn/riwayat"
    put = client.put(base, json={"keyword": "jadwal cpns 2027", "status": "dipakai", "seed": "cpns", "geo": "ID"})
    assert put.status_code == 200 and put.json()["item"]["status"] == "dipakai"
    assert client.put(base, json={"keyword": "info cpns", "status": "tidak"}).status_code == 200
    assert client.put(base, json={"keyword": "x", "status": "mungkin"}).status_code == 400
    assert client.put(base, json={"keyword": "", "status": "tidak"}).status_code == 400
    assert client.put("/api/platforms/xx/riwayat", json={"keyword": "x", "status": "tidak"}).status_code == 404
    lst = client.get(base).json()
    assert lst["counts"] == {"dipakai": 1, "tidak": 1} and len(lst["items"]) == 2
    assert client.get(base, params={"status": "tidak"}).json()["items"][0]["keyword"] == "info cpns"
    assert client.get(base + "/status").json()["items"] == {"jadwal cpns 2027": "dipakai", "info cpns": "tidak"}
    assert client.get("/api/platforms/jadibumn/riwayat").json()["items"] == []
    assert client.delete(base, params={"keyword": "Info CPNS"}).json()["removed"] is True
    assert client.delete(base, params={"keyword": "info cpns"}).json()["removed"] is False
    assert client.get(base, params={"status": "salah"}).status_code == 400
