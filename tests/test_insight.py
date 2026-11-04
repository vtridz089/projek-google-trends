"""Tes analisis tambahan: maksud pencarian, skor peluang, kelompok keyword."""
import pytest

from app import db, explore_service, insight
from app.explore_service import finalize_research, merge_research


@pytest.mark.parametrize("kw, expected", [
    ("harga bbm hari ini", "transaksi"),     # transaksi menang atas berita
    ("beli saham bumn", "transaksi"),
    ("login mybumn", "navigasi"),
    ("aplikasi bumn", "navigasi"),
    ("syarat cpns bumn", "informasi"),
    ("daftar bumn", "informasi"),
    ("bumn terbaru", "berita"),
    ("bumn", "umum"),
    ("apple watch", "umum"),                 # "app" tidak boleh cocok di dalam "apple"
    ("linkedin", "umum"),                    # "link" tidak boleh cocok di dalam "linkedin"
])
def test_intent_rules(kw, expected):
    assert insight.intent(kw) == expected


def row(rising=4, value=300, breakout=False, pattern="konsisten", belum=None, turunan=False):
    return {"rising_count": rising, "max_value": value, "breakout": breakout, "pattern": pattern,
            "belum_pernah": belum, "turunan": turunan}


def test_opportunity_maximum_and_parts_sum():
    o = insight.opportunity(row(breakout=True, belum=True, turunan=True, pattern="baru"))
    assert o["skor"] == 40 + 25 + 15 + 10 + 10 == 100 and o["level"] == "tinggi"
    assert sum(o["bagian"].values()) == o["skor"]


def test_opportunity_known_example_and_levels():
    o = insight.opportunity(row(rising=1, value=300, pattern="baru"))
    assert o["bagian"] == {"pertumbuhan": 27, "konsistensi": 6, "dini": 15, "baru": 0, "relevan": 0}
    assert o["skor"] == 48 and o["level"] == "sedang"
    assert insight.opportunity(row(rising=1, value=50, pattern="naik"))["level"] == "rendah"


def test_opportunity_zero_for_popular_only_and_grows_with_value():
    assert insight.opportunity(row(rising=0, pattern="populer"))["skor"] == 0
    low = insight.opportunity(row(value=100))["skor"]
    high = insight.opportunity(row(value=2000))["skor"]
    assert high > low


def test_history_bonus_only_when_never_saved():
    assert insight.opportunity(row(belum=True))["skor"] - insight.opportunity(row(belum=False))["skor"] == 10
    assert insight.opportunity(row(belum=None))["skor"] == insight.opportunity(row(belum=False))["skor"]


def _c(q, skor):
    return {"query": q, "peluang": {"skor": skor}}


def test_clusters_group_by_shared_modifier_and_sort():
    rows = [_c("bumn 2026", 50), _c("bumn 2026 gratis", 80), _c("bumn login", 30), _c("bumn gratis", 60),
            _c("danantara", 90)]
    got = insight.clusters(rows, "bumn")
    names = [g["nama"] for g in got]
    assert names[-1] == "Lainnya" and set(names) == {"2026", "gratis", "Lainnya"}
    by = {g["nama"]: g for g in got}
    # "bumn 2026 gratis" punya dua kata bersama (2026 dan gratis, masing-masing dipakai 2 keyword): tidak ganda
    assert sum(g["ukuran"] for g in got) == len(rows)
    assert "danantara" in by["Lainnya"]["keywords"] and "bumn login" in by["Lainnya"]["keywords"]


def test_clusters_ignore_topic_words_and_stopwords():
    got = insight.clusters([_c("bumn yang baru", 1), _c("bumn yang lama", 1)], "bumn")
    assert [g["nama"] for g in got] == ["Lainnya"]       # "yang" dan "bumn" bukan kata pembeda


def I(q, kind="naik", v=300, b=False):
    return {"query": q, "kind": kind, "value": v, "breakout": b, "label": "x"}


def test_finalize_adds_everything_and_counts_days():
    merged = merge_research("bumn", {"now 1-d": [I("bumn 2026", v=6000, b=True), I("bumn login")],
                                     "now 7-d": [I("bumn 2026"), I("danantara")]})
    res = {"topic": "bumn", "items": merged}
    out = finalize_research(res, {"bumn 2026": 3}, 5)
    by = {r["query"]: r for r in out["items"]}
    assert by["bumn 2026"]["hari_tersimpan"] == 3 and by["bumn 2026"]["belum_pernah"] is False
    assert by["danantara"]["hari_tersimpan"] == 0 and by["danantara"]["belum_pernah"] is True
    assert by["bumn login"]["maksud"] == "navigasi" and by["bumn 2026"]["peluang"]["skor"] > by["bumn login"]["peluang"]["skor"]
    assert all(r["kelompok"] for r in out["items"]) and out["history_days"] == 5


def test_finalize_without_history_keeps_flags_none():
    out = finalize_research({"topic": "t", "items": merge_research("t", {"now 7-d": [I("t a")]})}, {}, 0)
    assert out["items"][0]["belum_pernah"] is None and out["items"][0]["hari_tersimpan"] is None


def test_history_counts_per_keyword(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "h.db"))
    db.init_db(); db.discovery_init()
    w = db.watchlist_add("bumn", "ID", 0)
    for day in ("2026-11-01", "2026-11-02", "2026-11-03"):
        db.discovery_save(w["id"], day, [{"keyword": "Danantara", "source": "research"}])
    db.discovery_save(w["id"], "2026-11-02", [{"keyword": "lain", "source": "research"}])
    counts, days = db.discovery_history_counts("bumn", "ID", 0, "2026-11-03")
    assert counts == {"danantara": 2, "lain": 1} and days == 2
    assert db.discovery_history_counts("bumn", "ID", 0, "2026-11-04")[0]["danantara"] == 3
