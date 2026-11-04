"""Tes Riset topik: gabungan beberapa rentang waktu dan turunan keyword."""
import pytest

from app import db, explore_service
from app.explore import ExploreError
from app.explore_service import merge_research, run_research


def I(query, kind="naik", value=300, breakout=False):
    return {"query": query, "kind": kind, "value": value, "breakout": breakout,
            "label": "Breakout" if breakout else (f"+{value}%" if kind == "naik" else str(value))}


# --------------------------------------------------------------------------- merge_research


def test_pattern_baru_konsisten_naik_populer():
    got = merge_research("bumn", {
        "now 1-d": [I("bumn baru"), I("bumn tetap"), I("bumn pop", "populer", 90)],
        "now 7-d": [I("bumn baru", value=500), I("bumn tetap"), I("bumn sebulan")],
        "today 1-m": [I("bumn tetap"), I("bumn sebulan")],
        "today 3-m": [I("bumn tetap")],
    })
    by = {r["query"]: r for r in got}
    assert by["bumn baru"]["pattern"] == "baru" and by["bumn baru"]["max_value"] == 500
    assert by["bumn tetap"]["pattern"] == "konsisten" and by["bumn tetap"]["rising_count"] == 4
    assert by["bumn sebulan"]["pattern"] == "naik"
    assert by["bumn pop"]["pattern"] == "populer" and by["bumn pop"]["rising_count"] == 0


def test_sorted_breakout_then_window_count_then_growth_with_populer_last():
    got = merge_research("t", {
        "now 1-d": [I("pop", "populer", 99), I("kecil", value=100), I("besar", value=900)],
        "now 7-d": [I("kecil", value=100), I("brk", value=9000, breakout=True)],
    })
    assert [r["query"] for r in got] == ["brk", "kecil", "besar", "pop"]
    assert [r["rank"] for r in got] == [1, 2, 3, 4]


def test_same_keyword_different_case_is_merged_once():
    got = merge_research("t", {"now 1-d": [I("Danantara")], "now 7-d": [I("danantara")]})
    assert len(got) == 1 and got[0]["rising_count"] == 2


def test_turunan_requires_all_topic_words():
    got = {r["query"]: r["turunan"] for r in merge_research("harga bbm", {
        "now 7-d": [I("harga bbm naik"), I("bbm harga hari ini"), I("pertamax"), I("harga emas")]})}
    assert got == {"harga bbm naik": True, "bbm harga hari ini": True, "pertamax": False, "harga emas": False}


# --------------------------------------------------------------------------- run_research


class FakeProvider:
    """Mengembalikan related queries tetap; mencatat setiap permintaan."""
    def __init__(self, fail_on=()):
        self.calls, self.fail_on = [], set(fail_on)

    def explore(self, keywords, geo, category, time_range, gprop="", parts=()):
        self.calls.append((keywords[0], time_range))
        if (keywords[0], time_range) in self.fail_on:
            raise ExploreError("Google menolak (429).")
        k = keywords[0]
        rising = [{"query": f"{k} a", "value": 400, "label": "+400%"},
                  {"query": f"{k} b", "value": 5000, "label": "Breakout"}]
        return {"source": "google", "fetched_at": "x", "notes": [],
                "related": {k: {"rising": rising, "top": [{"query": f"{k} populer", "value": 50}]}}}


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"), raising=False)
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "t.db"))
    db.init_db()


def test_runs_four_windows_and_expands_top_rising():
    prov = FakeProvider()
    out = run_research("bumn", "ID", 0, expand=2, provider=prov, refresh=True)
    windows = [t for k, t in prov.calls if k == "bumn"]
    assert windows == ["now 1-d", "now 7-d", "today 1-m", "today 3-m"]
    assert len(prov.calls) == 4 + 2 and out["expanded"] == 2
    top = out["items"][0]
    assert top["query"] == "bumn b" and top["breakout"] and top["pattern"] == "konsisten"
    assert [c["query"] for c in top["children"]] == ["bumn b b", "bumn b a"]
    assert out["items"][-1]["pattern"] == "populer" and out["items"][-1]["children"] == []
    assert all(w["ok"] for w in out["windows"]) and out["errors"] == []


def test_expand_is_capped_and_zero_skips_children():
    class Many(FakeProvider):
        def explore(self, keywords, geo, category, time_range, gprop="", parts=()):
            res = super().explore(keywords, geo, category, time_range, gprop, parts)
            k = keywords[0]
            res["related"][k]["rising"] = [{"query": f"{k}{i}", "value": 100 + i, "label": f"+{100 + i}%"}
                                           for i in range(8)]
            return res

    prov = Many()
    run_research("x", "ID", 0, expand=99, provider=prov, refresh=True)
    assert len(prov.calls) == 4 + explore_service.RESEARCH_MAX_EXPAND
    prov2 = FakeProvider()
    run_research("x", "ID", 0, expand=0, provider=prov2, refresh=True)
    assert len(prov2.calls) == 4


def test_one_failed_window_is_reported_but_research_continues():
    prov = FakeProvider(fail_on={("bumn", "today 1-m")})
    out = run_research("bumn", "ID", 0, expand=0, provider=prov, refresh=True)
    assert [w["ok"] for w in out["windows"]] == [True, True, False, True]
    assert out["errors"] and out["errors"][0]["scope"] == "30 hari"
    assert all("today 1-m" not in r["windows"] for r in out["items"])


def test_all_windows_failing_raises_first_error():
    prov = FakeProvider(fail_on={("bumn", w) for w, _ in explore_service.RESEARCH_WINDOWS})
    with pytest.raises(ExploreError, match="429"):
        run_research("bumn", "ID", 0, provider=prov, refresh=True)


def test_failed_child_expansion_is_reported_not_fatal():
    prov = FakeProvider(fail_on={("bumn b", "now 7-d")})
    out = run_research("bumn", "ID", 0, expand=1, provider=prov, refresh=True)
    assert out["items"][0]["children"] == []
    assert any("bumn b" in e["scope"] for e in out["errors"])


def test_repeat_uses_cache_instead_of_calling_source_again():
    prov = FakeProvider()
    run_research("bumn", "ID", 0, expand=1, provider=prov, refresh=True)
    n = len(prov.calls)
    run_research("bumn", "ID", 0, expand=1, provider=prov)
    assert len(prov.calls) == n


def test_demo_mode_works_without_network():
    out = run_research("bumn", "ID", 0, expand=1, demo=True, refresh=True)
    assert out["source"] == "mock" and out["items"]
    assert out["items"][0]["rising_count"] >= 1 and out["expanded"] == 1


# --------------------------------------------------------------------------- riwayat Daily Discovery


def _save_day(topic, day, keywords, geo="ID", cat=0):
    db.discovery_init()
    w = db.watchlist_add(topic, geo, cat)
    db.discovery_save(w["id"], day, [{"keyword": k, "source": "research"} for k in keywords])
    return w


def test_history_returns_only_earlier_days_for_same_topic():
    _save_day("bumn", "2026-11-01", ["Danantara", "bumn lama"])
    _save_day("bumn", "2026-11-02", ["bumn kemarin"])
    _save_day("bumn", "2026-11-04", ["bumn hari ini"])        # hari ini tidak dihitung
    _save_day("pemilu", "2026-11-01", ["pemilu lama"])         # topik lain
    seen, days = db.discovery_history("BUMN", "ID", 0, "2026-11-04")
    assert seen == {"danantara", "bumn lama", "bumn kemarin"} and days == 2
    assert db.discovery_history("bumn", "MY", 0, "2026-11-04") == (set(), 0)
    assert db.discovery_history("tidak ada", "ID", 0, "2026-11-04") == (set(), 0)


def test_mark_history_flags_new_only_when_there_is_history():
    res = {"items": [{"query": "Danantara"}, {"query": "baru sekali"}]}
    got = explore_service.mark_history(res, {"danantara"}, 2)
    assert [r["belum_pernah"] for r in got["items"]] == [False, True] and got["history_days"] == 2
    res2 = explore_service.mark_history({"items": [{"query": "x"}]}, set(), 0)
    assert res2["items"][0]["belum_pernah"] is None and res2["history_days"] == 0


# --------------------------------------------------------------------------- API (perlu FastAPI)


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c


def _item(k, rising=True, breakout=False):
    return {"keyword": k, "rising": rising, "breakout": breakout, "score": 300, "label": "+300%"}


def test_api_save_adds_watchlist_and_candidates(client):
    r = client.post("/api/research/save", json={"topic": "bumn", "geo": "ID", "category": 0,
                    "items": [_item("bumn a"), _item("bumn b", breakout=True), _item("bumn pop", rising=False)]})
    assert r.status_code == 200 and r.json()["saved"] == 3
    rows = {x["keyword"]: x for x in client.get("/api/discovery").json()["items"]}
    assert rows["bumn b"]["trend_category"] == "trending" and rows["bumn a"]["trend_category"] == "emerging"
    assert rows["bumn pop"]["trend_category"] == "new_candidate" and rows["bumn pop"]["interest_score"] is None
    again = client.post("/api/research/save", json={"topic": "BUMN", "geo": "id", "items": [_item("bumn a")]})
    assert again.status_code == 200 and len(client.get("/api/watchlist").json()["items"]) == 1


@pytest.mark.parametrize("body", [
    {"topic": "", "geo": "ID", "items": [_item("a")]},
    {"topic": "x", "geo": "", "items": [_item("a")]},
    {"topic": "x", "geo": "ID", "source": "mock", "items": [_item("a")]},
    {"topic": "x", "geo": "ID", "items": []},
    {"topic": "x", "geo": "ID", "items": ["bukan objek"]},
    {"topic": "x", "geo": "ID", "items": [{"keyword": " "}]},
])
def test_api_save_rejects_bad_input(client, body):
    assert client.post("/api/research/save", json=body).status_code == 400
