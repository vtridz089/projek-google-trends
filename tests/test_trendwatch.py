import csv
import io
import urllib.error
from datetime import date, timedelta

import pytest

from app import cli, db
from app.collector import (
    DEMO_GEO,
    CollectorError,
    MockCollector,
    RssCollector,
    TrendItem,
    parse_rss,
    parse_traffic,
    target_geo,
)
from app.exporter import rows_to_csv
from app.timeutil import today_local

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

EMPTY_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>"""


# ---------------------------------------------------------------- helpers


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_opener(*outcomes):
    """Opener palsu: tiap panggilan mengembalikan/melempar hasil berikutnya
    (yang terakhir diulang)."""
    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    opener.calls = calls
    return opener


class FakeCollector:
    def __init__(self, items=None, error=None, fail_geos=()):
        self.items = items or []
        self.error = error
        self.fail_geos = set(fail_geos)

    def fetch(self, geo, day):
        if self.error or geo in self.fail_geos:
            raise self.error or CollectorError(f"{geo} gagal")
        return self.items


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("TRENDWATCH_CSV", str(tmp_path / "snapshots.csv"))
    monkeypatch.delenv("TRENDWATCH_PASSWORD", raising=False)
    db.init_db()
    return tmp_path


def ti(kw, n=0, **kw2):
    return TrendItem(keyword=kw, traffic_text=f"{n}+" if n else "", traffic_num=n, **kw2)


# ---------------------------------------------------------------- parsing


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


def test_parse_rss_rejects_malformed_xml():
    with pytest.raises(CollectorError):
        parse_rss(b"<rss><channel><item>")


def test_mock_is_deterministic():
    a = MockCollector().fetch("ID", date(2026, 11, 2))
    b = MockCollector().fetch("ID", date(2026, 11, 2))
    assert [i.keyword for i in a] == [i.keyword for i in b]
    assert len(a) == 10


def test_target_geo():
    assert target_geo("id", "rss") == "ID"
    assert target_geo("id", "mock") == DEMO_GEO


def test_today_local_survives_bad_timezone(monkeypatch):
    monkeypatch.setenv("TRENDWATCH_TZ", "Bukan/Zona")
    assert isinstance(today_local(), date)


# ---------------------------------------------------------------- RssCollector


def test_rss_retries_then_succeeds():
    opener = make_opener(urllib.error.URLError("putus"), urllib.error.URLError("putus"), SAMPLE_RSS)
    sleeps = []
    col = RssCollector(retries=3, backoff=1.0, opener=opener, sleep=sleeps.append)
    items = col.fetch("id", date(2026, 11, 2))
    assert len(items) == 2
    assert len(opener.calls) == 3 and "geo=ID" in opener.calls[0]
    assert sleeps == [1.0, 2.0]


def test_rss_gives_up_after_retries():
    opener = make_opener(urllib.error.URLError("putus"))
    col = RssCollector(retries=3, opener=opener, sleep=lambda s: None)
    with pytest.raises(CollectorError, match="3 percobaan"):
        col.fetch("ID", date(2026, 11, 2))
    assert len(opener.calls) == 3


def test_rss_does_not_retry_permanent_http_errors():
    err = urllib.error.HTTPError("http://x", 403, "Forbidden", {}, None)
    opener = make_opener(err)
    col = RssCollector(retries=3, opener=opener, sleep=lambda s: None)
    with pytest.raises(CollectorError, match="403"):
        col.fetch("ID", date(2026, 11, 2))
    assert len(opener.calls) == 1


def test_rss_retries_server_errors():
    err = urllib.error.HTTPError("http://x", 503, "Unavailable", {}, None)
    opener = make_opener(err, SAMPLE_RSS)
    col = RssCollector(retries=3, opener=opener, sleep=lambda s: None)
    assert len(col.fetch("ID", date(2026, 11, 2))) == 2


def test_rss_empty_feed_is_an_error():
    col = RssCollector(opener=make_opener(EMPTY_RSS), sleep=lambda s: None)
    with pytest.raises(CollectorError, match="tidak berisi tren"):
        col.fetch("ID", date(2026, 11, 2))


# ---------------------------------------------------------------- database


def test_overview_empty(tmp_db):
    assert db.overview("ID")["items"] == []


def test_overview_status(tmp_db):
    d1, d2, d3 = date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1)
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
    db.save_items("ID", date(2026, 11, 1), [ti("x", 1)])
    assert db.overview("ID")["items"][0]["status"] == "baseline"


def test_save_is_idempotent_per_day(tmp_db):
    day = date(2026, 11, 1)
    db.save_items("ID", day, [ti("x", 1000)])
    db.save_items("ID", day, [ti("X", 2000)])
    pts = db.history("ID", "x")
    assert len(pts) == 1 and pts[0]["traffic_num"] == 2000


# ---------------------------------------------------------------- CSV


def test_csv_roundtrip_excludes_demo(tmp_db, monkeypatch):
    path = tmp_db / "out.csv"
    tricky = ti("halo, dunia", 1000, news_title='Judul "kutip"\nbaris baru')
    db.save_items("ID", date(2026, 11, 1), [tricky])
    db.save_items(DEMO_GEO, date(2026, 11, 1), [ti("demo", 5)])

    assert db.dump_csv(str(path)) == 1  # data demo tidak ikut

    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_db / "lain.db"))
    db.init_db()
    assert db.import_csv(str(path)) == (1, 0)
    rows = db.export_rows()
    assert rows[0]["keyword"] == "halo, dunia"
    assert rows[0]["news_title"] == 'Judul "kutip"\nbaris baru'


def test_import_csv_skips_bad_rows_and_is_idempotent(tmp_db):
    path = tmp_db / "in.csv"
    path.write_text(
        ",".join(db.CSV_FIELDS) + "\n"
        "ID,2026-11-01,valid,1K+,1000,,,,\n"
        "ID,bukan-tanggal,rusak,1K+,1000,,,,\n"
        "ID,2026-11-01,,1K+,1000,,,,\n",
        encoding="utf-8",
    )
    assert db.import_csv(str(path)) == (1, 2)
    assert db.import_csv(str(path)) == (1, 2)
    assert len(db.export_rows()) == 1


def test_export_rows_days_window(tmp_db):
    for offset in range(5):
        db.save_items("ID", date(2026, 11, 5) - timedelta(days=offset), [ti("x", offset + 1)])
    assert len(db.export_rows(geo="ID", days=2)) == 2
    assert len(db.export_rows(geo="ID")) == 5


def test_rows_to_csv_neutralises_formulas():
    rows = [{"geo": "ID", "day": "2026-11-01", "keyword": "=HYPERLINK(\"x\")",
             "traffic_text": "", "traffic_num": 1, "pub_date": "", "news_title": "-2+3",
             "news_url": "", "news_source": ""}]
    parsed = list(csv.DictReader(io.StringIO(rows_to_csv(rows))))
    assert parsed[0]["keyword"].startswith("'=")
    assert parsed[0]["news_title"].startswith("'-")
    raw = list(csv.DictReader(io.StringIO(rows_to_csv(rows, sanitize=False))))
    assert raw[0]["keyword"].startswith("=HYPERLINK")


# ---------------------------------------------------------------- CLI


def test_cli_collect_writes_csv_and_is_idempotent(tmp_db, monkeypatch):
    path = tmp_db / "snap.csv"
    monkeypatch.setattr(cli, "get_collector", lambda source: FakeCollector([ti("a", 1000)]))
    assert cli.main(["collect", "--geo", "ID", "--csv", str(path)]) == 0
    assert cli.main(["collect", "--geo", "ID", "--csv", str(path)]) == 0
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2  # header + 1 baris


def test_cli_returns_1_on_failure(tmp_db, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_collector",
                        lambda source: FakeCollector(error=CollectorError("mati")))
    assert cli.main(["collect", "--geo", "ID", "--csv", str(tmp_db / "s.csv")]) == 1
    assert "mati" in capsys.readouterr().err
    assert not (tmp_db / "s.csv").exists()  # tidak ada CSV kosong yang ikut di-commit


def test_cli_partial_failure_keeps_successful_geos(tmp_db, monkeypatch):
    path = tmp_db / "snap.csv"
    monkeypatch.setattr(cli, "get_collector",
                        lambda source: FakeCollector([ti("a", 1)], fail_geos=["MY"]))
    assert cli.main(["collect", "--geo", "ID", "--geo", "MY", "--csv", str(path)]) == 1
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [(r["geo"], r["keyword"]) for r in rows] == [("ID", "a")]
    assert db.overview("ID")["total"] == 1


def test_cli_mock_goes_to_demo_geo_not_csv(tmp_db):
    path = tmp_db / "snap.csv"
    assert cli.main(["collect", "--source", "mock", "--csv", str(path)]) == 0
    assert db.overview(DEMO_GEO)["total"] == 10
    assert not path.exists()


def test_cli_unknown_source(tmp_db):
    assert cli.main(["collect", "--source", "bukan-sumber", "--no-csv"]) == 2


def test_cli_import_missing_file(tmp_db):
    assert cli.main(["import", "--csv", str(tmp_db / "tidak-ada.csv")]) == 1


# ---------------------------------------------------------------- API (perlu fastapi)


@pytest.fixture()
def client(tmp_db):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_api_flow(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/overview?geo=ID").json()["items"] == []

    r = client.post("/api/demo-seed?days=5")
    assert r.status_code == 200 and r.json() == {"geo": "XX", "days": 5, "saved": 50}

    ov = client.get("/api/overview?geo=XX").json()
    assert ov["total"] == 10 and ov["day"] == today_local().isoformat()
    assert ov["prev_day"] == (today_local() - timedelta(days=1)).isoformat()
    assert client.get("/api/overview?geo=ID").json()["items"] == []  # demo tidak bocor ke ID

    kw = ov["items"][0]["keyword"]
    hist = client.get("/api/history", params={"keyword": kw, "geo": "XX"}).json()
    assert hist["points"]

    top = client.get("/api/top?geo=XX&days=5").json()
    assert 1 <= len(top["items"]) <= 10

    assert client.get("/api/overview?geo=INVALID").status_code == 400
    assert client.post("/api/collect?source=bukan-sumber").status_code == 400
    assert client.post("/api/collect?source=mock").json() == {"geo": "XX", "source": "mock", "saved": 10}
    assert client.get("/").status_code == 200


def test_api_export_csv(client):
    client.post("/api/demo-seed?days=3")
    r = client.get("/api/export.csv?geo=XX&days=30")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    assert rows[0] == db.CSV_FIELDS and len(rows) == 1 + 30


def test_api_basic_auth(client, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_PASSWORD", "rahasia")
    assert client.get("/api/health").status_code == 200  # health check tetap terbuka
    r = client.get("/api/overview?geo=ID")
    assert r.status_code == 401
    assert client.get("/api/overview?geo=ID", auth=("admin", "salah")).status_code == 401
    assert client.get("/api/overview?geo=ID", auth=("admin", "rahasia")).status_code == 200
    assert client.get("/", auth=("admin", "rahasia")).status_code == 200


def test_api_collect_rate_limit_and_errors(client, monkeypatch):
    import app.main as m

    monkeypatch.setattr(m, "get_collector", lambda source: FakeCollector([ti("a", 1)]))
    monkeypatch.setattr(m, "_last_collect", {})
    monkeypatch.setattr(m, "COLLECT_MIN_INTERVAL", 300.0)
    assert client.post("/api/collect?source=rss&geo=ID").status_code == 200
    assert client.post("/api/collect?source=rss&geo=ID").status_code == 429
    assert client.post("/api/collect?source=rss&geo=MY").status_code == 200

    monkeypatch.setattr(m, "COLLECT_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(m, "get_collector",
                        lambda source: FakeCollector(error=CollectorError("mati")))
    r = client.post("/api/collect?source=rss&geo=SG")
    assert r.status_code == 502 and "mati" in r.json()["detail"]
