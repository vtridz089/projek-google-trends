"""Akun dan login ala aplikasi web: daftar, masuk, ubah kata sandi, lupa kata sandi (email).

Logika ada di sini dan tidak bergantung pada FastAPI; ``main.py`` hanya memetakannya ke HTTP.
- Kata sandi di-hash dengan scrypt (pustaka standar) + garam acak.
- Sesi = cookie bertanda tangan HMAC: ``id|kedaluwarsa|sidik-kata-sandi|tanda tangan``. Mengganti kata sandi
  mengubah sidik, jadi semua sesi lama otomatis tidak sah. Kunci dari ``TRENDWATCH_SECRET`` atau dibuat otomatis
  dan disimpan di database.
- Token reset: acak 256-bit, hanya hash-nya yang disimpan, berlaku 1 jam, sekali pakai.
- Login dimatikan hanya dengan ``TRENDWATCH_AUTH=off`` (untuk pengembangan lokal/tes).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time

from . import mailer, users

log = logging.getLogger("trendwatch.auth")

COOKIE = "tw_session"
MIN_PASSWORD = 8
RESET_TTL = 3600
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class AuthError(Exception):
    def __init__(self, status: int, message: str, retry_after: int = 0):
        super().__init__(message)
        self.status, self.message, self.retry_after = status, message, retry_after


def enabled() -> bool:
    return os.environ.get("TRENDWATCH_AUTH", "on").strip().lower() not in ("off", "0", "false", "no")


def session_seconds() -> int:
    try:
        return max(60, int(float(os.environ.get("TRENDWATCH_SESSION_HOURS", "168")) * 3600))
    except ValueError:
        return 168 * 3600


# --------------------------------------------------------------------------- kata sandi


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$14$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n_exp, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** int(n_exp), r=int(r), p=int(p),
                             dklen=len(digest) // 2)
        return hmac.compare_digest(got.hex(), digest)
    except (ValueError, TypeError, AttributeError):
        return False


_DUMMY = None


def _dummy_hash() -> str:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password("tidak-dipakai")
    return _DUMMY


def clean_email(value: str) -> str:
    email = (value or "").strip().lower()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise AuthError(400, "Email tidak valid.")
    return email


def check_password_rules(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(400, f"Kata sandi minimal {MIN_PASSWORD} karakter.")
    if len(password) > 200:
        raise AuthError(400, "Kata sandi terlalu panjang (maksimal 200 karakter).")


# --------------------------------------------------------------------------- sesi


def _key() -> bytes:
    env = os.environ.get("TRENDWATCH_SECRET")
    return env.encode() if env else users.setting_get_or_create("session_key", lambda: secrets.token_hex(32)).encode()


def _fingerprint(user: dict) -> str:
    return hashlib.sha256(user["password_hash"].encode()).hexdigest()[:16]


def _sig(payload: str) -> str:
    return hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(user: dict, now: float | None = None) -> str:
    exp = int((time.time() if now is None else now) + session_seconds())
    payload = f"{user['id']}|{exp}|{_fingerprint(user)}"
    return f"{payload}|{_sig(payload)}"


def verify_token(token: str | None, now: float | None = None) -> dict | None:
    """Pengguna bila token sah, belum kedaluwarsa, dan kata sandinya belum berubah; selain itu ``None``."""
    if not token or token.count("|") != 3:
        return None
    uid, exp, fp, sig = token.split("|")
    if not hmac.compare_digest(sig, _sig(f"{uid}|{exp}|{fp}")):
        return None
    try:
        if int(exp) < (time.time() if now is None else now):
            return None
        user = users.by_id(int(uid))
    except ValueError:
        return None
    return user if user and hmac.compare_digest(fp, _fingerprint(user)) else None


def public(user: dict) -> dict:
    return {"name": user["name"], "email": user["email"]}


def safe_next(target: str | None) -> str:
    """Alamat tujuan setelah login: hanya jalur di situs ini (cegah pengalihan ke situs lain)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target \
            and "\n" not in target and "\r" not in target and not target.startswith(("/login", "/daftar", "/lupa", "/reset", "/api/")):
        return target
    return "/"


# --------------------------------------------------------------------------- pembatas percobaan


class Throttle:
    """Batasi kejadian per kunci: ``limit`` kali dalam ``window`` detik."""

    def __init__(self, limit: int = 5, window: float = 300.0):
        self.limit, self.window = limit, window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        fresh = [t for t in self._hits.get(key, []) if now - t < self.window]
        if fresh:
            self._hits[key] = fresh
        else:
            self._hits.pop(key, None)
        return fresh

    def retry_after(self, key: str, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            fresh = self._recent(key, now)
            return 0 if len(fresh) < self.limit else int(self.window - (now - fresh[0])) + 1

    def fail(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._recent(key, now)
            self._hits.setdefault(key, []).append(now)
            if len(self._hits) > 5000:
                self._hits.pop(next(iter(self._hits)))

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


LOGIN_IP, LOGIN_EMAIL = Throttle(10, 300), Throttle(5, 900)
REGISTER, FORGOT = Throttle(10, 3600), Throttle(5, 3600)


def reset_throttles() -> None:
    global LOGIN_IP, LOGIN_EMAIL, REGISTER, FORGOT
    LOGIN_IP, LOGIN_EMAIL, REGISTER, FORGOT = Throttle(10, 300), Throttle(5, 900), Throttle(10, 3600), Throttle(5, 3600)


def _guard(throttle: Throttle, key: str, what: str) -> None:
    wait = throttle.retry_after(key)
    if wait:
        raise AuthError(429, f"Terlalu banyak percobaan {what}. Coba lagi dalam {wait} detik.", wait)


# --------------------------------------------------------------------------- aksi


def register(name: str, email: str, password: str, ip: str) -> dict:
    _guard(REGISTER, ip, "mendaftar")
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 80:
        raise AuthError(400, "Nama wajib diisi (maksimal 80 karakter).")
    email = clean_email(email)
    check_password_rules(password)
    REGISTER.fail(ip)
    try:
        return users.create(email, name, hash_password(password))
    except ValueError as exc:
        raise AuthError(409, str(exc)) from exc


def login(email: str, password: str, ip: str) -> dict:
    email = (email or "").strip().lower()
    _guard(LOGIN_IP, ip, "masuk")
    _guard(LOGIN_EMAIL, email, "masuk ke akun ini")
    user = users.by_email(email)
    ok = verify_password(password or "", user["password_hash"] if user else _dummy_hash())  # waktu sama bila email tak ada
    if not (user and ok):
        LOGIN_IP.fail(ip)
        LOGIN_EMAIL.fail(email)
        raise AuthError(401, "Email atau kata sandi salah.")
    LOGIN_EMAIL.reset(email)
    return user


def change_password(user: dict, current: str, new: str) -> dict:
    _guard(LOGIN_EMAIL, user["email"], "mengganti kata sandi")
    if not verify_password(current or "", user["password_hash"]):
        LOGIN_EMAIL.fail(user["email"])
        raise AuthError(400, "Kata sandi saat ini salah.")
    check_password_rules(new)
    if new == current:
        raise AuthError(400, "Kata sandi baru harus berbeda dari yang lama.")
    users.set_password(user["id"], hash_password(new))
    users.reset_purge(user["id"])
    return users.by_id(user["id"])


def forgot(email: str, ip: str, base_url: str, now: float | None = None) -> None:
    """Selalu berhasil dari sisi pemanggil (tidak membocorkan email mana yang terdaftar)."""
    _guard(FORGOT, ip, "meminta reset")
    FORGOT.fail(ip)
    try:
        email = clean_email(email)
    except AuthError:
        return
    user = users.by_email(email)
    if not user:
        return
    now = time.time() if now is None else now
    raw = secrets.token_urlsafe(32)
    users.reset_put(hashlib.sha256(raw.encode()).hexdigest(), user["id"], now + RESET_TTL)
    link = f"{(os.environ.get('TRENDWATCH_BASE_URL') or base_url).rstrip('/')}/reset?token={raw}"
    body = (f"Halo {user['name']},\n\nAda permintaan mengatur ulang kata sandi akun Keyword research Anda.\n"
            f"Buka tautan ini dalam 1 jam:\n{link}\n\nBila bukan Anda yang meminta, abaikan email ini; kata sandi tidak berubah.\n")
    if mailer.configured():
        def _send():
            try:
                mailer.send(user["email"], "Atur ulang kata sandi", body)
            except Exception:
                log.exception("Gagal mengirim email reset ke %s", user["email"])
        threading.Thread(target=_send, daemon=True).start()
    else:
        log.warning("SMTP belum diatur; tautan reset untuk %s: %s", user["email"], link)


def reset(token: str, password: str, now: float | None = None) -> None:
    check_password_rules(password)
    uid = users.reset_take(hashlib.sha256((token or "").encode()).hexdigest(), time.time() if now is None else now)
    if uid is None:
        raise AuthError(400, "Tautan reset tidak valid atau sudah kedaluwarsa. Minta tautan baru.")
    users.set_password(uid, hash_password(password))


def seed_admin() -> None:
    """Untuk hosting yang databasenya hilang saat restart: akun dibuat ulang dari variabel lingkungan."""
    email, pwd = os.environ.get("TRENDWATCH_ADMIN_EMAIL"), os.environ.get("TRENDWATCH_ADMIN_PASSWORD")
    if not (email and pwd):
        return
    try:
        email = clean_email(email)
        check_password_rules(pwd)
    except AuthError as exc:
        log.error("TRENDWATCH_ADMIN_EMAIL/PASSWORD tidak valid: %s", exc.message)
        return
    if not users.by_email(email):
        users.create(email, os.environ.get("TRENDWATCH_ADMIN_NAME", "Admin"), hash_password(pwd))
        log.info("Akun awal dibuat untuk %s", email)
