"""Login: sesi bertanda tangan (cookie) dan pembatas percobaan masuk. Hanya pustaka standar.

Aktif hanya jika ``TRENDWATCH_PASSWORD`` diisi (satu akun: ``TRENDWATCH_USER`` + kata sandi itu).
Cookie berisi ``nama|kedaluwarsa|tanda tangan`` (HMAC-SHA256). Kuncinya dari ``TRENDWATCH_SECRET`` bila diisi,
bila tidak diturunkan dari kata sandi, jadi mengganti kata sandi otomatis mengeluarkan semua sesi.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time

COOKIE = "tw_session"


def enabled() -> bool:
    return bool(os.environ.get("TRENDWATCH_PASSWORD"))


def username() -> str:
    return os.environ.get("TRENDWATCH_USER", "admin")


def session_seconds() -> int:
    try:
        return max(60, int(float(os.environ.get("TRENDWATCH_SESSION_HOURS", "12")) * 3600))
    except ValueError:
        return 12 * 3600


def _key() -> bytes:
    secret = os.environ.get("TRENDWATCH_SECRET")
    if secret:
        return secret.encode()
    return hashlib.sha256(f"trendwatch|{username()}|{os.environ.get('TRENDWATCH_PASSWORD', '')}".encode()).digest()


def check_credentials(user: str, password: str) -> bool:
    """Bandingkan dalam waktu tetap; kedua bagian selalu diperiksa."""
    ok_user = hmac.compare_digest(str(user).encode(), username().encode())
    ok_pwd = hmac.compare_digest(str(password).encode(), os.environ.get("TRENDWATCH_PASSWORD", "").encode())
    return enabled() and ok_user and ok_pwd


def _sig(payload: str) -> str:
    return hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(user: str, now: float | None = None) -> str:
    exp = int((time.time() if now is None else now) + session_seconds())
    payload = f"{user}|{exp}"
    return f"{payload}|{_sig(payload)}"


def verify_token(token: str | None, now: float | None = None) -> str | None:
    """Nama pengguna bila token sah dan belum kedaluwarsa, selain itu ``None``."""
    if not token or token.count("|") != 2:
        return None
    user, exp, sig = token.split("|")
    if not hmac.compare_digest(sig, _sig(f"{user}|{exp}")):
        return None
    try:
        if int(exp) < (time.time() if now is None else now):
            return None
    except ValueError:
        return None
    return user if hmac.compare_digest(user.encode(), username().encode()) else None


def safe_next(target: str | None) -> str:
    """Alamat tujuan setelah login: hanya jalur di situs ini (cegah pengalihan ke situs lain)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target and "\n" not in target \
            and "\r" not in target and not target.startswith("/login") and not target.startswith("/api/"):
        return target
    return "/"


class Throttle:
    """Batasi percobaan login yang gagal per alamat: ``limit`` kali dalam ``window`` detik."""

    def __init__(self, limit: int = 5, window: float = 300.0):
        self.limit, self.window = limit, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        fresh = [t for t in self._fails.get(key, []) if now - t < self.window]
        if fresh:
            self._fails[key] = fresh
        else:
            self._fails.pop(key, None)
        return fresh

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Detik yang harus ditunggu, 0 bila boleh mencoba."""
        now = time.time() if now is None else now
        with self._lock:
            fresh = self._recent(key, now)
            return 0 if len(fresh) < self.limit else int(self.window - (now - fresh[0])) + 1

    def fail(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._recent(key, now)
            self._fails.setdefault(key, []).append(now)
            if len(self._fails) > 5000:  # jaga memori
                self._fails.pop(next(iter(self._fails)))

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
