"""Tes pemantauan harian: CSV Watchlist/discovery dan perintah `python -m app.cli discover`."""
import csv
from datetime import date

import pytest

from app import cli, db, explore_service
from app.explore import ExploreError


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "d.db"))
    monkeypatch.setenv("TRENDWATCH_WATCHLIST_CSV", str(tmp_path / "watchlist.csv"))
    monkeypatch.setenv("TRENDWATCH_DISCOVERY_CSV", str(tmp_path / "discovery.csv"))
    db.init_db(); db.discovery_init()
    return tmp_path


class Fake:
    """Google tiruan: hasil tetap per topik; ``fail`` = topik yang selalu ditolak, ``fail_windows`` = rentang yang gagal."""
    def __init__(self, fail=(), fail_windows=()):
        self.calls, self.fail, self.fail_windows = [], set(fail), set(fail_windows)

    def explore(self, keywords, geo, category, time_range, gprop="", parts=()):
        k = keywords[0]
        self.calls.append((k, time_range))
        if k in self.fail or (k, time_range) in self.fail_windows:
            raise ExploreError("Google menolak (429).")
        rising = [{"query": f"{k} a", "value": 400, "label": "+400%"},
                  {"query": f"{k} b", "value": 5000, "label": "Breakout"}]
        return {"source": "google", "fetched_at": "x", "notes": [],
                "related": {k: {"rising": rising, "top": [{"query": f"{k} pop", "value": 9}]}}}


def run(monkeypatch, fake, *argv, day="2026-11-04"):
    monkeypatch.setattr(explore_service, "get_provider", lambda demo=False: fake)
    monkeypatch.setattr(cli, "today_local", lambda: date.fromisoformat(day))
    import app.explore_service as svc
    monkeypatch.setattr(svc, "today_local", lambda: date.fromisoformat(day), raising=False)
    return cli.main(["discover", "--refresh", *argv])


def rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- CSV


def test_csv_round_trip_keeps_status_score_and_is_idempotent(env):
    w = db.watchlist_add("bumn", "ID", 0)
    db.discovery_save(w["id"], "2026-11-01", [
        {"keyword": "bumn a", "source": "research", "trend_category": "trending", "interest_score": 5000,
         "opportunity_score": 77.5, "trend_change": "Breakout"}])
    cid = db.discovery_list(5)[0]["id"]
    db.discovery_set_status(cid, "Writing")
    wp, dp = str(env / "w.csv"), str(env / "d.csv")
    assert db.discovery_dump_csv(wp, dp) == (1, 1)

    other = env / "baru"; other.mkdir()
    import os
    os.environ["TRENDWATCH_DB"] = str(other / "x.db")
    db.init_db(); db.discovery_init()
    assert db.discovery_import_csv(wp, dp) == {"watchlist": 1, "discovery": 1, "dilewati": 0}
    got = db.discovery_list(5)[0]
    assert (got["keyword"], got["status"], got["opportunity_score"], got["interest_score"]) == ("bumn a", "Writing", 77.5, 5000)
    assert db.discovery_import_csv(wp, dp) == {"watchlist": 0, "discovery": 0, "dilewati": 0}   # diulang = tidak ganda
    assert len(db.discovery_list(50)) == 1 and len(db.watchlist_list()) == 1


def test_import_does_not_overwrite_local_changes_and_skips_bad_rows(env):
    w = db.watchlist_add("bumn", "ID", 0)
    db.watchlist_set_active(w["id"], False)                       # perubahan lokal
    wp = env / "w.csv"
    wp.write_text("topic,geo,category,is_active\nbumn,ID,0,1\n,ID,0,1\nbagus,XYZ,0,1\nok,MY,abc,1\npemilu,ID,0,0\n", encoding="utf-8")
    out = db.discovery_import_csv(str(wp), None)
    assert out["watchlist"] == 1 and out["dilewati"] == 3
    by = {x["topic"]: x for x in db.watchlist_list()}
    assert by["bumn"]["is_active"] == 0 and by["pemilu"]["is_active"] == 0


def test_discovery_rows_without_watchlist_topic_are_kept_inactive(env):
    dp = env / "d.csv"
    dp.write_text("day,topic,geo,category,keyword,source,trend_category,interest_score,opportunity_score,trend_change,status\n"
                  "2026-11-01,hilang,ID,0,kw,research,emerging,300,40,+300%,Saved\n", encoding="utf-8")
    assert db.discovery_import_csv(None, str(dp))["discovery"] == 1
    assert [(x["topic"], x["is_active"]) for x in db.watchlist_list()] == [("hilang", 0)]


# --------------------------------------------------------------------------- perintah discover


def test_discover_saves_only_rising_with_scores_and_writes_csv(env, monkeypatch, capsys):
    fake = Fake()
    assert run(monkeypatch, fake, "--add", "bumn", "--windows", "2") == 0
    assert [t for k, t in fake.calls] == ["now 1-d", "now 7-d"]               # --windows 2 = 2 permintaan
    got = {r["keyword"]: r for r in db.discovery_list(50)}
    assert set(got) == {"bumn a", "bumn b"}                                    # "bumn pop" (populer) tidak ikut
    assert got["bumn b"]["trend_category"] == "trending" and got["bumn b"]["opportunity_score"] > got["bumn a"]["opportunity_score"] > 0
    assert len(rows(env / "discovery.csv")) == 2 and rows(env / "watchlist.csv")[0]["topic"] == "bumn"
    assert "belum ada riwayat pembanding" in capsys.readouterr().out


def test_discover_next_day_flags_only_truly_new_keywords(env, monkeypatch, capsys):
    run(monkeypatch, Fake(), "--add", "bumn", "--windows", "1", day="2026-11-03")

    class Next(Fake):
        def explore(self, keywords, *a, **k):
            res = super().explore(keywords, *a, **k)
            res["related"][keywords[0]]["rising"].append({"query": "bumn baru", "value": 700, "label": "+700%"})
            return res

    capsys.readouterr()
    assert run(monkeypatch, Next(), "--windows", "1", day="2026-11-04") == 0
    out = capsys.readouterr().out
    assert "3 keyword naik" in out and "1 belum pernah tersimpan" in out
    by = {(r["keyword"], r["discovery_date"]): r for r in db.discovery_list(50)}
    assert ("bumn baru", "2026-11-04") in by and len(by) == 5                   # 2 hari pertama + 3 hari kedua
    # skor keyword baru mendapat bonus +10 "belum pernah tersimpan" dibanding keyword lama bernilai serupa
    assert by[("bumn baru", "2026-11-04")]["opportunity_score"] >= 10


def test_partial_failure_is_green_with_warning_all_failed_is_red(env, monkeypatch, capsys):
    db.watchlist_add("bagus", "ID", 0); db.watchlist_add("ditolak", "ID", 0)
    assert run(monkeypatch, Fake(fail={"ditolak"}), "--windows", "1") == 0
    out = capsys.readouterr()
    assert "[GAGAL] ditolak (ID)" in out.err and "::warning::1 dari 2 topik gagal" in out.out
    assert {r["keyword"] for r in db.discovery_list(50)} == {"bagus a", "bagus b"}

    # Semua gagal, walaupun cache lama untuk "bagus" masih ada: data lama tidak boleh disimpan sebagai data hari ini.
    capsys.readouterr()
    before = len(db.discovery_list(50))
    assert run(monkeypatch, Fake(fail={"bagus", "ditolak"}), "--windows", "1", day="2026-11-05") == 1
    assert "hanya ada cache lama" in capsys.readouterr().err
    assert len(db.discovery_list(50)) == before


def test_failed_window_becomes_annotation_not_failure(env, monkeypatch, capsys):
    db.watchlist_add("bumn", "ID", 0)
    assert run(monkeypatch, Fake(fail_windows={("bumn", "now 7-d")}), "--windows", "2") == 0
    assert "::warning::bumn (ID): gagal mengambil 7 hari" in capsys.readouterr().out


def test_empty_watchlist_is_not_an_error(env, monkeypatch, capsys):
    assert run(monkeypatch, Fake()) == 0 and "Watchlist kosong" in capsys.readouterr().out


def test_invalid_topic_is_rejected_and_inactive_topics_skipped(env, monkeypatch):
    assert run(monkeypatch, Fake(), "--add", "a, b") == 2
    w = db.watchlist_add("mati", "ID", 0); db.watchlist_set_active(w["id"], False)
    fake = Fake()
    run(monkeypatch, fake)
    assert fake.calls == []


def test_demo_run_touches_nothing(env, monkeypatch):
    assert cli.main(["discover", "--demo", "--add", "bumn", "--refresh"]) == 0
    assert db.watchlist_list() == [] and db.discovery_list(5) == []
    assert not (env / "watchlist.csv").exists() and not (env / "discovery.csv").exists()


def test_added_topic_is_saved_even_when_every_request_fails(env, monkeypatch):
    assert run(monkeypatch, Fake(fail={"bumn"}), "--add", "bumn", "--windows", "1") == 1
    assert [r["topic"] for r in rows(env / "watchlist.csv")] == ["bumn"]      # tetap terpantau besok
