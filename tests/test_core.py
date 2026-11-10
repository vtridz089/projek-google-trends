"""Tes keyword inti per platform: daftar, perhitungan kenaikan, penyimpanan, kegagalan, dan API."""
import pytest

from app import core, db, platforms
from app.explore import ExploreError


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "c.db"))
    db.init_db()
    core.init()


def tl(values, partial_last=False):
    rows = [{"t": i, "label": f"hari {i + 1}", "values": [v], "partial": False} for i, v in enumerate(values)]
    if partial_last:
        rows[-1]["partial"] = True
    return rows


class Fake:
    name = "fake"

    def __init__(self, series=None, fail=()):
        self.series, self.fail, self.calls = series or {}, set(fail), []

    def explore(self, keywords, geo, category, time_range, gprop="", parts=()):
        kw = keywords[0]
        self.calls.append((kw, time_range))
        if kw in self.fail:
            raise ExploreError("Google menolak (429)")
        return {"timeline": tl(self.series.get(kw, [10] * 20 + [20] * 7)), "source": "fake"}


# --------------------------------------------------------------------------- daftar


def test_core_keywords_match_the_list_given_by_the_client():
    assert set(core.CORE) == {p.slug for p in platforms.PLATFORMS}
    assert {s: len(v) for s, v in core.CORE.items()} == {
        "jadiasn": 11, "jadipppk": 6, "jadibumn": 8, "jadipcpm": 5, "jadiojk": 5, "psikoteskerja": 5, "jadicerebrum": 9,
        "toeflacademy": 5, "jadibeasiswa": 6, "jadipolisi": 8, "jadiprajurit": 6, "jadisekdin": 5, "jagotpa": 5,
        "jadippg": 5, "jagoukp": 7}
    assert sum(len(v) for v in core.CORE.values()) == 96
    assert core.keywords("jagoukp")[:2] == ("UKP", "UJIAN KOMPETENSI PERPAJAKAN") and core.keywords("xx") == ()


# --------------------------------------------------------------------------- perhitungan


def test_change_handles_zero_base():
    assert core.change(150, 100) == {"value": 50, "label": "+50%", "breakout": False}
    assert core.change(50, 100)["label"] == "-50%"
    assert core.change(5, 0) == {"value": None, "label": "Baru muncul", "breakout": True}
    assert core.change(0, 0) is None


def test_metrics_windows_and_partial_day_dropped():
    series = [10] * 83 + [20] * 6 + [40]            # 90 hari lengkap + satu hari
    m = core.metrics(tl(series + [1], partial_last=True))  # hari terakhir (1) belum selesai -> diabaikan
    assert m["status"] == "ok" and m["interest"] == 40 and len(m["series"]) == 90
    assert m["ch_1d"]["value"] == 100                     # 40 vs 20
    assert m["ch_7d"]["value"] == round((((20 * 6) + 40) / 7 - 10) / 10 * 100)
    assert m["ch_90d"]["value"] == round((((20 * 6) + 40) / 7 - 10) / 10 * 100)


def test_metrics_declines_and_few_data():
    down = core.metrics(tl([100] * 20 + [50] * 7))
    assert down["ch_7d"]["value"] < 0 and down["ch_7d"]["label"].startswith("-")
    assert core.metrics(tl([5, 6, 7]))["status"] == "sedikit"
    assert core.metrics(tl([0] * 30))["status"] == "sedikit"
    assert core.metrics([])["status"] == "sedikit"


# --------------------------------------------------------------------------- refresh dan simpan


def test_refresh_stores_and_listing_reads_back():
    f = Fake({"UKP": [10] * 20 + [30] * 7})
    r = core.refresh("jagoukp", provider=f, now=1000.0)
    assert r == {"diperbarui": 7, "gagal": 0, "dilewati": 0, "errors": []}
    assert [c[0] for c in f.calls] == list(core.keywords("jagoukp")) and all(c[1] == "today 3-m" for c in f.calls)
    out = core.listing("jagoukp", now=1000.0 + 60)
    ukp = out["items"][0]
    assert ukp["keyword"] == "UKP" and ukp["status"] == "ok" and ukp["interest"] == 30 and ukp["ch_7d"]["breakout"] is False
    assert out["meta"]["ok"] == 7 and out["meta"]["total"] == 7 and out["meta"]["stale"] is False and out["meta"]["sample"] is False
    assert core.listing("jagoukp", now=1000.0 + 25 * 3600)["meta"]["stale"] is True


def test_listing_before_any_refresh_is_all_unfetched():
    out = core.listing("jadippg")
    assert [i["status"] for i in out["items"]] == ["belum"] * 5 and out["meta"]["updated_at"] is None and out["meta"]["stale"]


def test_failure_keeps_previous_good_data_and_stops_after_repeated_blocks():
    core.refresh("jadippg", provider=Fake(), now=1.0)
    bad = Fake(fail=core.keywords("jadippg"))
    r = core.refresh("jadippg", provider=bad, now=2.0)
    assert r["gagal"] == core.MAX_CONSECUTIVE_FAILS and r["dilewati"] == 5 - core.MAX_CONSECUTIVE_FAILS and len(bad.calls) == 2
    items = core.listing("jadippg")["items"]
    assert all(i["status"] == "ok" for i in items)                    # data lama tetap
    assert "429" in items[0]["error"] and items[2]["error"] == ""      # galat dicatat hanya pada yang gagal


def test_failure_without_previous_data_is_marked_failed_and_other_keywords_still_work():
    f = Fake(fail=["SOAL PPG"])
    r = core.refresh("jadippg", provider=f, now=1.0)
    assert (r["diperbarui"], r["gagal"]) == (4, 1)
    by = {i["keyword"]: i for i in core.listing("jadippg")["items"]}
    assert by["SOAL PPG"]["status"] == "gagal" and "429" in by["SOAL PPG"]["error"] and by["PPG"]["status"] == "ok"


def test_sample_data_is_flagged_and_replaced_by_real_data():
    class Mock(Fake):
        def explore(self, *a, **k):
            r = super().explore(*a, **k); r["source"] = "mock"; return r
    core.refresh("jadippg", provider=Mock(), now=1.0)
    assert core.listing("jadippg")["meta"]["sample"] is True
    core.refresh("jadippg", provider=Fake(), now=2.0)
    assert core.listing("jadippg")["meta"]["sample"] is False


def test_few_data_keyword_is_reported_as_such():
    r = core.refresh("jadippg", provider=Fake({"PPG": [0] * 30}), now=1.0)
    assert r["diperbarui"] == 5
    assert core.listing("jadippg")["items"][0]["status"] == "sedikit"


def test_refresh_rejects_unknown_platform_and_concurrent_runs():
    with pytest.raises(KeyError):
        core.refresh("xx", provider=Fake())
    assert core._refresh_lock.acquire(blocking=False)
    try:
        with pytest.raises(core.CoreBusy):
            core.refresh("jadippg", provider=Fake())
    finally:
        core._refresh_lock.release()


def test_demo_mode_uses_mock_provider_and_fills_every_keyword():
    r = core.refresh("jadiasn", demo=True, now=5.0)
    assert r["diperbarui"] == 11 and r["gagal"] == 0
    out = core.listing("jadiasn", now=6.0)
    assert out["meta"]["sample"] is True and out["meta"]["ok"] + sum(1 for i in out["items"] if i["status"] == "sedikit") == 11
    assert all(len(i["series"]) > 20 for i in out["items"])


# --------------------------------------------------------------------------- API


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c


def test_api_core_list_and_demo_refresh(client):
    r = client.get("/api/platforms/jadibumn/inti").json()
    assert r["platform"] == "jadibumn" and len(r["items"]) == 8 and r["meta"]["updated_at"] is None
    done = client.post("/api/platforms/jadibumn/inti/refresh", params={"demo": "true"}).json()
    assert done["hasil"]["diperbarui"] == 8 and done["meta"]["sample"] is True and done["items"][0]["keyword"] == "RBB BUMN"
    assert client.get("/api/platforms/jadibumn/inti").json()["meta"]["ok"] >= 1
    assert client.get("/api/platforms/xx/inti").status_code == 404
    assert client.post("/api/platforms/xx/inti/refresh").status_code == 404


def test_cli_core_refresh_demo(capsys):
    from app import cli
    assert cli.main(["core-refresh", "--platform", "jadippg", "--demo"]) == 0
    assert "jadippg: 5 diperbarui" in capsys.readouterr().out
    assert cli.main(["core-refresh", "--platform", "tidak-ada"]) == 2
