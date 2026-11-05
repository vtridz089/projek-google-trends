"""Tes keyword research: saran Google, intent/funnel, skor, penggabungan, dan cache."""
import pytest

from app import db, insight, keyword_research as kr, suggest
from app.explore import ExploreError


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "k.db"))
    db.init_db()


# --------------------------------------------------------------------------- saran Google (parser dan sebaran)


def test_parse_reads_firefox_format_and_rejects_garbage():
    assert suggest.parse('["bumn", ["bumn adalah", " bumn 2026 ", "", 5, "bumn gratis"]]') == ["bumn adalah", "bumn 2026", "bumn gratis"]
    for bad in ("<html>captcha</html>", '{"a": 1}', '["x"]', '["x", "bukan list"]'):
        with pytest.raises(ValueError):
            suggest.parse(bad)


def test_variants_count_and_labels():
    assert len(suggest.variants("bumn", "cepat")) == 17 and len(suggest.variants("bumn", "lengkap")) == 43
    assert suggest.variants("bumn")[0] == ("bumn", "seed") and ("apa itu bumn", "apa itu") in suggest.variants("bumn")
    assert ("bumn z", "z") in suggest.variants("bumn", "lengkap")


def test_collect_dedupes_drops_seed_and_off_topic():
    raw = [("bumn", "seed", ["BUMN", "bumn 2026", "bumd"]), ("apa itu bumn", "apa itu", ["apa itu bumn", "Bumn 2026", "pt telkom"])]
    got = suggest._collect("bumn", raw)
    assert [(i["query"], i["via"], i["rank"]) for i in got] == [("bumn 2026", "seed", 2), ("apa itu bumn", "apa itu", 1)]


class FlakySuggest(suggest.GoogleSuggest):
    def __init__(self, fail_texts=(), fail_all=False):
        super().__init__(min_interval=0)
        self.fail_texts, self.fail_all, self.seen = set(fail_texts), fail_all, []

    def fetch(self, text, geo):
        self.seen.append(text)
        if self.fail_all or text in self.fail_texts:
            raise ExploreError("Google Autocomplete menolak permintaan (HTTP 429).")
        return [f"{text} a", f"{text} b"]


def test_expand_partial_failure_adds_note_and_all_failed_raises():
    ok = FlakySuggest(fail_texts={"cara bumn"}).expand("bumn", "ID", "cepat")
    assert ok["failed"] == 1 and ok["requests"] == 17 and any("1 dari 17" in n for n in ok["notes"]) and ok["items"]
    with pytest.raises(ExploreError, match="429"):
        FlakySuggest(fail_all=True).expand("bumn", "ID", "cepat")


def test_mock_suggest_is_deterministic_and_marked():
    a, b = suggest.MockSuggest().expand("cpns", "ID", "cepat"), suggest.MockSuggest().expand("cpns", "ID", "cepat")
    assert a == b and a["source"] == "mock" and a["items"] and any("bukan dari Google" in n for n in a["notes"])


# --------------------------------------------------------------------------- intent dan funnel


@pytest.mark.parametrize("kw, intent, tahap", [
    ("apa itu bumn", "informasional", "TOFU"), ("cara daftar bumn", "informasional", "TOFU"),
    ("review bumn terbaik", "komersial", "MOFU"), ("bumn vs swasta", "komersial", "MOFU"),
    ("harga saham bumn", "transaksional", "BOFU"), ("harga terbaik saham", "transaksional", "BOFU"),  # transaksi menang
    ("login mybumn", "navigasi", "BOFU"), ("bumn 2026", "lainnya", None),
])
def test_intent5_and_funnel(kw, intent, tahap):
    assert insight.intent5(kw) == intent and insight.FUNNEL[intent] == tahap
    assert insight.CONTENT_FORMAT[intent]


def test_is_question():
    assert all(kr.is_question(k) for k in ("apa itu bumn", "cara daftar", "di mana kantor bumn", "berapa gaji"))
    assert not any(kr.is_question(k) for k in ("bumn terbaru", "harga cara", "apalah", ""))


# --------------------------------------------------------------------------- skor


def row(q="bumn x", n7=None, n90=None, demand=None, rank=None, src=()):
    return {"query": q, "naik_7h": n7, "naik_90h": n90, "permintaan": demand, "ac_rank": rank, "sumber": set(src)}


G = lambda v, b=False: {"label": f"+{v}%", "value": v, "breakout": b}  # noqa: E731


def test_score_known_values():
    s = kr.score(row("bumn 2026 gratis terbaru", demand=64, src=("saran", "populer")), {"bumn"})
    assert s["bagian"] == {"momentum": 0, "permintaan": 19, "longtail": 15, "relevan": 10, "multisumber": 10} and s["skor"] == 54
    assert kr.score(row(n7=G(9999, True)), set())["bagian"]["momentum"] == 35
    assert kr.score(row(rank=1), set())["bagian"]["permintaan"] == 20 and kr.score(row(rank=10), set())["bagian"]["permintaan"] == 8
    assert kr.score(row(demand=100, rank=1), set())["bagian"]["permintaan"] == 30       # Trends menang atas urutan saran
    assert kr.score(row("satu"), set())["bagian"]["longtail"] == 0


def test_score_bounds_and_monotonic():
    best = kr.score(row("bumn a b c d", n7=G(9999, True), demand=100, rank=1, src=("saran", "naik", "populer")), {"bumn"})
    assert best["skor"] == 100 and best["level"] == "tinggi"
    assert 0 <= kr.score(row("x"), {"bumn"})["skor"] <= 100
    assert kr.score(row(n7=G(2000)), set())["skor"] > kr.score(row(n7=G(100)), set())["skor"]
    assert kr.score(row(n90=G(2000)), set())["bagian"]["momentum"] == kr.score(row(n7=G(2000)), set())["bagian"]["momentum"]


# --------------------------------------------------------------------------- penggabungan


def I(q, kind="naik", v=400, b=False):
    return {"query": q, "kind": kind, "value": v, "breakout": b, "label": "Breakout" if b else f"+{v}%"}


def test_build_rows_merges_case_insensitively_and_excludes_seed():
    sugg = [{"query": "Bumn 2026", "rank": 3, "via": "seed"}, {"query": "bumn 2026", "rank": 1, "via": "harga"},
            {"query": "BUMN", "rank": 1, "via": "seed"}]
    trends = {"naik_7h": [I("BUMN 2026"), I("bumn")], "naik_90h": [I("bumn 2026", "populer", 80)]}
    got = kr.build_rows("bumn", sugg, trends)
    assert len(got) == 1
    r = got[0]
    assert (r["ac_rank"], r["via"], r["permintaan"]) == (1, "harga", 80)
    assert r["naik_7h"]["label"] == "+400%" and r["naik_90h"] is None and r["sumber"] == ["saran", "naik", "populer"]
    assert r["skor"] == r["peluang"]["skor"] and r["rank"] == 1 and r["relevan"] is True


def test_build_rows_sorted_by_score_and_ranked():
    got = kr.build_rows("bumn", [{"query": "bumn a b c d", "rank": 1, "via": "x"}, {"query": "bumn z", "rank": 9, "via": "x"}],
                        {"naik_7h": [I("bumn z", v=5000, b=True)]})
    assert [r["query"] for r in got] == ["bumn z", "bumn a b c d"] or got[0]["skor"] >= got[1]["skor"]
    assert [r["rank"] for r in got] == [1, 2] and got[0]["skor"] >= got[1]["skor"]


def test_summarize_counts():
    rows = kr.build_rows("bumn", [{"query": "apa itu bumn", "rank": 1, "via": "x"}, {"query": "bumn harga saham hari ini", "rank": 2, "via": "x"}],
                         {"naik_7h": [I("bumn harga saham hari ini")]})
    s = kr.summarize(rows)
    assert (s["total"], s["naik"], s["pertanyaan"], s["longtail"]) == (2, 1, 1, 1)
    assert s["maksud"]["informasional"] == 1 and s["maksud"]["transaksional"] == 1 and s["tahap"] == {"TOFU": 1, "MOFU": 0, "BOFU": 1}


# --------------------------------------------------------------------------- run_keywords


class Sug:
    def __init__(self, items=None, fail=False):
        self.calls, self.fail = 0, fail
        self.items = items if items is not None else [{"query": "bumn 2026", "rank": 1, "via": "seed"},
                                                      {"query": "apa itu bumn", "rank": 2, "via": "apa itu"}]

    def expand(self, seed, geo="ID", depth="cepat"):
        self.calls += 1
        if self.fail:
            raise ExploreError("Google Autocomplete menolak permintaan (HTTP 429).", hint="tunggu")
        return {"items": self.items, "requests": 17, "failed": 0, "notes": [], "source": "google"}


class Trends:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def explore(self, keywords, geo, category, time_range, gprop="", parts=()):
        self.calls.append(time_range)
        if self.fail:
            raise ExploreError("Google menolak (429).")
        k = keywords[0]
        return {"source": "google", "fetched_at": "x", "notes": [], "related": {k: {
            "rising": [{"query": f"{k} baru", "value": 700, "label": "+700%"}],
            "top": [{"query": f"{k} 2026", "value": 90}]}}}


def test_run_combines_both_sources_and_clusters():
    out = kr.run_keywords("bumn", "ID", "cepat", suggester=Sug(), provider=Trends(), now=1000)
    by = {r["query"]: r for r in out["items"]}
    assert set(by) == {"bumn 2026", "apa itu bumn", "bumn baru"}
    assert by["bumn 2026"]["sumber"] == ["saran", "populer"] and by["bumn 2026"]["permintaan"] == 90
    assert by["bumn baru"]["naik_7h"]["label"] == "+700%" and by["apa itu bumn"]["tahap"] == "TOFU"
    assert out["ringkasan"]["total"] == 3 and out["partial"] is False and out["errors"] == []
    assert all(r["kelompok"] for r in out["items"]) and out["sumber"] == {"saran": "google", "trends": "google"}


def test_trends_failure_is_partial_not_fatal_and_suggest_failure_too():
    out = kr.run_keywords("bumn", "ID", "cepat", suggester=Sug(), provider=Trends(fail=True), now=1000)
    assert out["partial"] and {e["scope"] for e in out["errors"]} == {"Google Trends (7 hari)", "Google Trends (90 hari)"} and out["items"]
    out2 = kr.run_keywords("bumn", "ID", "cepat", suggester=Sug(fail=True), provider=Trends(), now=1000, refresh=True)
    assert out2["partial"] and out2["errors"][0]["scope"].startswith("Saran Google") and "Saran: tunggu" in out2["errors"][0]["error"]
    assert {r["query"] for r in out2["items"]} == {"bumn baru", "bumn 2026"}


def test_everything_failing_raises_and_trends_can_be_skipped():
    with pytest.raises(ExploreError, match="429"):
        kr.run_keywords("bumn", "ID", "cepat", suggester=Sug(fail=True), provider=Trends(fail=True), now=1)
    prov = Trends()
    out = kr.run_keywords("bumn", "ID", "cepat", suggester=Sug(), provider=prov, with_trends=False, now=1)
    assert prov.calls == [] and out["sumber"]["trends"] is None and len(out["items"]) == 2


def test_cache_full_results_for_hours_but_partial_only_minutes():
    sug = Sug()
    kr.run_keywords("bumn", "ID", "cepat", suggester=sug, provider=Trends(), now=1000)
    assert kr.run_keywords("bumn", "ID", "cepat", suggester=sug, provider=Trends(), now=1000 + 3600)["cached"] is True
    assert sug.calls == 1
    assert kr.run_keywords("bumn", "ID", "cepat", suggester=sug, provider=Trends(), now=1000 + 7 * 3600)["cached"] is False

    sp = Sug()
    kr.run_keywords("cpns", "ID", "cepat", suggester=sp, provider=Trends(fail=True), now=5000)
    assert kr.run_keywords("cpns", "ID", "cepat", suggester=sp, provider=Trends(), now=5000 + 100)["cached"] is True
    again = kr.run_keywords("cpns", "ID", "cepat", suggester=sp, provider=Trends(), now=5000 + 700)   # hasil sebagian kedaluwarsa
    assert again["cached"] is False and again["partial"] is False and sp.calls == 2


def test_demo_runs_offline_and_is_never_cached():
    a = kr.run_keywords("cpns", "ID", "lengkap", demo=True, now=1)
    b = kr.run_keywords("cpns", "ID", "lengkap", demo=True, now=2)
    assert a["sumber"] == {"saran": "mock", "trends": "mock"} and a["cached"] is False and b["cached"] is False
    assert a["requests"]["saran"] == 43 and a["ringkasan"]["total"] > 50


@pytest.mark.parametrize("seed, geo, depth", [("", "ID", "cepat"), ("a, b", "ID", "cepat"), ("a\nb", "ID", "cepat"),
                                              ("x" * 101, "ID", "cepat"), ("bumn", "IDN", "cepat"), ("bumn", "ID", "dalam")])
def test_invalid_input_raises_value_error(seed, geo, depth):
    with pytest.raises(ValueError):
        kr.normalize(seed, geo, depth)


# --------------------------------------------------------------------------- API (perlu FastAPI)


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c


def test_api_keywords_demo_and_validation(client):
    r = client.get("/api/keywords", params={"seed": "cpns", "demo": "true"})
    assert r.status_code == 200 and r.json()["ringkasan"]["total"] > 20 and r.json()["items"][0]["rank"] == 1
    assert client.get("/api/keywords").status_code == 400
    assert client.get("/api/keywords", params={"seed": "a, b"}).status_code == 400
    assert client.get("/api/keywords", params={"seed": "a", "depth": "dalam"}).status_code == 400
    assert "Keyword research" in client.get("/").text and client.get("/booming").status_code == 200


# --------------------------------------------------------------------------- saluran tambahan (YouTube, Bing)


def test_channel_urls_and_validation(monkeypatch):
    seen = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'["x", ["x a"]]'

    monkeypatch.setattr(suggest.urllib.request, "urlopen", lambda req, timeout=0: (seen.append(req.full_url), Resp())[1])
    for ch in suggest.CHANNELS:
        assert suggest.GoogleSuggest(min_interval=0, channel=ch).fetch("x", "ID") == ["x a"]
    g, yt, bing = seen
    assert "suggestqueries.google.com" in g and "ds=yt" not in g
    assert "suggestqueries.google.com" in yt and "ds=yt" in yt
    assert "api.bing.com/osjson.aspx" in bing and "mkt=id-ID" in bing
    with pytest.raises(ValueError):
        suggest.GoogleSuggest(channel="tiktok")


def test_normalize_channels():
    assert kr.normalize_channels("bing, YouTube") == ("youtube", "bing") and kr.normalize_channels("") == ()
    assert kr.normalize_channels(["bing"]) == ("bing",)
    with pytest.raises(ValueError, match="tiktok"):
        kr.normalize_channels("youtube,tiktok")


def test_extra_channels_merge_sources_and_tolerate_failure(monkeypatch):
    class Ch:
        def __init__(self, ch): self.ch = ch
        def expand(self, seed, geo="ID", depth="cepat"):
            if self.ch == "bing":
                raise ExploreError("Bing (saran pencarian) menolak permintaan (HTTP 429).")
            return {"items": [{"query": "bumn 2026", "rank": 2, "via": "seed"}, {"query": "bumn tutorial video", "rank": 1, "via": "seed"}],
                    "requests": 17, "failed": 0, "notes": [], "source": "google", "channel": "youtube"}
    monkeypatch.setattr(suggest, "get_suggest", lambda demo=False, channel="google": Ch(channel))
    out = kr.run_keywords("bumn", "ID", "cepat", suggester=Sug(), provider=Trends(), now=1, channels="youtube,bing")
    by = {r["query"]: r for r in out["items"]}
    assert by["bumn 2026"]["sumber"] == ["saran", "youtube", "populer"] and by["bumn 2026"]["skor_bagian"]["multisumber"] == 10
    assert by["bumn tutorial video"]["sumber"] == ["youtube"]
    assert out["kanal"] == ["youtube", "bing"] and out["partial"] and any(e["scope"] == "Saran Bing" for e in out["errors"])
    assert out["requests"]["saran"] == 34


def test_two_suggest_channels_alone_count_as_multisource():
    r = kr.score(row("bumn a b c", src=("saran", "youtube")), set())
    assert r["bagian"]["multisumber"] == 10 and kr.score(row("bumn a b c", src=("saran",)), set())["bagian"]["multisumber"] == 0


def test_channels_change_cache_key_only_when_used(monkeypatch):
    monkeypatch.setattr(suggest, "get_suggest", lambda demo=False, channel="google": Sug())  # jangan menyentuh jaringan
    sug = Sug()
    kr.run_keywords("bumn", "ID", "cepat", suggester=sug, provider=Trends(), now=1000, demo=False, channels=())
    assert kr.run_keywords("bumn", "ID", "cepat", suggester=sug, provider=Trends(), now=1001)["cached"] is True
    assert kr.run_keywords("bumn", "ID", "cepat", suggester=sug, provider=Trends(), now=1002, channels="youtube", )["cached"] is False


def test_demo_with_channels_marks_mock():
    out = kr.run_keywords("cpns", "ID", "cepat", demo=True, now=1, channels="youtube,bing")
    assert out["sumber"]["saran"] == "mock" and any("youtube" in r["sumber"] for r in out["items"])


# --------------------------------------------------------------------------- turunan satu keyword


@pytest.mark.parametrize("kw, intent", [
    ("pendaftaran cpns 2027 kapan dibuka", "informasional"), ("kapan cpns 2027 dibuka", "informasional"),
    ("info cpns 2027", "lainnya"), ("jadwal cpns 2027", "informasional"),
    ("pendaftaran cpns 2027", "navigasi"), ("link akun sscasn", "navigasi")])
def test_intent_for_children_examples(kw, intent):
    assert insight.intent5(kw) == intent


class KidSug:
    def __init__(self): self.calls = 0
    def expand(self, seed, geo="ID", depth="cepat"):
        self.calls += 1
        raw = [(seed, "seed", [f"kapan {seed} dibuka", f"info {seed}", f"jadwal {seed}", "cpns 2026", seed, f"{seed} apa"])]
        return {"items": suggest._collect(seed, raw), "requests": 17, "failed": 0, "notes": [], "source": "google"}


def test_run_children_returns_only_phrase_matches_and_caches():
    sug = KidSug()
    out = kr.run_children("cpns 2027", "ID", suggester=sug, now=1000)
    assert {r["query"] for r in out["items"]} == {"kapan cpns 2027 dibuka", "info cpns 2027", "jadwal cpns 2027", "cpns 2027 apa"}
    assert all(r["rank"] and r["maksud_label"] for r in out["items"]) and out["query"] == "cpns 2027" and not out["partial"]
    assert kr.run_children("cpns 2027", "ID", suggester=sug, now=1100)["cached"] is True and sug.calls == 1
    assert kr.run_children("cpns 2027", "ID", suggester=sug, now=1200, refresh=True)["cached"] is False


def test_run_children_failure_and_limit():
    class Bad:
        def expand(self, *a, **k): raise ExploreError("429", hint="tunggu")
    with pytest.raises(ExploreError, match="429"):
        kr.run_children("cpns 2027", "ID", suggester=Bad(), now=1)
    assert len(kr.run_children("cpns", "ID", demo=True, now=1, limit=5)["items"]) == 5
    with pytest.raises(ValueError):
        kr.run_children("a, b", "ID", demo=True)


def test_api_children(client):
    r = client.get("/api/keywords/turunan", params={"q": "cpns 2027", "demo": "true"})
    assert r.status_code == 200 and r.json()["items"] and all("cpns" in i["query"] for i in r.json()["items"])
    assert client.get("/api/keywords/turunan").status_code == 400
    assert client.get("/api/keywords/turunan", params={"q": "x", "kanal": "tiktok"}).status_code == 400
