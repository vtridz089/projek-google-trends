"""Akun pengguna di SQLite: pengguna, pengaturan (kunci sesi), dan token reset kata sandi."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Callable

from . import db


def init() -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name          TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
        conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reset_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                expires    REAL NOT NULL
            )""")


def _one(row) -> dict | None:
    return dict(row) if row else None


def create(email: str, name: str, password_hash: str) -> dict:
    try:
        with closing(db.connect()) as conn, conn:
            cur = conn.execute("INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
                               (email, name, password_hash))
            return _one(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
    except sqlite3.IntegrityError as exc:
        raise ValueError("Email sudah terdaftar.") from exc


def by_email(email: str) -> dict | None:
    with closing(db.connect()) as conn:
        return _one(conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())


def by_id(user_id: int) -> dict | None:
    with closing(db.connect()) as conn:
        return _one(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def count() -> int:
    with closing(db.connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def set_password(user_id: int, password_hash: str) -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def setting_get_or_create(key: str, factory: Callable[[], str]) -> str:
    with closing(db.connect()) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)", (key, factory()))
        return conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()[0]


def reset_put(token_hash: str, user_id: int, expires: float) -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("DELETE FROM reset_tokens WHERE user_id = ?", (user_id,))   # hanya satu token aktif per pengguna
        conn.execute("INSERT INTO reset_tokens (token_hash, user_id, expires) VALUES (?, ?, ?)",
                     (token_hash, user_id, expires))


def reset_take(token_hash: str, now: float) -> int | None:
    """Pakai token sekali: kembalikan id pengguna bila sah dan belum kedaluwarsa, token langsung dihapus."""
    with closing(db.connect()) as conn, conn:
        conn.execute("DELETE FROM reset_tokens WHERE expires < ?", (now,))
        row = conn.execute("SELECT user_id FROM reset_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM reset_tokens WHERE token_hash = ?", (token_hash,))
        return row[0]


def reset_purge(user_id: int) -> None:
    with closing(db.connect()) as conn, conn:
        conn.execute("DELETE FROM reset_tokens WHERE user_id = ?", (user_id,))
