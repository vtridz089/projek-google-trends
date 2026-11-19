"""Penyimpanan dan panel data Trends lengkap (``app/trends_web.py``) dan pengumpul (``app/trends_runner.py``)."""
import pytest

from app.trends_web import format_change


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
