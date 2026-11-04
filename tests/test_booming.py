"""Tes fitur Booming (top 10 pencarian yang sedang naik untuk satu topik)."""
import json
import urllib.error
from datetime import date

import pytest

from app import db, explore, explore_service, insight
from app.collector import TrendItem
from app.explore import ExploreError, GoogleDirectProvider, MockExploreProvider, SerpApiProvider
from app.explore_service import normalize_booming, rank_booming, run_booming

# --------------------------------------------------------------------------- rank_booming


def R(query, value, label=""):
    return {"query": query, "value": value, "label": label or str(value)}


def test_breakout_first_in_google_order_then_by_growth():
    rel = {"rising": [R("c", 120, "+120%"), R("b1", 5000, "Breakout"), R("a", 900, "+900%"),
                      R("b2", 5000, "Breakout")],
           "top": []}
    got = rank_booming(rel, "topik")
    assert [i["query"] for i in got] == ["b1", "b2", "a", "c"]
    assert [i["rank"] for i in got] == [1, 2, 3, 4]
    assert got[0]["breakout"] and got[0]["label"] == "Breakout"
    assert got[2]["label"] == "+900%" and not got[2]["breakout"]


def test_value_over_threshold_counts_as_breakout_even_without_label():
    got = rank_booming({"rising": [R("x", 7000, "+7000%")], "top": []}, "t")
    assert got[0]["breakout"] and got[0]["label"] == "Breakout"


def test_fills_with_popular_when_few_rising():
    rel = {"rising": [R("naik1", 300, "+300%")], "top": [R("pop1", 100), R("pop2", 80)]}
    got = rank_booming(rel, "t", limit=10)
    assert [(i["query"], i["kind"]) for i in got] == [("naik1", "naik"), ("pop1", "populer"), ("pop2", "populer")]
    assert got[1]["label"] == "100"


def test_dedupes_and_drops_the_topic_itself():
    rel = {"rising": [R("Telkom", 500, "+500%"), R("bumn", 400, "+400%"), R("TELKOM", 300, "+300%")],
           "top": [R("telkom", 100), R("pertamina", 90)]}
    got = rank_booming(rel, "BUMN")
    assert [i["query"] for i in got] == ["Telkom", "pertamina"]


def test_limit_and_empty():
    rel = {"rising": [R(f"q{i}", 1000 - i, f"+{1000 - i}%") for i in range(30)], "top": []}
    assert len(rank_booming(rel, "t", limit=10)) == 10
    assert len(rank_booming(rel, "t", limit=3)) == 3
    assert rank_booming({}, "t") == []
    assert rank_booming({"rising": [R("", 5)], "top": None}, "t") == []


# --------------------------------------------------------------------------- normalize_booming


def test_normalize_booming_ok():
    p = normalize_booming("  BUMN ", "id", "19", "now 7-d", "", "10")
    assert p == {"kind": "booming", "topic": "BUMN", "keywords": ["BUMN"], "geo": "ID", "category": 19,
                 "time": "now 7-d", "gprop": "", "parts": ["related"], "limit": 10}


@pytest.mark.parametrize("kw, msg", [
    (dict(topic=""), "minimal satu"),
    (dict(topic="bumn, telkom"), "satu topik"),
    (dict(topic="a\nb"), "satu topik"),
    (dict(topic="x" * 101), "terlalu panjang"),
    (dict(topic="bumn", geo="Indonesia"), "wilayah"),
    (dict(topic="bumn", time_range="kemarin"), "Rentang waktu"),
    (dict(topic="bumn", limit=0), "antara 1 dan 20"),
    (dict(topic="bumn", limit=21), "antara 1 dan 20"),
    (dict(topic="bumn", limit="abc"), "angka"),
])
def test_normalize_booming_rejects(kw, msg):
    base = dict(topic="bumn", geo="ID", category=0, time_range="now 7-d", gprop="", limit=10)
    base.update(kw)
    with pytest.raises(ValueError, match=msg):
        normalize_booming(**base)


# --------------------------------------------------------------------------- run_booming


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "b.db"))
    monkeypatch.delenv("TRENDWATCH_BOOMING_TTL_MINUTES", raising=False)
    db.init_db()


class RelProvider:
    name = "fake"

    def __init__(self, rel=None, fail=False, key="BUMN"):
        self.rel, self.fail, self.key, self.calls, self.last_parts = rel, fail, key, 0, None

    def explore(self, keywords, geo, category, time_range, gprop="", parts=explore.PARTS):
        self.calls += 1
        self.last_parts = tuple(parts)
        if self.fail:
            raise ExploreError("diblokir", hint="coba SerpApi")
        related = {self.key: self.rel} if self.rel is not None else {}
        return {"keywords": list(keywords), "source": "fake", "fetched_at": "2026-11-03T00:00:00+00:00",
                "related": related, "notes": ["catatan sumber"]}


GOOD = {"rising": [R("telkom", 5000, "Breakout"), R("garuda", 300, "+300%")], "top": [R("danantara", 100)]}


def booming_params(**kw):
    base = dict(topic="BUMN", geo="ID", category=0, time_range="now 7-d", gprop="", limit=10)
    base.update(kw)
    return normalize_booming(**base)


def test_run_booming_builds_ranked_result_and_only_requests_related(tmp_db):
    prov = RelProvider(GOOD)
    out = run_booming(booming_params(), provider=prov, now=1000)
    assert prov.last_parts == ("related",)  # tidak membuang kuota untuk grafik
    assert [i["query"] for i in out["items"]] == ["telkom", "garuda", "danantara"]
    assert out["rising_count"] == 2 and out["topic"] == "BUMN" and out["category_name"] == "Semua kategori"
    assert out["cached"] is False
    assert any("diisi pencarian terpopuler" in n for n in out["notes"])
    assert "catatan sumber" in out["notes"]


def test_run_booming_note_when_nothing_is_rising(tmp_db):
    out = run_booming(booming_params(), provider=RelProvider({"rising": [], "top": [R("x", 100)]}), now=1)
    assert out["rising_count"] == 0 and any("paling sering dicari" in n for n in out["notes"])


def test_run_booming_matches_topic_key_case_insensitively_or_falls_back(tmp_db):
    out = run_booming(booming_params(), provider=RelProvider(GOOD, key="bumn"), now=1)  # kunci beda huruf
    assert out["items"]


def test_run_booming_cache_ttl_and_refresh(tmp_db):
    prov, params = RelProvider(GOOD), booming_params()
    run_booming(params, provider=prov, now=1000)
    r2 = run_booming(params, provider=prov, now=1000 + 30 * 60)
    assert prov.calls == 1 and r2["cached"] and r2["cache_age_seconds"] == 1800
    run_booming(params, provider=prov, now=1000 + 61 * 60)  # lewat TTL 1 jam
    assert prov.calls == 2
    run_booming(params, provider=prov, now=1000 + 62 * 60, refresh=True)
    assert prov.calls == 3


def test_run_booming_ttl_is_configurable(tmp_db, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_BOOMING_TTL_MINUTES", "5")
    prov, params = RelProvider(GOOD), booming_params()
    run_booming(params, provider=prov, now=0)
    run_booming(params, provider=prov, now=6 * 60)
    assert prov.calls == 2


def test_run_booming_errors(tmp_db):
    with pytest.raises(ExploreError, match="diblokir"):
        run_booming(booming_params(), provider=RelProvider(fail=True), now=1)
    with pytest.raises(ExploreError, match="daftar pencarian terkait"):
        run_booming(booming_params(), provider=RelProvider(None), now=1)
    with pytest.raises(ExploreError, match="cukup besar"):
        run_booming(booming_params(), provider=RelProvider({"rising": [], "top": []}), now=1)


def test_run_booming_serves_stale_cache_on_failure(tmp_db):
    params = booming_params()
    run_booming(params, provider=RelProvider(GOOD), now=0)
    out = run_booming(params, provider=RelProvider(fail=True), now=3 * 3600)
    assert out["stale"] and out["items"] and "diblokir" in out["notes"][-1]


def test_run_booming_demo_uses_mock_and_skips_cache(tmp_db):
    out = run_booming(booming_params(), demo=True, now=1)
    assert out["source"] == "mock" and len(out["items"]) == 10
    assert out["items"][0]["breakout"] and out["rising_count"] == 6
    assert db.recent_searches(kind="booming") == []


def test_booming_and_explore_recents_are_separate(tmp_db):
    run_booming(booming_params(topic="bumn"), provider=RelProvider(GOOD, key="bumn"), now=1)
    explore_service.run(explore_service.normalize_params(["beasiswa"], "ID"),
                        provider=MockExploreProvider(), now=2)
    assert [p["topic"] for p in db.recent_searches(kind="booming")] == ["bumn"]
    assert [p["keywords"] for p in db.recent_searches()] == [["beasiswa"]]


# --------------------------------------------------------------------------- tren harian terkait


def seed_today(geo="ID", day=date(2026, 11, 2)):
    db.save_items(geo, day, [
        TrendItem("danantara", "200K+", 200_000, news_title="BUMN baru dibentuk", news_url="https://x.id/1", news_source="X"),
        TrendItem("timnas", "500K+", 500_000, news_title="Laga malam ini"),
        TrendItem("Erick Thohir", "50K+", 50_000, news_title="Menteri BUMN bicara"),
        TrendItem("diskon 50%_", "10K+", 10_000, news_title="promo"),
    ])


def test_today_matches_by_keyword_or_headline(tmp_db):
    seed_today()
    t = db.today_matches("ID", "bumn")
    assert t["day"] == "2026-11-02"
    assert [i["keyword"] for i in t["items"]] == ["danantara", "Erick Thohir"]  # urut traffic


def test_today_matches_escapes_like_wildcards(tmp_db):
    seed_today()
    # Bila '%' / '_' dianggap wildcard, keempat baris akan cocok. Seharusnya hanya yang memuat karakternya.
    assert [i["keyword"] for i in db.today_matches("ID", "%")["items"]] == ["diskon 50%_"]
    assert [i["keyword"] for i in db.today_matches("ID", "_")["items"]] == ["diskon 50%_"]
    assert db.today_matches("ID", "tidak ada")["items"] == []


def test_today_matches_empty_cases(tmp_db):
    assert db.today_matches("ID", "bumn") == {"day": None, "items": []}  # belum ada data harian
    assert db.today_matches("", "bumn") == {"day": None, "items": []}    # seluruh dunia: tidak ada RSS
    seed_today()
    assert db.today_matches("MY", "bumn")["items"] == []


def test_run_booming_attaches_fresh_today_even_when_cached(tmp_db):
    prov, params = RelProvider(GOOD), booming_params()
    assert run_booming(params, provider=prov, now=1)["today"]["items"] == []
    seed_today()
    again = run_booming(params, provider=prov, now=2)
    assert again["cached"] and len(again["today"]["items"]) == 2


# --------------------------------------------------------------------------- penyedia tanpa grafik

EXPLORE_RESP = ")]}'\n" + json.dumps({"widgets": [
    {"id": "TIMESERIES", "token": "T", "request": {}},
    {"id": "RELATED_QUERIES", "token": "TOK_RQ", "request": {"restriction": {
        "complexKeywordsRestriction": {"keyword": [{"value": "BUMN"}]}}}}]})
RELATED_RESP = ")]}'\n" + json.dumps({"default": {"rankedList": [
    {"rankedKeyword": [{"query": "telkom", "value": 100, "formattedValue": "100"}]},
    {"rankedKeyword": [{"query": "danantara", "value": 5000, "formattedValue": "Breakout"}]}]}})


class Resp:
    def __init__(self, b):
        self.b = b.encode() if isinstance(b, str) else b

    def read(self):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_google_provider_skips_multiline_when_timeline_not_requested():
    calls = []

    class Op:
        def open(self, req, timeout=None):
            calls.append(req.full_url)
            return Resp(EXPLORE_RESP if "/api/explore" in req.full_url else RELATED_RESP)

    p = GoogleDirectProvider(opener=Op(), min_interval=0, sleep=lambda s: None)
    r = p.explore(["BUMN"], "ID", 0, "now 7-d", "", ("related",))
    assert r["timeline"] == [] and r["related"]["BUMN"]["rising"][0]["label"] == "Breakout"
    assert not any("multiline" in c for c in calls) and len(calls) == 2  # explore + relatedsearches


def test_google_provider_empty_explore_without_timeline_is_error():
    class Op:
        def open(self, req, timeout=None):
            return Resp(")]}'\n{\"widgets\": []}")

    p = GoogleDirectProvider(opener=Op(), min_interval=0, sleep=lambda s: None)
    with pytest.raises(ExploreError, match="kosong"):
        p.explore(["x"], "ID", 0, "now 7-d", "", ("related",))


def test_serpapi_skips_timeseries_when_not_requested():
    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        return Resp(json.dumps({"related_queries": {"rising": [
            {"query": "danantara", "value": "Breakout", "extracted_value": 5000}], "top": []}}))

    r = SerpApiProvider("K", opener=opener).explore(["BUMN"], "ID", 0, "now 7-d", "", ("related",))
    assert len(calls) == 1 and "RELATED_QUERIES" in calls[0] and "TIMESERIES" not in calls[0]
    assert r["timeline"] == [] and r["related"]["BUMN"]["rising"][0]["label"] == "Breakout"


def test_mock_without_timeline_part():
    r = MockExploreProvider().explore(["bumn"], "ID", 0, "now 7-d", "", ("related",))
    assert r["timeline"] == [] and len(r["related"]["bumn"]["rising"]) == 6 and r["regions"] == []


# --------------------------------------------------------------------------- API (perlu fastapi)


@pytest.fixture()
def client(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.delenv("TRENDWATCH_PASSWORD", raising=False)
    with TestClient(app) as c:
        yield c


def test_api_booming_demo(client):
    r = client.get("/api/booming", params={"topic": "BUMN", "demo": "true"})
    assert r.status_code == 200
    d = r.json()
    # Bawaan API: hanya yang benar-benar naik (data contoh punya 6 yang naik), tanpa isian populer.
    assert d["topic"] == "BUMN" and d["source"] == "mock" and len(d["items"]) == 6
    assert all(i["kind"] == "naik" and 0 <= i["peluang"]["skor"] <= 100 for i in d["items"])
    assert d["items"][0]["rank"] == 1 and "today" in d
    full = client.get("/api/booming", params={"topic": "BUMN", "demo": "true", "rising_only": "false"}).json()
    assert len(full["items"]) == 10                 # mode lama tetap tersedia: diisi populer


def test_api_booming_validation_and_errors(client, monkeypatch):
    assert client.get("/api/booming").status_code == 400
    assert client.get("/api/booming", params={"topic": "a, b"}).status_code == 400
    assert client.get("/api/booming", params={"topic": "a", "limit": 99}).status_code == 400

    class Boom:
        name = "boom"

        def explore(self, *a, **k):
            raise ExploreError("diblokir", hint="coba SerpApi")

    monkeypatch.setattr(explore_service, "get_provider", lambda demo=False: Boom())
    r = client.get("/api/booming", params={"topic": "bumn"})
    assert r.status_code == 502 and "diblokir" in r.json()["detail"] and "coba SerpApi" in r.json()["detail"]


def test_api_pages_and_recent(client):
    for path in ("/", "/hari-ini", "/explore"):
        assert client.get(path).status_code == 200
    assert 'Lagi booming apa?' in client.get("/").text
    assert client.get("/api/booming/recent").json() == {"items": []}


# --------------------------------------------------------------------------- hanya yang naik (rising_only)


def rising_params(**kw):
    return booming_params(rising_only=True, **kw)


def test_rising_only_hides_popular_and_adds_percent_and_opportunity(tmp_db):
    out = run_booming(rising_params(), provider=RelProvider(GOOD), now=1000)
    assert [i["query"] for i in out["items"]] == ["telkom", "garuda"] and out["rising_only"] is True
    assert out["popular_count"] == 1                      # ada 1 yang populer, tapi tidak ditampilkan
    tel, gar = out["items"]
    assert tel["label"] == "Breakout" and gar["label"] == "+300%"
    assert tel["peluang"]["skor"] > gar["peluang"]["skor"] > 0
    assert any("Hanya 2 pencarian yang tercatat naik" in n for n in out["notes"])
    assert not any("populer" in n.lower() and "diisi" in n for n in out["notes"])


def test_rising_only_with_nothing_rising_is_empty_not_an_error(tmp_db):
    out = run_booming(rising_params(), provider=RelProvider({"rising": [], "top": [R("x", 100), R("y", 90)]}), now=1)
    assert out["items"] == [] and out["rising_count"] == 0 and out["popular_count"] == 2
    # tanpa rising_only perilakunya tetap seperti dulu: terisi populer
    full = run_booming(booming_params(), provider=RelProvider({"rising": [], "top": [R("x", 100)]}), now=1)
    assert [i["kind"] for i in full["items"]] == ["populer"]


def test_rising_only_is_a_separate_cache_entry_and_old_cache_keys_are_unchanged(tmp_db):
    prov = RelProvider(GOOD)
    run_booming(booming_params(), provider=prov, now=1000)
    assert "rising_only" not in booming_params()          # kunci cache lama tidak berubah
    out = run_booming(rising_params(), provider=prov, now=1001)
    assert prov.calls == 2 and out["cached"] is False      # entri baru, bukan hasil campuran
    assert run_booming(rising_params(), provider=prov, now=1002)["cached"] is True


def test_sub_lookups_do_not_flood_recent_searches(tmp_db):
    prov = RelProvider(GOOD)
    run_booming(rising_params(), provider=prov, now=1000)
    run_booming(normalize_booming("telkom", "ID", 0, "now 7-d", "", 10, rising_only=True, sub=True),
                provider=RelProvider(GOOD, key="telkom"), now=1001)
    assert [p["topic"] for p in db.recent_searches(8, kind="booming")] == ["BUMN"]


def test_one_period_opportunity_short_window_gets_early_signal_and_matches_formula():
    item = {"query": "bumn 2026", "value": 300, "breakout": False}
    short = insight.opportunity_one_period(item, "bumn", "now 7-d")
    long_ = insight.opportunity_one_period(item, "bumn", "today 3-m")
    assert short["bagian"]["dini"] == 15 and long_["bagian"]["dini"] == 0
    assert short["bagian"]["relevan"] == 10 and short["bagian"]["konsistensi"] == 6
    assert insight.opportunity_one_period({"query": "danantara", "value": 300, "breakout": False},
                                          "bumn", "now 7-d")["bagian"]["relevan"] == 0
    assert 0 <= insight.opportunity_one_period({"query": "x", "value": 9999, "breakout": True}, "t", "now 1-d")["skor"] <= 100
