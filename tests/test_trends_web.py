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
