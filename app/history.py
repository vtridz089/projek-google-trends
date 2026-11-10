"""Riwayat keyword per platform: tiap keyword ditandai "Dipakai" atau "Tidak dipakai".

Riwayat dipakai bersama satu tim di platform yang sama (supaya tidak ada dua orang menulis keyword yang sama);
yang menandai dan waktunya dicatat. Memilih ulang mengganti status, dan menghapus mengembalikan ke "belum dipilih".
"""
from __future__ import annotations

import re
from contextlib import closing

from . import db

STATUSES = ("dipakai", "tidak")
MAX_KEYWORD = 200


def init() -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS keyword_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                platform   TEXT NOT NULL,
                keyword    TEXT NOT NULL,
                keyword_key TEXT NOT NULL,
                status     TEXT NOT NULL,
                seed       TEXT NOT NULL DEFAULT '',
                geo        TEXT NOT NULL DEFAULT '',
                user_id    INTEGER,
                user_name  TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (platform, keyword_key)
            )""")


def clean(keyword: str) -> str:
    keyword = re.sub(r"\s+", " ", str(keyword or "")).strip()
    if not keyword:
        raise ValueError("Keyword wajib diisi.")
    if len(keyword) > MAX_KEYWORD:
        raise ValueError(f"Keyword maksimal {MAX_KEYWORD} karakter.")
    return keyword


def _key(keyword: str) -> str:
    return keyword.casefold()


def _row(r) -> dict:
    return {"id": r["id"], "keyword": r["keyword"], "status": r["status"], "seed": r["seed"], "geo": r["geo"],
            "user": r["user_name"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


def set_status(platform: str, keyword: str, status: str, *, seed: str = "", geo: str = "",
               user_id: int | None = None, user_name: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError("Status harus 'dipakai' atau 'tidak'.")
    keyword = clean(keyword)
    seed = re.sub(r"\s+", " ", str(seed or "")).strip()[:100]
    geo = str(geo or "").strip().upper()[:2]
    with closing(db.connect()) as conn, conn:
        conn.execute("""
            INSERT INTO keyword_history (platform, keyword, keyword_key, status, seed, geo, user_id, user_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (platform, keyword_key) DO UPDATE SET
                status = excluded.status, keyword = excluded.keyword, seed = excluded.seed, geo = excluded.geo,
                user_id = excluded.user_id, user_name = excluded.user_name, updated_at = datetime('now')
            """, (platform, keyword, _key(keyword), status, seed, geo, user_id, str(user_name or "")[:80]))
        return _row(conn.execute("SELECT * FROM keyword_history WHERE platform = ? AND keyword_key = ?",
                                 (platform, _key(keyword))).fetchone())


def clear(platform: str, keyword: str) -> bool:
    keyword = clean(keyword)
    with closing(db.connect()) as conn, conn:
        cur = conn.execute("DELETE FROM keyword_history WHERE platform = ? AND keyword_key = ?", (platform, _key(keyword)))
        return cur.rowcount > 0


def statuses(platform: str) -> dict[str, str]:
    """{keyword (huruf kecil): status} untuk menandai baris hasil riset."""
    with closing(db.connect()) as conn:
        rows = conn.execute("SELECT keyword_key, status FROM keyword_history WHERE platform = ?", (platform,)).fetchall()
    return {r["keyword_key"]: r["status"] for r in rows}


def listing(platform: str, status: str = "", q: str = "", limit: int = 500) -> dict:
    where, args = ["platform = ?"], [platform]
    if status:
        if status not in STATUSES:
            raise ValueError("Status harus 'dipakai' atau 'tidak'.")
        where.append("status = ?"); args.append(status)
    q = (q or "").strip()
    if q:
        where.append("keyword_key LIKE ? ESCAPE '\\'")
        args.append("%" + q.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    with closing(db.connect()) as conn:
        rows = conn.execute(f"SELECT * FROM keyword_history WHERE {' AND '.join(where)} "
                            "ORDER BY updated_at DESC, id DESC LIMIT ?", (*args, max(1, min(int(limit), 1000)))).fetchall()
        counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM keyword_history WHERE platform = ? GROUP BY status", (platform,))}
    return {"items": [_row(r) for r in rows], "counts": {s: counts.get(s, 0) for s in STATUSES}}
