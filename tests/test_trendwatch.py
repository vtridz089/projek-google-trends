from datetime import date, timedelta

import pytest

from app import db
from app.collector import MockCollector, TrendItem, parse_rss, parse_traffic

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
  <channel>
    <title>Daily Search Trends</title>
    <item>
      <title>contoh keyword satu</title>
      <ht:approx_traffic>50K+</ht:approx_traffic>
      <pubDate>Mon, 02 Nov 2026 08:00:00 +0700</pubDate>
      <ht:news_item>
        <ht:news_item_title>Judul berita</ht:news_item_title>
        <ht:news_item_url>https://example.com/berita</ht:news_item_url>
        <ht:news_item_source>Contoh Media</ht:news_item_source>
      </ht:news_item>
    </item>
    <item>
      <title>tanpa berita</title>
      <ht:approx_traffic>2M+</ht:approx_traffic>
    </item>
  </channel>
</rss>"""


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "test.db"))
    db.init_db()
    return tmp_path


def test_parse_traffic():
    assert parse_traffic("50K+") == 50_000
    assert parse_traffic("2M+") == 2_000_000
    assert parse_traffic("500+") == 500
    assert parse_traffic("1,000+") == 1_000
    assert parse_traffic("") == 0
    assert parse_traffic(None) == 0
    assert parse_traffic("abc") == 0


def test_parse_rss():
    items = parse_rss(SAMPLE_RSS)
    assert [i.keyword for i in items] == ["contoh keyword satu", "tanpa berita"]
    first = items[0]
    assert first.traffic_num == 50_000
    assert first.news_title == "Judul berita"
    assert first.news_url == "https://example.com/berita"
    assert first.pub_date.startswith("2026-11-02T08:00:00")
    assert items[1].traffic_num == 2_000_000
    assert items[1].news_title == ""


def test_mock_is_deterministic():
    a = MockCollector().fetch("ID", date(2026, 11, 2))
    b = MockCollector().fetch("ID", date(2026, 11, 2))
    assert [i.keyword for i in a] == [i.keyword for i in b]
    assert len(a) == 10


def test_overview_empty(tmp_db):
    assert db.overview("ID")["items"] == []


def test_overview_status(tmp_db):
    d1, d2, d3 = date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1)

    def ti(kw, n):
        return TrendItem(keyword=kw, traffic_text=f"{n}+", traffic_num=n)

    db.save_items("ID", d1, [ti("lama", 1000), ti("pernah", 500)])
    db.save_items("ID", d2, [ti("lama", 1000)])
    db.save_items("ID", d3, [ti("lama", 5000), ti("pernah", 500), ti("Baru", 200)])

    res = db.overview("ID")
    by_kw = {i["keyword"].lower(): i for i in res["items"]}
    assert res["day"] == d3.isoformat()
    assert by_kw["lama"]["status"] == "naik"
    assert by_kw["lama"]["change_pct"] == 400
    assert by_kw["pernah"]["status"] == "muncul lagi"
    assert by_kw["baru"]["status"] == "baru"


def test_overview_baseline_when_single_day(tmp_db):
    db.save_items("ID", date(2026, 11, 1), [TrendItem(keyword="x", traffic_num=1)])
    assert db.overview("ID")["items"][0]["status"] == "baseline"


def test_save_is_idempotent_per_day(tmp_db):
    day = date(2026, 11, 1)
    db.save_items("ID", day, [TrendItem(keyword="x", traffic_text="1K+", traffic_num=1000)])
    db.save_items("ID", day, [TrendItem(keyword="X", traffic_text="2K+", traffic_num=2000)])
    pts = db.history("ID", "x")
    assert len(pts) == 1 and pts[0]["traffic_num"] == 2000


def test_api_flow(tmp_db):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/overview?geo=ID").json()["items"] == []

        r = client.post("/api/demo-seed?geo=ID&days=5")
        assert r.status_code == 200 and r.json()["saved"] == 50

        ov = client.get("/api/overview?geo=ID").json()
        assert ov["total"] == 10 and ov["day"] == date.today().isoformat()
        assert ov["prev_day"] == (date.today() - timedelta(days=1)).isoformat()

        kw = ov["items"][0]["keyword"]
        hist = client.get("/api/history", params={"keyword": kw, "geo": "ID"}).json()
        assert hist["points"]

        top = client.get("/api/top?geo=ID&days=5").json()
        assert 1 <= len(top["items"]) <= 10

        assert client.get("/api/overview?geo=INVALID").status_code == 400
        assert client.post("/api/collect?source=bukan-sumber").status_code == 400
        assert client.post("/api/collect?source=mock").json()["saved"] == 10
        assert client.get("/").status_code == 200
