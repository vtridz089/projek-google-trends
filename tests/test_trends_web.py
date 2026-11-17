"""Parser jawaban antarmuka Trends baru (fXqlme), memakai jawaban asli yang ditangkap dari browser."""
import json

import pytest

from app.trends_web import WebFormatError, format_change, parse_fxqlme

INNER = [[["soal cpns",
           [["sscasn bkn", 110, 3], ["latihan soal cpns 2026 gratis", 110, 5], ["soal tes cpns", -7, 40], ["jenis soal cpns", -50, 2]],
           [["soal cpns 2026", 100, 6], ["cpns 2026", 100, 7], ["soal tiu cpns", 43, 8]]]]]
BODY = ")]}'\n\n123\n" + json.dumps([["wrb.fr", "fXqlme", json.dumps(INNER), None, None, None, "generic"]]) + "\n59\n" + \
    json.dumps([["di", 318], ["af.httprm", 318, "-8168647297798538669", 247]]) + "\n26\n" + json.dumps([["e", 4, None, None, 1849]])


def test_parses_rising_and_top_with_change_and_interest():
    r = parse_fxqlme(BODY)
    assert r["term"] == "soal cpns"
    assert r["rising"][0] == {"query": "sscasn bkn", "change": 110, "interest": 3}      # rising: [query, perubahan, minat]
    assert r["top"][0] == {"query": "soal cpns 2026", "interest": 100, "change": 6}      # top: [query, minat, perubahan]
    assert [x["query"] for x in r["rising"]] == ["sscasn bkn", "latihan soal cpns 2026 gratis", "soal tes cpns", "jenis soal cpns"]


def test_rejects_unknown_or_missing_shapes_instead_of_guessing():
    with pytest.raises(WebFormatError):
        parse_fxqlme(")]}'\n\n5\n" + json.dumps([["di", 1]]))
    bad = ")]}'\n\n9\n" + json.dumps([["wrb.fr", "fXqlme", json.dumps({"x": 1}), None]])
    with pytest.raises(WebFormatError):
        parse_fxqlme(bad)


def test_format_change_matches_trends_site():
    assert format_change(130) == "+130%" and format_change(-7) == "-7%" and format_change(0) == "+0%"


def _parsed():
    return {"term": "soal cpns",
            "rising": [{"query": "sscasn bkn", "change": 5000, "interest": 3}, {"query": "bank soal cpns", "change": 90, "interest": 5},
                       {"query": "jenis soal cpns", "change": -50, "interest": 2}],
            "top": [{"query": "soal cpns 2026", "interest": 100, "change": 6}]}


def test_panel_rows_keep_google_order_and_label_breakout():
    from app.trends_web import panel_rows
    rows = panel_rows(_parsed())
    assert [(r["query"], r["kind"], r["label"]) for r in rows] == [
        ("sscasn bkn", "naik", "Breakout"), ("bank soal cpns", "naik", "+90%"), ("jenis soal cpns", "naik", "-50%"),
        ("soal cpns 2026", "populer", "100")]
    assert rows[0]["breakout"] and not rows[1]["breakout"] and rows[3]["change"] == 6


def test_file_roundtrip_and_missing_or_broken_file(tmp_path, monkeypatch):
    from app import trends_web as tw
    p = tmp_path / "w.json"
    monkeypatch.setenv("TRENDWATCH_TRENDS_WEB", str(p))
    assert tw.load() == {} and tw.rows_for("jadiasn", "cpns 2026") == ({}, None)
    tw.write({tw.key("jadiasn", "CPNS 2026"): {"24j": {**_parsed_lists(), "diambil": 1000.0}}})
    rows, at = tw.rows_for("jadiasn", "CPNS 2026")
    assert at == 1000.0 and len(rows["24j"]) == 4 and "7h" not in rows
    p.write_text("{rusak", encoding="utf-8")
    import os; os.utime(p, (os.path.getmtime(p) + 5, os.path.getmtime(p) + 5))
    assert tw.load() == {}


def _parsed_lists():
    d = _parsed()
    return {"rising": d["rising"], "top": d["top"]}


# --------------------------------------------------------------------------- pengambilan lewat Chrome (app/trends_runner.py)

def _fake_fetch_factory(rising_n=30, calls=None):
    def fetch(term, tr):
        if calls is not None:
            calls.append((term, tr))
        return {"term": term, "rising": [{"query": f"{term} r{i}", "change": 100 - i, "interest": 5} for i in range(rising_n)],
                "top": [{"query": f"{term} t", "interest": 100, "change": 1}]}
    return fetch


@pytest.fixture
def runner(tmp_path, monkeypatch):
    from app import trends_runner
    monkeypatch.setenv("TRENDWATCH_TRENDS_WEB", str(tmp_path / "w.json"))
    monkeypatch.setattr(trends_runner, "_job", {"running": False})
    return trends_runner


def test_collect_saves_after_each_topic_and_skips_fresh_ones(runner):
    from app import trends_web as tw
    calls = []
    res = runner.collect(["jagoukp"], _fake_fetch_factory(calls=calls), sleep=lambda s: None, now=lambda: 1000.0)
    assert res == {"diambil": 21, "gagal": 0, "dilewati": 0} and len(calls) == 21           # 7 topik x 3 kurun
    rows, at = tw.rows_for("jagoukp", "UKP")
    assert at == 1000.0 and set(rows) == {"24j", "7h", "90h"} and len(rows["24j"]) == 31
    calls.clear()
    res = runner.collect(["jagoukp"], _fake_fetch_factory(calls=calls), sleep=lambda s: None, now=lambda: 2000.0)
    assert res["dilewati"] == 7 and calls == []                                            # masih segar
    res = runner.collect(["jagoukp"], _fake_fetch_factory(calls=calls), sleep=lambda s: None, now=lambda: 2000.0, ulangi=True)
    assert res["diambil"] == 21


def test_failed_fetch_keeps_old_data(runner):
    from app import trends_web as tw
    runner.collect(["jagoukp"], _fake_fetch_factory(), sleep=lambda s: None, now=lambda: 1000.0)
    res = runner.collect(["jagoukp"], lambda t, tr: None, sleep=lambda s: None, now=lambda: 999999.0, ulangi=True)
    assert res["gagal"] == 21 and res["diambil"] == 0
    assert tw.rows_for("jagoukp", "UKP")[1] == 1000.0          # data lama tetap


def test_login_check_detects_limited_list(runner):
    runner.check_login(_fake_fetch_factory(rising_n=30))
    with pytest.raises(runner.LoginNeeded):
        runner.check_login(_fake_fetch_factory(rising_n=3))
    with pytest.raises(runner.RunnerError):
        runner.check_login(lambda t, tr: None)


def test_job_runs_with_progress_and_reports_login_needed(runner, monkeypatch):
    class F:
        def __init__(self, n): self.fetch = _fake_fetch_factory(rising_n=n)
        def close(self): pass
    runner.start_job("jagoukp", fetcher_factory=lambda: F(3), ensure=lambda: None, background=False)
    st = runner.job_state()
    assert st["running"] is False and st["perlu_login"] is True and "login" in st["error"].lower()
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    runner.start_job("jagoukp", fetcher_factory=lambda: F(30), ensure=lambda: None, background=False)
    st = runner.job_state()
    assert st["error"] is None and st["result"]["diambil"] == 21 and st["done"] == st["total"] == 7


def test_chrome_path_env_and_availability(runner, monkeypatch, tmp_path):
    fake = tmp_path / "chrome.exe"
    fake.write_text("x")
    monkeypatch.setenv("TRENDWATCH_CHROME", str(fake))
    assert runner.chrome_path() == str(fake) and runner.available()["chrome"] is True
    monkeypatch.setenv("TRENDWATCH_CHROME", str(tmp_path / "tidak-ada.exe"))
    assert runner.chrome_path() is None


def test_ensure_chrome_launches_once_and_reports_missing(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "cdp_ready", lambda port=9222: False)
    monkeypatch.setenv("TRENDWATCH_CHROME", str(tmp_path / "tidak-ada.exe"))
    with pytest.raises(runner.RunnerError, match="Chrome tidak ditemukan"):
        runner.ensure_chrome()
    fake = tmp_path / "chrome.exe"; fake.write_text("x")
    monkeypatch.setenv("TRENDWATCH_CHROME", str(fake))
    monkeypatch.setenv("TRENDWATCH_CHROME_PROFILE", str(tmp_path / "profil"))
    launched = []
    state = {"n": 0}
    def ready(port=9222):
        state["n"] += 1
        return state["n"] > 2          # siap setelah beberapa pengecekan
    monkeypatch.setattr(runner, "cdp_ready", ready)
    runner.ensure_chrome(launcher=lambda cmd: launched.append(cmd), wait=5)
    assert len(launched) == 1 and "--remote-debugging-port=9222" in launched[0] and any("profil" in a for a in launched[0])
