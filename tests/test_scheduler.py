"""Tes jadwal harian keyword inti."""
from datetime import datetime, time as dtime, timezone, timedelta

import pytest

from app import core, db, platforms, scheduler
from app.explore import ExploreError

WIB = timezone(timedelta(hours=7))


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "s.db"))
    db.init_db()
    from app import users
    users.init()
    core.init()


class Runner:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, seed, geo, demo=False):
        self.calls.append(seed)
        if self.fail:
            raise ExploreError("429")
        return {"seed": seed, "items": [], "sumber": {"saran": "google"}, "errors": []}


def test_due_logic():
    at = dtime(6, 0)
    assert not scheduler.due(datetime(2026, 11, 11, 5, 59, tzinfo=WIB), at, "")
    assert scheduler.due(datetime(2026, 11, 11, 6, 0, tzinfo=WIB), at, "")
    assert scheduler.due(datetime(2026, 11, 11, 15, 0, tzinfo=WIB), at, "2026-11-10")  # mengejar
    assert not scheduler.due(datetime(2026, 11, 11, 15, 0, tzinfo=WIB), at, "2026-11-11")
    assert not scheduler.due(datetime(2026, 11, 11, 15, 0, tzinfo=WIB), None, "")


def test_schedule_env(monkeypatch):
    monkeypatch.setenv("TRENDWATCH_CORE_SCHEDULE", "off")
    assert scheduler.schedule_time() is None and scheduler.label() is None
    monkeypatch.setenv("TRENDWATCH_CORE_SCHEDULE", "07:30")
    assert scheduler.schedule_time() == dtime(7, 30) and scheduler.label() == "07:30 WIB"
    monkeypatch.setenv("TRENDWATCH_CORE_SCHEDULE", "ngawur")
    assert scheduler.schedule_time() is None


def test_tick_runs_all_once_per_day(monkeypatch):
    monkeypatch.setenv("TRENDWATCH_CORE_SCHEDULE", "06:00")
    r = Runner()
    now = datetime(2026, 11, 11, 6, 5, tzinfo=WIB)
    assert scheduler.tick(now, runner=r) is True
    assert len(r.calls) == sum(len(core.keywords(p.slug)) for p in platforms.PLATFORMS) == 96
    assert scheduler.get_last() == "2026-11-11"
    assert scheduler.tick(now + timedelta(hours=1), runner=r) is False  # hari yang sama: tidak diulang
    assert len(r.calls) == 96
    assert scheduler.tick(now + timedelta(days=1), runner=r) is True  # besok jalan lagi


def test_tick_before_time_and_off(monkeypatch):
    r = Runner()
    monkeypatch.setattr(core, "missing", lambda geo="ID": 0)
    monkeypatch.setenv("TRENDWATCH_CORE_SCHEDULE", "06:00")
    assert scheduler.tick(datetime(2026, 11, 11, 5, 0, tzinfo=WIB), runner=r) is False
    monkeypatch.setenv("TRENDWATCH_CORE_SCHEDULE", "off")
    assert scheduler.tick(datetime(2026, 11, 11, 9, 0, tzinfo=WIB), runner=r) is False
    assert r.calls == []


def test_failures_do_not_crash_and_day_is_marked(monkeypatch):
    monkeypatch.setenv("TRENDWATCH_CORE_SCHEDULE", "06:00")
    r = Runner(fail=True)
    assert scheduler.tick(datetime(2026, 11, 11, 7, 0, tzinfo=WIB), runner=r) is True
    assert scheduler.get_last() == "2026-11-11"
    assert len(r.calls) == 2 * 15  # pemutus: berhenti setelah 2 gagal berturut-turut per platform


def test_busy_is_retried_later(monkeypatch):
    monkeypatch.setenv("TRENDWATCH_CORE_SCHEDULE", "06:00")
    assert core._refresh_lock.acquire(blocking=False)
    try:
        assert scheduler.tick(datetime(2026, 11, 11, 7, 0, tzinfo=WIB), runner=Runner()) is False
        assert scheduler.get_last() == ""
    finally:
        core._refresh_lock.release()


def test_listing_meta_has_schedule(monkeypatch):
    monkeypatch.setenv("TRENDWATCH_CORE_SCHEDULE", "06:00")
    assert core.listing("jadiasn")["meta"]["jadwal"] == "06:00 WIB"


def test_empty_database_is_filled_right_away_but_not_hammered(monkeypatch):
    monkeypatch.setenv("TRENDWATCH_CORE_SCHEDULE", "06:00")
    monkeypatch.setattr(scheduler, "_last_empty_try", 0.0)
    r = Runner(fail=True)
    early = datetime(2026, 11, 11, 3, 0, tzinfo=WIB)  # sebelum jam jadwal
    assert scheduler.tick(early, runner=r) is True
    assert scheduler.get_last() == ""  # bukan jalan terjadwal, jadi hari ini belum dianggap selesai
    n = len(r.calls)
    assert scheduler.tick(early, runner=r) is False  # masih kosong, tetapi tidak diulang tiap 5 menit
    assert len(r.calls) == n


def test_snapshot_roundtrip_fills_new_database_and_never_overwrites_newer(tmp_path, monkeypatch):
    import time as _t
    path = str(tmp_path / "core.json")
    r = Runner()
    core.refresh("jadiasn", runner=r, now=1000.0)
    assert core.export_json(path) == len(core.keywords("jadiasn"))
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "baru.db"))
    db.init_db(); core.init()
    assert core.missing() == 96
    assert core.import_json(path) == len(core.keywords("jadiasn"))
    assert core.missing() == 96 - len(core.keywords("jadiasn"))
    assert core.listing("jadiasn")["meta"]["ok"] == len(core.keywords("jadiasn"))
    assert core.import_json(path) == 0  # sama: tidak ditimpa ulang
    assert core.import_json(str(tmp_path / "tidak-ada.json")) == 0
    (tmp_path / "rusak.json").write_text("{bukan json", encoding="utf-8")
    assert core.import_json(str(tmp_path / "rusak.json")) == 0


def test_akhir_pekan_tidak_berjalan(monkeypatch):
    from datetime import datetime, time as dtime
    from app import scheduler
    monkeypatch.delenv("TRENDWATCH_CORE_WEEKEND", raising=False)
    sabtu = datetime(2026, 11, 21, 7, 0, tzinfo=scheduler.tzinfo())
    senin = datetime(2026, 11, 23, 7, 0, tzinfo=scheduler.tzinfo())
    assert scheduler.due(sabtu, dtime(6, 0), "") is False
    assert scheduler.due(senin, dtime(6, 0), "") is True
    monkeypatch.setenv("TRENDWATCH_CORE_WEEKEND", "on")
    assert scheduler.due(sabtu, dtime(6, 0), "") is True
