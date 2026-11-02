"""Penyimpanan SQLite + query untuk dashboard."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import date, timedelta

from .collector import TrendItem


def db_path() -> str:
    return os.environ.get("TRENDWATCH_DB", "trendwatch.db")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                geo           TEXT NOT NULL,
                keyword       TEXT NOT NULL COLLATE NOCASE,
                day           TEXT NOT NULL,           -- YYYY-MM-DD
                traffic_text  TEXT NOT NULL DEFAULT '',
                traffic_num   INTEGER NOT NULL DEFAULT 0,
                pub_date      TEXT NOT NULL DEFAULT '',
                news_title    TEXT NOT NULL DEFAULT '',
                news_url      TEXT NOT NULL DEFAULT '',
                news_source   TEXT NOT NULL DEFAULT '',
                collected_at  TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (geo, keyword, day)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_geo_day ON snapshots (geo, day)")


def save_items(geo: str, day: date, items: list[TrendItem]) -> int:
    """Simpan hasil pengambilan. Satu baris per (geo, keyword, hari);
    jika dijalankan lagi di hari yang sama, nilainya diperbarui."""
    with closing(connect()) as conn, conn:
        for it in items:
            conn.execute(
                """
                INSERT INTO snapshots
                    (geo, keyword, day, traffic_text, traffic_num, pub_date,
                     news_title, news_url, news_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (geo, keyword, day) DO UPDATE SET
                    traffic_text = excluded.traffic_text,
                    traffic_num  = excluded.traffic_num,
                    pub_date     = excluded.pub_date,
                    news_title   = excluded.news_title,
                    news_url     = excluded.news_url,
                    news_source  = excluded.news_source,
                    collected_at = datetime('now')
                """,
                (geo, it.keyword, day.isoformat(), it.traffic_text, it.traffic_num,
                 it.pub_date, it.news_title, it.news_url, it.news_source),
            )
    return len(items)


def overview(geo: str) -> dict:
    """Keyword pada hari terakhir yang ada datanya, lengkap dengan status
    dibanding hari sebelumnya:

    - baseline    : belum ada hari pembanding (baru ada data satu hari)
    - baru        : belum pernah muncul sebelumnya
    - muncul lagi : pernah muncul, tetapi tidak ada di hari sebelumnya
    - naik/turun/tetap : dibanding perkiraan traffic hari sebelumnya
    """
    with closing(connect()) as conn:
        latest = conn.execute(
            "SELECT MAX(day) AS d FROM snapshots WHERE geo = ?", (geo,)
        ).fetchone()["d"]
        if not latest:
            return {"geo": geo, "day": None, "prev_day": None, "summary": {}, "items": []}

        rows = conn.execute(
            """
            SELECT keyword, traffic_text, traffic_num, pub_date,
                   news_title, news_url, news_source
            FROM snapshots WHERE geo = ? AND day = ?
            ORDER BY traffic_num DESC, keyword
            """,
            (geo, latest),
        ).fetchall()

        prev_day = conn.execute(
            "SELECT MAX(day) AS d FROM snapshots WHERE geo = ? AND day < ?", (geo, latest)
        ).fetchone()["d"]

        prev_traffic: dict[str, int] = {}
        if prev_day:
            prev_traffic = {
                r["keyword"].lower(): r["traffic_num"]
                for r in conn.execute(
                    "SELECT keyword, traffic_num FROM snapshots WHERE geo = ? AND day = ?",
                    (geo, prev_day),
                )
            }

        seen_before = {
            r["keyword"].lower(): r["n"]
            for r in conn.execute(
                """SELECT keyword, COUNT(*) AS n FROM snapshots
                   WHERE geo = ? AND day < ? GROUP BY keyword""",
                (geo, latest),
            )
        }

    items = []
    summary: dict[str, int] = {}
    for r in rows:
        key = r["keyword"].lower()
        d = dict(r)
        d["appearances"] = seen_before.get(key, 0) + 1
        d["prev_traffic_num"] = prev_traffic.get(key)
        d["change_pct"] = None

        if not prev_day:
            status = "baseline"
        elif key not in seen_before:
            status = "baru"
        elif key not in prev_traffic:
            status = "muncul lagi"
        else:
            before, now = prev_traffic[key], r["traffic_num"]
            if before > 0:
                d["change_pct"] = round((now - before) / before * 100)
            status = "naik" if now > before else "turun" if now < before else "tetap"

        d["status"] = status
        summary[status] = summary.get(status, 0) + 1
        items.append(d)

    return {
        "geo": geo,
        "day": latest,
        "prev_day": prev_day,
        "total": len(items),
        "summary": summary,
        "items": items,
    }


def history(geo: str, keyword: str, days: int = 30) -> list[dict]:
    with closing(connect()) as conn:
        latest = conn.execute(
            "SELECT MAX(day) AS d FROM snapshots WHERE geo = ?", (geo,)
        ).fetchone()["d"]
        if not latest:
            return []
        since = (date.fromisoformat(latest) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """SELECT day, traffic_text, traffic_num FROM snapshots
               WHERE geo = ? AND keyword = ? AND day >= ? ORDER BY day""",
            (geo, keyword, since),
        ).fetchall()
    return [dict(r) for r in rows]


def top_keywords(geo: str, days: int = 7, limit: int = 10) -> list[dict]:
    """Keyword dengan akumulasi perkiraan traffic terbesar dalam N hari terakhir."""
    with closing(connect()) as conn:
        latest = conn.execute(
            "SELECT MAX(day) AS d FROM snapshots WHERE geo = ?", (geo,)
        ).fetchone()["d"]
        if not latest:
            return []
        since = (date.fromisoformat(latest) - timedelta(days=days - 1)).isoformat()
        rows = conn.execute(
            """SELECT keyword, SUM(traffic_num) AS total, COUNT(*) AS days_seen
               FROM snapshots WHERE geo = ? AND day >= ?
               GROUP BY keyword ORDER BY total DESC, keyword LIMIT ?""",
            (geo, since, limit),
        ).fetchall()
    return [dict(r) for r in rows]
