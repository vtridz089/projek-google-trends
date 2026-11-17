import io
import json

import pytest

from app import serpapi_trends as sp
from app import trends_runner, trends_web


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(payload):
    return lambda url, timeout=0: Resp(json.dumps(payload).encode())


PAYLOAD = {"related_queries": {
    "rising": [{"query": "soal cpns 2026", "value": "+3,200%", "extracted_value": 3200},
               {"query": "link ujian", "value": "Breakout", "extracted_value": 5000}],
    "top": [{"query": "soal cpns pdf", "value": "100", "extracted_value": 100}]}}


def test_parse_menyamai_bentuk_trends_web():
    r = sp.parse(PAYLOAD)
    assert r["rising"][0] == {"query": "soal cpns 2026", "change": 3200, "interest": 0}
    assert r["rising"][1]["change"] == 5000
    assert r["top"][0] == {"query": "soal cpns pdf", "interest": 100, "change": 0}


def test_batas_pencarian():
    f = sp.Fetcher(limit=1, opener=opener_for(PAYLOAD), key="k")
    assert f.fetch("a", "now 7-d")["top"]
    with pytest.raises(sp.QuotaReached):
        f.fetch("b", "now 7-d")


def test_tanpa_kunci(monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    with pytest.raises(trends_runner.RunnerError):
        sp.api_key()


def test_collect_satu_rentang(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_TRENDS_WEB", str(tmp_path / "t.json"))
    monkeypatch.setattr(trends_web, "path", lambda: str(tmp_path / "t.json"))
    f = sp.Fetcher(limit=3, opener=opener_for(PAYLOAD), key="k")
    with pytest.raises(sp.QuotaReached):
        trends_runner.collect(["jadiasn"], f.fetch, windows=[("7h", "now 7-d")], sleep=lambda s: None)
    data = trends_web.load()
    assert data and all(set(e) == {"7h"} for e in data.values())
