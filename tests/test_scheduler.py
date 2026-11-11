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
