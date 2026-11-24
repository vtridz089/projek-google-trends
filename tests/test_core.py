"""Tes keyword inti: tiap keyword inti diriset sebagai seed, hasilnya disimpan dan dibaca per platform."""
import pytest

from app import core, db, platforms
from app.explore import ExploreError


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "c.db"))
    db.init_db()
    core.init()


def gain(v, brk=False):
    return {"label": "Breakout" if brk else f"+{v}%", "value": v, "breakout": brk}


def item(q, rank, g24=None, g7=None):
    return {"query": q, "rank": rank, "words": len(q.split()), "pertanyaan": False, "longtail": False, "maksud": "informasional",
            "maksud_label": "Informasional", "sumber": ["saran"], "naik_24j": g24, "naik_7h": g7, "naik_90h": None,
            "skor": 50, "format": "Artikel"}


class Runner:
    """Pengganti riset keyword: isi per seed bisa diatur; seed tertentu bisa dibuat gagal."""
    def __init__(self, per_seed=None, fail=(), source="google", errors=()):
        self.per_seed, self.fail, self.source, self.errors, self.calls = per_seed or {}, set(fail), source, list(errors), []

    def __call__(self, seed, geo, demo=False):
        self.calls.append((seed, geo, demo))
        if seed in self.fail:
            raise ExploreError("Google menolak (429)")
        items = self.per_seed.get(seed) or [item(f"{seed.lower()} kapan dibuka", 1, g24=gain(300)), item(f"info {seed.lower()}", 2, g7=gain(40)),
                                            item(f"jadwal {seed.lower()}", 3)]
        return {"seed": seed, "items": items, "sumber": {"saran": self.source, "trends": self.source},
                "errors": [{"scope": "x", "error": e} for e in self.errors]}


# --------------------------------------------------------------------------- daftar


def test_core_keywords_match_the_list_given_by_the_client():
    assert set(core.CORE) == {p.slug for p in platforms.PLATFORMS}
    assert {s: len(v) for s, v in core.CORE.items()} == {
        "jadiasn": 11, "jadipppk": 6, "jadibumn": 8, "jadipcpm": 5, "jadiojk": 5, "psikoteskerja": 5, "jadicerebrum": 9,
        "toeflacademy": 5, "jadibeasiswa": 6, "jadipolisi": 8, "jadiprajurit": 6, "jadisekdin": 5, "jagotpa": 5,
        "jadippg": 5, "jagoukp": 7}
    assert sum(len(v) for v in core.CORE.values()) == 96
    assert core.keywords("jagoukp")[:2] == ("UKP", "UJIAN KOMPETENSI PERPAJAKAN") and core.keywords("xx") == ()


# --------------------------------------------------------------------------- riset dan simpan


def test_refresh_researches_every_core_keyword_as_a_seed_and_stores_results():
    r = Runner()
    out = core.refresh("jagoukp", runner=r, now=1000.0)
    assert out == {"diperbarui": 7, "gagal": 0, "dilewati": 0, "errors": []}
    assert [c[0] for c in r.calls] == list(core.keywords("jagoukp")) and all(c[1] == "ID" and c[2] is False for c in r.calls)
    data = core.listing("jagoukp", now=1060.0)
    first = data["items"][0]
    assert first["seed"] == "UKP" and first["status"] == "ok" and first["summary"]["total"] == 3 and first["summary"]["naik"] == 2
    assert first["items"][0]["query"] == "ukp kapan dibuka" and first["items"][0]["naik_24j"]["breakout"] is False
    assert first["summary"]["naik_24j"] == 1
    m = data["meta"]
    assert (m["total"], m["ok"], m["naik"], m["stale"], m["sample"]) == (7, 7, 14, False, False) and m["job"] is None
    assert core.listing("jagoukp", now=1000.0 + 25 * 3600)["meta"]["stale"] is True


def test_listing_before_any_refresh_is_all_unfetched():
    d = core.listing("jadippg")
    assert [i["status"] for i in d["items"]] == ["belum"] * 5 and d["meta"]["updated_at"] is None and d["meta"]["stale"]


def test_all_items_are_stored_and_new_keywords_are_flagged_on_the_next_run(monkeypatch):
    monkeypatch.setattr(core, "MAX_ITEMS", 80)  # batas pengaman; uji bahwa pemotongan hanya terjadi di batas itu
    many = [item(f"ppg kata {i}", i) for i in range(1, 121)]
    core.refresh("jadippg", runner=Runner({"PPG": many}), now=1.0)
    first = core.listing("jadippg")["items"][0]
    assert len(first["items"]) == core.MAX_ITEMS and first["summary"]["total"] == 120 and first["summary"]["ditampilkan"] == core.MAX_ITEMS
    assert not any(i["baru"] for i in first["items"])                   # belum ada pembanding
    nxt = [item("ppg kata 1", 1), item("ppg kata 2", 2), item("ppg topik baru", 3, g24=gain(500))]
    core.refresh("jadippg", runner=Runner({"PPG": nxt}), now=2.0)
    got = {i["query"]: i["baru"] for i in core.listing("jadippg")["items"][0]["items"]}
    assert got == {"ppg kata 1": False, "ppg kata 2": False, "ppg topik baru": True}
    assert core.listing("jadippg")["items"][0]["summary"]["baru"] == 1


def test_failure_keeps_previous_results_and_stops_after_repeated_failures():
    core.refresh("jadippg", runner=Runner(), now=1.0)
    bad = Runner(fail=core.keywords("jadippg"))
    out = core.refresh("jadippg", runner=bad, now=2.0)
    assert out["gagal"] == core.MAX_CONSECUTIVE_FAILS and out["dilewati"] == 5 - core.MAX_CONSECUTIVE_FAILS and len(bad.calls) == 2
    items = core.listing("jadippg")["items"]
    assert all(i["status"] == "ok" and i["items"] for i in items)      # hasil lama tetap
    assert "429" in items[0]["error"] and items[2]["error"] == ""


def test_failure_without_previous_results_is_marked_and_other_seeds_still_work():
    out = core.refresh("jadippg", runner=Runner(fail=["SOAL PPG"]), now=1.0)
    assert (out["diperbarui"], out["gagal"]) == (4, 1)
    by = {i["seed"]: i for i in core.listing("jadippg")["items"]}
    assert by["SOAL PPG"]["status"] == "gagal" and "429" in by["SOAL PPG"]["error"] and by["PPG"]["status"] == "ok"


def test_partial_source_failure_is_recorded_but_results_are_kept():
    core.refresh("jadippg", runner=Runner(errors=["Google Trends (24 jam): 429"]), now=1.0)
    first = core.listing("jadippg")["items"][0]
    assert first["status"] == "ok" and "429" in first["error"] and first["items"]


def test_sample_data_is_flagged_and_replaced_by_real_data():
    core.refresh("jadippg", runner=Runner(source="mock"), now=1.0)
    assert core.listing("jadippg")["meta"]["sample"] is True
    core.refresh("jadippg", runner=Runner(), now=2.0)
    assert core.listing("jadippg")["meta"]["sample"] is False


def test_refresh_rejects_unknown_platform_and_concurrent_runs():
    with pytest.raises(KeyError):
        core.refresh("xx", runner=Runner())
    assert core._refresh_lock.acquire(blocking=False)
    try:
        with pytest.raises(core.CoreBusy):
            core.refresh("jadippg", runner=Runner())
        with pytest.raises(core.CoreBusy):
            core.start_refresh("jadippg", runner=Runner())
    finally:
        core._refresh_lock.release()


def test_background_job_reports_progress_and_result():
    seen = []

    class Slow(Runner):
        def __call__(self, seed, geo, demo=False):
            seen.append(dict(core._job))      # keadaan job di tengah proses
            return super().__call__(seed, geo, demo)
    job = core.start_refresh("jadippg", runner=Slow())
    assert job["running"] is True and job["total"] == 5
    core._job_thread.join(timeout=10)
    end = core.job_state("jadippg")
    assert end["running"] is False and end["result"]["diperbarui"] == 5 and end["done"] == 5 and end["error"] == ""
    assert seen and all(s["running"] for s in seen) and seen[0]["platform"] == "jadippg"
    assert core.job_state("jadiasn") is None


def test_background_job_surfaces_unexpected_errors():
    def boom(seed, geo, demo=False):
        raise RuntimeError("rusak")
    core.start_refresh("jadippg", runner=boom)
    core._job_thread.join(timeout=10)
    assert core.job_state("jadippg")["running"] is False and "rusak" in core.job_state("jadippg")["error"]


def test_real_research_in_demo_mode_fills_a_platform_end_to_end():
    out = core.refresh("jadippg", demo=True, now=5.0)
    assert out["diperbarui"] == 5 and out["gagal"] == 0
    d = core.listing("jadippg", now=6.0)
    assert d["meta"]["sample"] is True and all(i["status"] == "ok" and len(i["items"]) > 10 for i in d["items"])
    top = d["items"][0]["items"][0]
    assert {"query", "rank", "maksud_label", "sumber", "naik_24j", "naik_7h", "naik_90h", "baru"} <= set(top)


# --------------------------------------------------------------------------- API & CLI


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c


def test_api_core_list_and_background_refresh(client):
    r = client.get("/api/platforms/jadippg/inti").json()
    assert r["platform"] == "jadippg" and len(r["items"]) == 5 and r["meta"]["updated_at"] is None
    started = client.post("/api/platforms/jadippg/inti/refresh", params={"demo": "true"}).json()
    assert started["job"]["total"] == 5
    core._job_thread.join(timeout=30)
    d = client.get("/api/platforms/jadippg/inti").json()
    assert d["meta"]["ok"] == 5 and d["meta"]["sample"] is True and d["meta"]["job"]["running"] is False
    assert client.get("/api/platforms/xx/inti").status_code == 404
    assert client.post("/api/platforms/xx/inti/refresh").status_code == 404


def test_cli_core_refresh_demo(capsys):
    from app import cli
    assert cli.main(["core-refresh", "--platform", "jadippg", "--demo"]) == 0
    assert "jadippg: 5 diperbarui" in capsys.readouterr().out
    assert cli.main(["core-refresh", "--platform", "tidak-ada"]) == 2


# --------------------------------------------------------------------------- Trends per keyword + YouTube/Bing


def day(v, partial=False):
    return {"label": "x", "values": [v], "partial": partial}


def test_timeline_gains_computes_real_changes_and_ignores_unfinished_day():
    series = [10] * 83 + [20] * 7
    tl = [day(v) for v in series] + [day(1, partial=True)]
    g = core.timeline_gains(tl)
    assert g["naik_24j"] is None            # 20 vs 20: tidak naik
    assert g["naik_7h"]["value"] == 100 and g["naik_7h"]["label"] == "+100%"
    assert g["naik_90h"]["value"] == 100    # 20 vs 10 di awal
    assert core.timeline_gains([day(5)] * 5) is None       # data terlalu sedikit
    assert core.timeline_gains([day(0)] * 90) is None      # tidak pernah dicari


def test_enrich_marks_rising_keywords_and_reorders():
    class P:
        name = "x"
        def __init__(self): self.calls = []
        def explore(self, kws, geo, cat, tr, gprop="", parts=()):
            self.calls.append(kws[0])
            rising = kws[0] == "b"
            vals = [10] * 76 + [50] * 14 if rising else [10] * 90
            return {"timeline": [day(v) for v in vals]}
    res = {"items": [item("a", 1), item("b", 2), item("c", 3)]}
    out = core.enrich_with_trends(res, "ID", provider=P())
    assert [r["query"] for r in out["items"]][0] == "b"
    assert out["items"][0]["naik_90h"] and out["trends_cek"] == {"dicek": 3, "naik": 1}


def test_enrich_stops_after_repeated_failures_and_keeps_result():
    class Bad:
        name = "x"
        n = 0
        def explore(self, *a, **k):
            Bad.n += 1
            raise ExploreError("429")
    res = {"items": [item(str(i), i) for i in range(8)]}
    out = core.enrich_with_trends(res, "ID", provider=Bad())
    assert Bad.n == core.TREND_FAIL_STREAK and len(out["items"]) == 8
    assert out["errors"][0]["scope"].startswith("Google Trends")


def test_default_runner_uses_youtube_and_bing(monkeypatch):
    seen = {}
    def fake(seed, geo, depth, **kw):
        seen.update(kw); return {"seed": seed, "items": [], "sumber": {"saran": "google"}, "errors": []}
    monkeypatch.setattr(core.keyword_research, "run_keywords", fake)
    monkeypatch.setattr(core, "related_with_fallback", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(core, "related_of_derived", lambda *a, **k: [])
    core._default_runner("CPNS 2026", "ID")
    assert seen["channels"] == ("youtube", "bing")


def test_trends_related_is_saved_per_window_for_display(monkeypatch, tmp_path):
    monkeypatch.setenv("TRENDWATCH_TRENDS_WEB", str(tmp_path / "trends_web.json"))
    terkait = {"24j": [{"query": "formasi cpns 2026", "kind": "populer", "label": "100", "value": 100, "breakout": False}],
               "7h": [], "90h": []}
    def runner(seed, geo, demo=False):
        return {"seed": seed, "items": [item("x", 1)], "sumber": {"saran": "google"}, "errors": [], "trends_terkait": terkait}
    core.refresh("jadippg", runner=runner)
    first = core.listing("jadippg")["items"][0]
    assert first["summary"]["terkait"]["24j"][0]["query"] == "formasi cpns 2026" and first["summary"]["terkait"]["7h"] == []


class RawProvider:
    """Jawaban Google apa adanya: urutan, label, dan kata seed sendiri."""
    name = "fake"

    def __init__(self, fail_on=()):
        self.fail_on, self.calls = fail_on, []

    def explore(self, kws, geo, cat, tr, gprop="", parts=()):
        self.calls.append(tr)
        if tr in self.fail_on:
            raise ExploreError("429")
        return {"related": {kws[0]: {
            "top": [{"query": "formasi cpns", "value": 100, "label": "100"}, {"query": "cpns 2026", "value": 90, "label": "90"}],
            "rising": [{"query": "cpns 2026", "value": 130, "label": "+130%"},          # sama dengan seed: tetap ikut
                       {"query": "sccasn", "value": 5000, "label": "Pesat"},
                       {"query": "manggala agni", "value": 40, "label": "+40%"}]}}}


def test_trends_related_is_google_verbatim_without_filtering():
    out = core.trends_related("cpns 2026", "ID", provider=RawProvider())
    assert set(out) == {"24j", "7h", "90h"}
    rows = out["24j"]
    assert [(r["query"], r["kind"], r["label"]) for r in rows] == [
        ("cpns 2026", "naik", "+130%"), ("sccasn", "naik", "Pesat"), ("manggala agni", "naik", "+40%"),
        ("formasi cpns", "populer", "100"), ("cpns 2026", "populer", "90")]           # urutan Google, seed ikut
    assert [r["breakout"] for r in rows[:3]] == [False, True, False]


def test_trends_related_marks_failed_windows_as_none_not_empty():
    out = core.trends_related("cpns 2026", "ID", provider=RawProvider(fail_on=("now 7-d",)))
    assert out["7h"] is None and len(out["24j"]) == 5


class EmptyThenBroader(RawProvider):
    """Seed spesifik dikosongkan Google; kata yang lebih umum punya data."""
    def explore(self, kws, geo, cat, tr, gprop="", parts=()):
        self.calls.append((kws[0], tr))
        if kws[0] == "pcpm bi":
            return {"related": {"pcpm bi": {"top": [{"query": "soal pcpm bi", "value": 100, "label": "100"}], "rising": []}}}
        return {"related": {kws[0]: {"top": [], "rising": []}}}


def test_broader_terms():
    assert core.broader_terms("bimbel pcpm bi") == ["pcpm bi", "bimbel pcpm"]
    assert core.broader_terms("lpdp") == [] and core.broader_terms("tes toefl") == ["toefl"]  # "tes" terlalu pendek


def test_empty_windows_fall_back_to_broader_term_and_are_marked():
    out, dari = core.related_with_fallback("bimbel pcpm bi", "ID", provider=EmptyThenBroader())
    assert dari == {"24j": "pcpm bi", "7h": "pcpm bi", "90h": "pcpm bi"}
    assert out["24j"][0]["query"] == "soal pcpm bi"


def test_failed_windows_are_not_replaced_by_fallback():
    out, dari = core.related_with_fallback("bimbel pcpm bi", "ID", provider=RawProvider(fail_on=("now 1-d", "now 7-d", "today 3-m")))
    assert out == {"24j": None, "7h": None, "90h": None} and dari == {}


def test_related_of_derived_collects_google_rows_per_keyword():
    items = [item("cpns 2026", 1), {**item("soal cpns 2026", 2), "skor": 50}, {**item("formasi cpns 2026", 3), "skor": 80}]
    out = core.related_of_derived(items, "cpns 2026", "ID", provider=RawProvider(), limit=2)
    assert [(o["dari"], o["w"]) for o in out] == [("formasi cpns 2026", "7h"), ("formasi cpns 2026", "90h"),
                                                    ("soal cpns 2026", "7h"), ("soal cpns 2026", "90h")]
    assert out[0]["rows"][0]["query"] == "cpns 2026"


def test_listing_uses_full_trends_web_data_when_available(tmp_path, monkeypatch):
    from app import trends_web as tw
    monkeypatch.setenv("TRENDWATCH_TRENDS_WEB", str(tmp_path / "w.json"))
    core.refresh("jadippg", runner=lambda seed, geo, demo=False: {
        "seed": seed, "items": [item("x", 1)], "sumber": {"saran": "google"}, "errors": [],
        "trends_terkait": {"24j": [{"query": "lama", "kind": "naik", "label": "+1%", "value": 1, "breakout": False}], "7h": [], "90h": []},
        "trends_dari": {"24j": "ppg"}})
    before = core.listing("jadippg")["items"][0]["summary"]
    assert before["terkait"]["24j"][0]["query"] == "lama" and "terkait_web" not in before
    seed = core.keywords("jadippg")[0]
    tw.write({tw.key("jadippg", seed): {"24j": {"rising": [{"query": "baru", "change": 40, "interest": 9}], "top": [], "diambil": 5.0}}})
    after = core.listing("jadippg")["items"][0]["summary"]
    assert after["terkait"]["24j"][0]["query"] == "baru" and after["terkait_web"] == 5.0
    assert after["terkait"]["7h"] == [] and "24j" not in after["terkait_dari"]   # kurun lain tetap dari jalur lama
