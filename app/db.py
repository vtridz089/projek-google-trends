"""Penyimpanan SQLite + query untuk dashboard."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from contextlib import closing
from datetime import date, timedelta

from .collector import DEMO_GEO, TrendItem

CSV_FIELDS = ["geo", "day", "keyword", "traffic_text", "traffic_num",
              "pub_date", "news_title", "news_url", "news_source"]


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
        # Cache hasil halaman Jelajah, agar pencarian yang sama tidak menembak Google/SerpApi lagi.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS explore_cache (
                cache_key  TEXT PRIMARY KEY,
                params     TEXT NOT NULL,
                payload    TEXT NOT NULL,
                fetched_at REAL NOT NULL
            )
            """
        )


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


EXPLORE_CACHE_MAX_ROWS = 500


def cache_get(key: str) -> tuple[dict, float] | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM explore_cache WHERE cache_key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload"]), row["fetched_at"]
    except ValueError:
        return None  # cache rusak dianggap tidak ada


def cache_put(key: str, params: dict, payload: dict, now: float) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            """INSERT INTO explore_cache (cache_key, params, payload, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (cache_key) DO UPDATE SET
                   params = excluded.params, payload = excluded.payload,
                   fetched_at = excluded.fetched_at""",
            (key, json.dumps(params, ensure_ascii=False),
             json.dumps(payload, ensure_ascii=False), now),
        )
        conn.execute(
            """DELETE FROM explore_cache WHERE cache_key NOT IN
               (SELECT cache_key FROM explore_cache ORDER BY fetched_at DESC LIMIT ?)""",
            (EXPLORE_CACHE_MAX_ROWS,),
        )


def recent_searches(limit: int = 8, kind: str = "explore") -> list[dict]:
    """Pencarian terakhir dari cache. ``kind`` memisahkan halaman Jelajah ('explore')
    dari halaman Booming ('booming')."""
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT params FROM explore_cache ORDER BY fetched_at DESC LIMIT ?", (limit * 6,)
        ).fetchall()
    out = []
    for r in rows:
        try:
            p = json.loads(r["params"])
        except ValueError:
            continue
        if p.get("kind", "explore") == kind:
            out.append(p)
            if len(out) >= limit:
                break
    return out


def today_matches(geo: str, topic: str, limit: int = 5) -> dict:
    """Tren harian (RSS) pada hari terakhir yang keyword atau judul beritanya memuat topik.
    Pelengkap halaman Booming; kosong bila pengambilan harian belum pernah dijalankan."""
    empty = {"day": None, "items": []}
    if not geo or not topic:
        return empty
    like = "%" + topic.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with closing(connect()) as conn:
        day = conn.execute("SELECT MAX(day) AS d FROM snapshots WHERE geo = ?", (geo,)).fetchone()["d"]
        if not day:
            return empty
        rows = conn.execute(
            """SELECT keyword, traffic_text, traffic_num, news_title, news_url, news_source
               FROM snapshots
               WHERE geo = ? AND day = ?
                 AND (LOWER(keyword) LIKE ? ESCAPE '\\' OR LOWER(news_title) LIKE ? ESCAPE '\\')
               ORDER BY traffic_num DESC, keyword LIMIT ?""",
            (geo, day, like, like, limit),
        ).fetchall()
    return {"day": day, "items": [dict(r) for r in rows]}


def export_rows(geo: str | None = None, days: int | None = None) -> list[dict]:
    """Baris mentah untuk diekspor, urut (hari, negara, keyword) agar diff-nya rapi.

    Tanpa ``geo``, semua negara kecuali data demo (XX) ikut diekspor.
    ``days`` dihitung mundur dari hari terakhir yang ada datanya.
    """
    where: list[str] = []
    params: list = []
    with closing(connect()) as conn:
        if geo:
            where.append("geo = ?")
            params.append(geo)
        else:
            where.append("geo != ?")
            params.append(DEMO_GEO)

        if days:
            q = "SELECT MAX(day) AS d FROM snapshots" + (" WHERE geo = ?" if geo else "")
            latest = conn.execute(q, (geo,) if geo else ()).fetchone()["d"]
            if not latest:
                return []
            since = (date.fromisoformat(latest) - timedelta(days=days - 1)).isoformat()
            where.append("day >= ?")
            params.append(since)

        sql = (
            "SELECT " + ", ".join(CSV_FIELDS) + " FROM snapshots WHERE "
            + " AND ".join(where) + " ORDER BY day, geo, keyword"
        )
        return [dict(r) for r in conn.execute(sql, params)]


def dump_csv(path: str) -> int:
    """Tulis seluruh data nyata (tanpa demo) ke CSV. Dipakai agar riwayat bisa
    disimpan di Git: berkas teks, mudah di-diff, dan bisa dimuat ulang kapan saja."""
    rows = export_rows()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)
    return len(rows)


def import_csv(path: str) -> tuple[int, int]:
    """Muat CSV ke database (aman dijalankan berulang). Mengembalikan (diimpor, dilewati)."""
    groups: dict[tuple[str, str], list[TrendItem]] = {}
    skipped = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                geo = (row["geo"] or "").strip().upper()
                day = date.fromisoformat((row["day"] or "").strip()).isoformat()
                keyword = (row["keyword"] or "").strip()
                if len(geo) != 2 or not geo.isalpha() or not keyword:
                    raise ValueError("baris tidak valid")
                traffic_num = int(row.get("traffic_num") or 0)
            except (KeyError, ValueError, TypeError, AttributeError):
                skipped += 1
                continue
            groups.setdefault((geo, day), []).append(
                TrendItem(
                    keyword=keyword,
                    traffic_text=row.get("traffic_text") or "",
                    traffic_num=traffic_num,
                    pub_date=row.get("pub_date") or "",
                    news_title=row.get("news_title") or "",
                    news_url=row.get("news_url") or "",
                    news_source=row.get("news_source") or "",
                )
            )

    imported = 0
    for (geo, day), items in groups.items():
        imported += save_items(geo, date.fromisoformat(day), items)
    return imported, skipped


# ------------------------------------------------------------------
# Daily Keyword Discovery (SQLite)
# ------------------------------------------------------------------

def discovery_init() -> None:
    """Create discovery tables without touching existing TrendWatch data."""
    with closing(connect()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS topic_watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL COLLATE NOCASE,
                geo TEXT NOT NULL DEFAULT 'ID',
                category INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(topic, geo, category)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS keyword_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL COLLATE NOCASE,
                topic_id INTEGER NOT NULL REFERENCES topic_watchlist(id) ON DELETE CASCADE,
                discovery_date TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'related',
                trend_category TEXT NOT NULL DEFAULT 'new_candidate',
                interest_score INTEGER,
                opportunity_score REAL,
                status TEXT NOT NULL DEFAULT 'New',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(keyword, topic_id, discovery_date, source)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS keyword_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL REFERENCES keyword_candidates(id) ON DELETE CASCADE,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
                interest_score INTEGER,
                trend_change TEXT,
                source TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_candidate_date ON keyword_candidates(discovery_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_candidate ON keyword_snapshots(candidate_id, recorded_at)")

def watchlist_list() -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM topic_watchlist ORDER BY is_active DESC, topic COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]

def watchlist_add(topic: str, geo: str = "ID", category: int = 0) -> dict:
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT INTO topic_watchlist(topic, geo, category) VALUES (?, ?, ?) "
            "ON CONFLICT(topic, geo, category) DO UPDATE SET is_active=1",
            (topic, geo, category),
        )
        row = conn.execute(
            "SELECT * FROM topic_watchlist WHERE topic=? AND geo=? AND category=?",
            (topic, geo, category),
        ).fetchone()
    return dict(row)

def watchlist_set_active(topic_id: int, active: bool) -> bool:
    with closing(connect()) as conn, conn:
        cur = conn.execute("UPDATE topic_watchlist SET is_active=? WHERE id=?", (int(active), topic_id))
    return cur.rowcount > 0

def watchlist_delete(topic_id: int) -> bool:
    with closing(connect()) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        cur = conn.execute("DELETE FROM topic_watchlist WHERE id=?", (topic_id,))
    return cur.rowcount > 0

def discovery_save(topic_id: int, day: str, rows: list[dict]) -> int:
    saved = 0
    with closing(connect()) as conn, conn:
        for item in rows:
            keyword = str(item.get("keyword", "")).strip()
            if not keyword:
                continue
            conn.execute("""
                INSERT INTO keyword_candidates
                    (keyword, topic_id, discovery_date, source, trend_category, interest_score, opportunity_score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword, topic_id, discovery_date, source) DO UPDATE SET
                    trend_category=excluded.trend_category,
                    interest_score=excluded.interest_score,
                    opportunity_score=excluded.opportunity_score
            """, (keyword, topic_id, day, item.get("source", "related"),
                  item.get("trend_category", "new_candidate"), item.get("interest_score"),
                  item.get("opportunity_score")))
            row = conn.execute("""
                SELECT id FROM keyword_candidates
                WHERE keyword=? AND topic_id=? AND discovery_date=? AND source=?
            """, (keyword, topic_id, day, item.get("source", "related"))).fetchone()
            conn.execute("""
                INSERT INTO keyword_snapshots(candidate_id, interest_score, trend_change, source)
                VALUES (?, ?, ?, ?)
            """, (row["id"], item.get("interest_score"), item.get("trend_change"), item.get("source", "related")))
            saved += 1
    return saved

def discovery_list(limit: int = 200) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute("""
            SELECT c.*, w.topic AS watch_topic, w.geo
            FROM keyword_candidates c JOIN topic_watchlist w ON w.id=c.topic_id
            ORDER BY c.discovery_date DESC, COALESCE(c.opportunity_score, 0) DESC, c.keyword
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]

def discovery_set_status(candidate_id: int, status: str) -> bool:
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE keyword_candidates SET status=? WHERE id=?",
            (status, candidate_id),
        )
    return cur.rowcount > 0


def discovery_history(topic: str, geo: str, category: int, before_day: str) -> tuple[set[str], int]:
    """Keyword (huruf kecil) yang sudah tersimpan untuk topik ini sebelum ``before_day``,
    dan berapa hari berbeda yang tercatat. (set(), 0) bila topik belum ada di Watchlist."""
    with closing(connect()) as conn:
        rows = conn.execute("""
            SELECT DISTINCT lower(c.keyword) AS k, c.discovery_date AS d
            FROM keyword_candidates c JOIN topic_watchlist w ON w.id = c.topic_id
            WHERE w.topic = ? AND w.geo = ? AND w.category = ? AND c.discovery_date < ?
        """, (topic, geo, int(category), before_day)).fetchall()
    return {r["k"] for r in rows}, len({r["d"] for r in rows})
