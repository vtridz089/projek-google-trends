"""Tanggal 'hari ini' mengikuti zona waktu lokal (default WIB), bukan zona server.

Penting karena server/Codespaces/GitHub Actions biasanya berjalan di UTC;
tanpa ini, pengambilan pukul 05.00 WIB akan tercatat sebagai hari kemarin.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone


def today_local() -> date:
    name = os.environ.get("TRENDWATCH_TZ", "Asia/Jakarta")
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(name)).date()
    except Exception:
        # Database zona waktu tidak ada / nama salah: pakai UTC+7 (WIB).
        return datetime.now(timezone(timedelta(hours=7))).date()
