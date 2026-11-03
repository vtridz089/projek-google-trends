"""Tes fitur Jelajah. Format respons Google/SerpApi di bawah disusun dari dokumentasi dan
contoh publik (bukan rekaman langsung), jadi tes ini membuktikan parser konsisten dengan
format yang dimaksud, bukan bahwa Google masih memakai format itu hari ini."""
import io
import json
import urllib.error

import pytest

from app import db, explore, explore_service
from app.explore import (
    ChainProvider,
    ExploreError,
    GoogleDirectProvider,
    MockExploreProvider,
    SerpApiProvider,
    build_result,
    parse_google_json,
    parse_timeline_google,
    summarize,
)

PREFIX = ")]}'\n"


def g(obj) -> str:
    return PREFIX + json.dumps(obj)


EXPLORE_RESP = g({"widgets": [
    {"id": "TIMESERIES", "token": "TOK_TS", "request": {"time": "today 12-m"}},
    {"id": "GEO_MAP", "token": "TOK_GEO", "request": {"resolution": "COUNTRY"}},
    {"id": "RELATED_TOPICS", "token": "TOK_RT", "request": {}},
    {"id": "RELATED_QUERIES", "token": "TOK_RQ", "request": {"restriction": {
        "complexKeywordsRestriction": {"keyword": [{"type": "BROAD", "value": "Beasiswa"}]}}}},
]})

TIMELINE_RESP = g({"default": {"timelineData": [
    {"time": "1730000000", "formattedAxisTime": "27 Okt 2024", "value": [40, 10], "hasData": [True, True]},
    {"time": "1730604800", "formattedAxisTime": "3 Nov 2024", "value": [100, 20], "hasData": [True, True]},
    {"time": "1731209600", "formattedAxisTime": "10 Nov 2024", "value": [60, 0], "hasData": [True, False],
     "isPartial": True},
]}})

RELATED_RESP = g({"default": {"rankedList": [
    {"rankedKeyword": [{"query": "beasiswa lpdp", "value": 100, "formattedValue": "100"},
                       {"query": "beasiswa s2", "value": 60, "formattedValue": "60"}]},
    {"rankedKeyword": [{"query": "beasiswa 2026", "value": 5000, "formattedValue": "Breakout"},
                       {"query": "beasiswa unggulan", "value": 150, "formattedValue": "+150%"}]},
]}})

GEO_RESP = g({"default": {"geoMapData": [
    {"geoCode": "ID-JK", "geoName": "DKI Jakarta", "value": [80, 10], "hasData": [True, True]},
    {"geoCode": "ID-JB", "geoName": "Jawa Barat", "value": [100, 40], "hasData": [True, True]},
    {"geoCode": "ID-AC", "geoName": "Aceh", "value": [0, 0], "hasData": [False, False]},
]}})


class FakeResponse:
    def __init__(self, body):
        self.body = body.encode() if isinstance(body, str) else body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """routes: {potongan-url: str | Exception | [urutan hasil]}"""

    def __init__(self, routes):
        self.routes = {k: (list(v) if isinstance(v, list) else [v]) for k, v in routes.items()}
        self.calls = []

    def open(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        for frag, queue in self.routes.items():
            if frag in url:
                out = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(out, Exception):
                    raise out
                return FakeResponse(out)
        raise AssertionError(f"URL tak terduga: {url}")


def google(routes=None, **kw):
    routes = routes or {"/api/explore": EXPLORE_RESP, "/multiline": TIMELINE_RESP,
                        "/relatedsearches": RELATED_RESP, "/comparedgeo": GEO_RESP}
    opener = FakeOpener(routes)
    kw.setdefault("min_interval", 0)
    return GoogleDirectProvider(opener=opener, sleep=lambda s: None, **kw), opener


# ------------------------------------------------------------------ parser Google


def test_parse_google_json_strips_prefix():
    assert parse_google_json(')]}\'\n{"a": 1}') == {"a": 1}
    assert parse_google_json('{"a": 1}') == {"a": 1}
    assert parse_google_json(")]}',\n[1]") == [1]


def test_parse_google_json_rejects_html():
    with pytest.raises(ExploreError) as e:
        parse_google_json("<html>Too many requests</html>")
    assert e.value.hint


def test_parse_timeline_google():
    rows = parse_timeline_google(json.loads(TIMELINE_RESP[len(PREFIX):]), 2)
    assert [r["values"] for r in rows] == [[40, 10], [100, 20], [60, 0]]
    assert rows[0]["label"] == "27 Okt 2024" and rows[0]["t"] == 1730000000
    assert rows[2]["partial"] is True


def test_summarize_ignores_partial_last_point():
    tl = [{"t": 1, "label": "a", "values": [10], "partial": False},
          {"t": 2, "label": "b", "values": [20], "partial": False},
          {"t": 3, "label": "c", "values": [30], "partial": False},
          {"t": 4, "label": "d", "values": [40], "partial": False},
          {"t": 5, "label": "e", "values": [0], "partial": True}]
    s = summarize(tl, ["x"])[0]
    assert s["latest"] == 40 and s["peak"] == 40 and s["peak_label"] == "d"
    assert s["average"] == 25.0
    assert s["change_pct"] == 300  # 10 -> 40


def test_summarize_zero_base_and_empty():
    assert summarize([{"t": 1, "label": "a", "values": [0], "partial": False}], ["x"])[0]["change_pct"] is None
    assert summarize([], ["x"])[0]["latest"] == 0


# ------------------------------------------------------------------ GoogleDirectProvider


def test_google_full_explore():
    p, opener = google()
    r = p.explore(["Beasiswa", "cpns"], "ID", 0, "today 12-m", "", explore.PARTS)
    assert r["source"] == "google" and len(r["timeline"]) == 3
    assert r["summary"][0]["keyword"] == "Beasiswa" and r["summary"][0]["peak"] == 100
    assert r["related"]["Beasiswa"]["top"][0]["query"] == "beasiswa lpdp"
    assert r["related"]["Beasiswa"]["rising"][0]["label"] == "Breakout"
    assert [x["name"] for x in r["regions"]] == ["Jawa Barat", "DKI Jakarta"]  # Aceh (tanpa data) dibuang
    urls = " ".join(opener.calls)
    assert "token=TOK_TS" in urls and "token=TOK_RQ" in urls and "token=TOK_GEO" in urls
    assert "REGION" in [c for c in opener.calls if "comparedgeo" in c][0]  # negara -> resolusi provinsi


def test_google_worldwide_uses_country_resolution():
    p, opener = google()
    p.explore(["beasiswa"], "", 0, "today 12-m", "", ("timeline", "regions"))
    assert "COUNTRY" in [c for c in opener.calls if "comparedgeo" in c][0]
    assert not any("relatedsearches" in c for c in opener.calls)  # parts dihormati


def test_google_related_failure_becomes_note_not_error():
    bad = urllib.error.HTTPError("http://x", 400, "Bad", {}, None)
    p, _ = google({"/api/explore": EXPLORE_RESP, "/multiline": TIMELINE_RESP,
                   "/relatedsearches": bad, "/comparedgeo": GEO_RESP})
    r = p.explore(["beasiswa"], "ID", 0, "today 12-m")
    assert r["timeline"] and r["related"] == {} and r["regions"]
    assert any("Query terkait" in n for n in r["notes"])


def test_google_missing_timeseries_widget():
    p, _ = google({"/api/explore": g({"widgets": []})})
    with pytest.raises(ExploreError, match="data grafik"):
        p.explore(["x"], "ID", 0, "today 12-m")


def test_google_empty_timeline_explains_why():
    p, _ = google({"/api/explore": EXPLORE_RESP, "/multiline": g({"default": {"timelineData": []}})})
    with pytest.raises(ExploreError, match="volume pencarian") as e:
        p.explore(["x"], "ID", 0, "today 12-m")
    assert e.value.hint


def test_google_retries_429_then_succeeds():
    r429 = urllib.error.HTTPError("http://x", 429, "Too Many", {}, None)
    p, opener = google({"/api/explore": [r429, r429, EXPLORE_RESP], "/multiline": TIMELINE_RESP,
                        "/relatedsearches": RELATED_RESP, "/comparedgeo": GEO_RESP})
    assert p.explore(["x"], "ID", 0, "today 12-m")["timeline"]
    assert sum("/api/explore" in c for c in opener.calls) == 3


def test_google_gives_up_after_429s_with_hint():
    r429 = urllib.error.HTTPError("http://x", 429, "Too Many", {}, None)
    p, _ = google({"/api/explore": r429})
    with pytest.raises(ExploreError) as e:
        p.explore(["x"], "ID", 0, "today 12-m")
    assert "3 percobaan" in str(e.value) and "IP" in e.value.hint


def test_google_403_is_not_retried():
    r403 = urllib.error.HTTPError("http://x", 403, "Forbidden", {}, None)
    p, opener = google({"/api/explore": r403})
    with pytest.raises(ExploreError, match="403"):
        p.explore(["x"], "ID", 0, "today 12-m")
    assert len(opener.calls) == 1


def test_google_throttles_between_requests():
    sleeps, clock = [], iter(range(0, 1000))
    opener = FakeOpener({"/api/explore": EXPLORE_RESP, "/multiline": TIMELINE_RESP,
                         "/relatedsearches": RELATED_RESP, "/comparedgeo": GEO_RESP})
    p = GoogleDirectProvider(opener=opener, sleep=sleeps.append, clock=lambda: next(clock) * 0.1,
                             min_interval=1.0)
    p.explore(["Beasiswa"], "ID", 0, "today 12-m")
    assert sleeps and all(0 < s <= 1.0 for s in sleeps)


# ------------------------------------------------------------------ SerpApi

SERP_TS = json.dumps({"interest_over_time": {"timeline_data": [
    {"date": "Nov 3 – 9, 2024", "timestamp": "1730592000",
     "values": [{"query": "beasiswa", "value": "45", "extracted_value": 45},
                {"query": "cpns", "value": "<1", "extracted_value": 0}]},
    {"date": "Nov 10 – 16, 2024", "timestamp": "1731196800", "partial_data": True,
     "values": [{"query": "cpns", "value": "80", "extracted_value": 80},
                {"query": "beasiswa", "value": "100", "extracted_value": 100}]},
]}})
SERP_REL = json.dumps({"related_queries": {
    "top": [{"query": "beasiswa lpdp", "value": "100", "extracted_value": 100}],
    "rising": [{"query": "beasiswa 2026", "value": "Breakout", "extracted_value": 5000}]}})
SERP_GEO = json.dumps({"compared_breakdown_by_region": [
    {"geo": "ID-JB", "location": "Jawa Barat",
     "values": [{"query": "beasiswa", "extracted_value": 90}, {"query": "cpns", "extracted_value": 30}]}]})


def serp(routes, key="SECRETKEY"):
    queue = {k: (list(v) if isinstance(v, list) else [v]) for k, v in routes.items()}
    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        for frag, q in queue.items():
            if frag in req.full_url:
                out = q.pop(0) if len(q) > 1 else q[0]
                if isinstance(out, Exception):
                    raise out
                return FakeResponse(out)
        raise AssertionError(req.full_url)

    p = SerpApiProvider(key, opener=opener)
    p.calls = calls
    return p


def test_serpapi_full_explore_maps_values_by_query_name():
    p = serp({"data_type=TIMESERIES": SERP_TS, "data_type=RELATED_QUERIES": SERP_REL,
              "data_type=GEO_MAP": SERP_GEO})
    r = p.explore(["beasiswa", "cpns"], "ID", 7, "today 12-m")
    assert r["source"] == "serpapi"
    assert [pt["values"] for pt in r["timeline"]] == [[45, 0], [100, 80]]  # urutan dipetakan lewat nama
    assert r["timeline"][1]["partial"] is True and r["timeline"][0]["t"] == 1730592000
    assert r["related"]["beasiswa"]["rising"][0]["label"] == "Breakout"
    assert r["regions"][0]["values"] == [90, 30]
    assert any("keyword pertama" in n for n in r["notes"])
    first = p.calls[0]
    assert "cat=7" in first and "geo=ID" in first and "q=beasiswa%2Ccpns" in first


def test_serpapi_never_leaks_key_in_errors():
    body = io.BytesIO(json.dumps({"error": "Invalid API key. SECRETKEY"}).encode())
    err = urllib.error.HTTPError("https://serpapi.com/search.json?api_key=SECRETKEY", 401, "x", {}, body)
    p = serp({"data_type=TIMESERIES": err})
    with pytest.raises(ExploreError) as e:
        p.explore(["x"], "ID", 0, "today 12-m")
    assert "SECRETKEY" not in str(e.value) and "SECRETKEY" not in repr(e.value.__cause__)


def test_serpapi_error_field_in_200_response():
    p = serp({"data_type=TIMESERIES": json.dumps({"error": "Google hasn't returned any results"})})
    with pytest.raises(ExploreError, match="hasn't returned"):
        p.explore(["x"], "ID", 0, "today 12-m")


def test_serpapi_optional_parts_fail_softly():
    p = serp({"data_type=TIMESERIES": SERP_TS, "data_type=RELATED_QUERIES": json.dumps({"error": "boom"}),
              "data_type=GEO_MAP": json.dumps({"error": "boom"})})
    r = p.explore(["beasiswa", "cpns"], "", 0, "today 12-m")
    assert r["timeline"] and r["related"] == {} and r["regions"] == []
    assert len([n for n in r["notes"] if "tidak tersedia" in n]) == 2


# ------------------------------------------------------------------ Mock & chain


def test_mock_is_deterministic_and_normalised():
    a = MockExploreProvider().explore(["beasiswa", "cpns"], "ID", 0, "today 12-m")
    b = MockExploreProvider().explore(["beasiswa", "cpns"], "ID", 0, "today 12-m")
    assert a["timeline"] == b["timeline"]
    assert len(a["timeline"]) == 53
    assert max(v for p in a["timeline"] for v in p["values"]) == 100
    assert a["source"] == "mock" and "bukan dari Google" in a["notes"][0]
    assert a["regions"] and a["related"]["cpns"]["rising"][0]["label"] == "Breakout"


def test_mock_respects_parts_and_custom_range():
    r = MockExploreProvider().explore(["x"], "US", 0, "2025-01-01 2025-01-10", "", ("timeline",))
    assert len(r["timeline"]) == 10 and r["related"] == {} and r["regions"] == []


def test_chain_falls_back_and_reports_all_errors():
    class Boom:
        name = "a"

        def explore(self, *a, **k):
            raise ExploreError("rusak", hint="saran A")

    ok = MockExploreProvider()
    assert ChainProvider([Boom(), ok]).explore(["x"], "ID", 0, "today 12-m")["source"] == "mock"
    with pytest.raises(ExploreError) as e:
        ChainProvider([Boom(), Boom()]).explore(["x"], "ID", 0, "today 12-m")
    assert "a: rusak | a: rusak" in str(e.value) and e.value.hint == "saran A"


def test_get_provider_selection(monkeypatch):
    explore._providers.clear()
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.delenv("TRENDWATCH_EXPLORE_SOURCE", raising=False)
    assert isinstance(explore.get_provider(), GoogleDirectProvider)
    assert isinstance(explore.get_provider(demo=True), MockExploreProvider)
    monkeypatch.setenv("SERPAPI_KEY", "k")
    chain = explore.get_provider()
    assert isinstance(chain, ChainProvider) and [p.name for p in chain.providers] == ["google", "serpapi"]
    monkeypatch.setenv("TRENDWATCH_EXPLORE_SOURCE", "serpapi")
    assert isinstance(explore.get_provider(), SerpApiProvider)
    monkeypatch.delenv("SERPAPI_KEY")
    with pytest.raises(ExploreError, match="SERPAPI_KEY"):
        explore.get_provider()
    monkeypatch.setenv("TRENDWATCH_EXPLORE_SOURCE", "ngawur")
    with pytest.raises(ExploreError):
        explore.get_provider()
    explore._providers.clear()


# ------------------------------------------------------------------ explore_service


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("TRENDWATCH_EXPLORE_TTL_HOURS", raising=False)
    db.init_db()


def norm(**kw):
    base = dict(keywords=["beasiswa"], geo="ID", category=0, time_range="today 12-m", gprop="", parts=None)
    base.update(kw)
    return explore_service.normalize_params(**base)


def test_normalize_cleans_keywords():
    p = norm(keywords=["  Beasiswa ", "beasiswa", "cpns, pns", "", "a\tb"])
    assert p["keywords"] == ["Beasiswa", "cpns pns", "a b"]  # dedupe, koma jadi spasi
    assert p["parts"] == ["timeline", "related", "regions"]
    assert norm(geo="id-jk")["geo"] == "ID-JK"
    assert norm(geo="")["geo"] == ""
    assert norm(parts="regions")["parts"] == ["timeline", "regions"]  # grafik selalu ikut


@pytest.mark.parametrize("kw, msg", [
    (dict(keywords=[]), "minimal satu"),
    (dict(keywords=["a", "b", "c", "d", "e", "f"]), "Maksimal 5"),
    (dict(keywords=["x" * 101]), "terlalu panjang"),
    (dict(keywords=["a\x00b"]), "karakter"),
    (dict(geo="Indonesia"), "wilayah"),
    (dict(category="abc"), "angka"),
    (dict(category=-1), "kategori"),
    (dict(time_range="kapan saja"), "Rentang waktu"),
    (dict(time_range="2025-06-01 2025-01-01"), "Tanggal awal"),
    (dict(time_range="2025-13-01 2025-14-01"), "Tanggal tidak valid"),
    (dict(gprop="tiktok"), "Jenis pencarian"),
])
def test_normalize_rejects_bad_input(kw, msg):
    with pytest.raises(ValueError, match=msg):
        norm(**kw)


class CountingProvider:
    name = "count"

    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def explore(self, keywords, geo, category, time_range, gprop="", parts=explore.PARTS):
        self.calls += 1
        if self.fail:
            raise ExploreError("diblokir")
        tl = [{"t": 1, "label": "a", "values": [10] * len(keywords), "partial": False}]
        return build_result("count", list(keywords), geo, category, time_range, gprop, tl, {}, [], [])


def test_run_caches_and_refresh(tmp_db):
    prov, params = CountingProvider(), norm()
    r1 = explore_service.run(params, provider=prov, now=1000)
    r2 = explore_service.run(params, provider=prov, now=1060)
    assert (prov.calls, r1["cached"], r2["cached"], r2["cache_age_seconds"]) == (1, False, True, 60)
    explore_service.run(params, provider=prov, now=1100, refresh=True)
    assert prov.calls == 2
    explore_service.run(params, provider=prov, now=1100 + 7 * 3600)  # lewat TTL 6 jam
    assert prov.calls == 3
    assert r1["category_name"] == "Semua kategori"


def test_run_different_params_do_not_share_cache(tmp_db):
    prov = CountingProvider()
    explore_service.run(norm(), provider=prov, now=1)
    explore_service.run(norm(geo=""), provider=prov, now=2)
    explore_service.run(norm(category=5), provider=prov, now=3)
    assert prov.calls == 3


def test_run_serves_stale_cache_when_source_fails(tmp_db):
    params = norm()
    explore_service.run(params, provider=CountingProvider(), now=1000)
    out = explore_service.run(params, provider=CountingProvider(fail=True), now=1000 + 10 * 3600)
    assert out["stale"] is True and out["cached"] is True
    assert "diblokir" in out["notes"][-1]


def test_run_raises_when_source_fails_without_cache(tmp_db):
    with pytest.raises(ExploreError, match="diblokir"):
        explore_service.run(norm(), provider=CountingProvider(fail=True), now=1)


def test_run_demo_is_never_cached(tmp_db):
    out = explore_service.run(norm(), demo=True, now=1)
    assert out["source"] == "mock"
    assert db.recent_searches() == []


def test_recent_searches_newest_first(tmp_db):
    prov = CountingProvider()
    explore_service.run(norm(keywords=["a"]), provider=prov, now=1)
    explore_service.run(norm(keywords=["b"]), provider=prov, now=2)
    assert [r["keywords"] for r in db.recent_searches()] == [["b"], ["a"]]


def test_corrupt_cache_row_is_ignored(tmp_db):
    params = norm()
    key = explore_service._cache_key(params)
    db.cache_put(key, params, {"x": 1}, 1)
    from contextlib import closing
    with closing(db.connect()) as c, c:
        c.execute("UPDATE explore_cache SET payload = 'bukan json'")
    assert db.cache_get(key) is None
    assert explore_service.run(params, provider=CountingProvider(), now=5)["cached"] is False


# ------------------------------------------------------------------ API (perlu fastapi)


@pytest.fixture()
def client(tmp_db, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.delenv("TRENDWATCH_PASSWORD", raising=False)
    with TestClient(app) as c:
        yield c


def test_api_categories(client):
    d = client.get("/api/categories").json()
    assert {"id": 0, "name": "Semua kategori"} in d["categories"]
    assert d["limit"] == 5 and d["timeframes"][0]["value"] == "now 7-d"


def test_api_explore_demo_and_validation(client):
    r = client.get("/api/explore", params={"q": ["beasiswa", "cpns"], "demo": "true", "geo": "ID", "cat": 0})
    assert r.status_code == 200
    d = r.json()
    assert d["source"] == "mock" and d["keywords"] == ["beasiswa", "cpns"] and d["category_name"]
    assert client.get("/api/explore?demo=true").status_code == 400  # tanpa keyword
    assert client.get("/api/explore", params={"q": "x", "geo": "zzz", "demo": "true"}).status_code == 400
    assert client.get("/api/explore", params={"q": "x", "time": "salah", "demo": "true"}).status_code == 400


def test_api_explore_error_becomes_502_with_hint(client, monkeypatch):
    class Boom:
        name = "boom"

        def explore(self, *a, **k):
            raise ExploreError("diblokir", hint="coba SerpApi")

    monkeypatch.setattr(explore_service, "get_provider", lambda demo=False: Boom())
    r = client.get("/api/explore", params={"q": "x"})
    assert r.status_code == 502 and "diblokir" in r.json()["detail"] and "coba SerpApi" in r.json()["detail"]


def test_api_pages_and_recent(client):
    assert client.get("/explore").status_code == 200
    assert client.get("/api/explore/recent").json() == {"items": []}
