"""Ambil Rising/Top queries LENGKAP dari Google Trends lewat Chrome di komputer ini, digerakkan dari dalam aplikasi.

Alasannya (terbukti dengan uji): jalur data lama hanya memberi 3 sampai 8 baris Rising, sedangkan antarmuka Trends yang
baru memberi 30 Rising + 30 Top, **hanya untuk sesi browser yang login ke akun Google**. Maka aplikasi membuka Chrome
sendiri dengan profil khusus (login sekali di jendela itu, profilnya diingat), lalu membaca jawaban ``fXqlme``.

Hanya berfungsi bila aplikasi berjalan di komputer yang punya Chrome (bukan di server tanpa layar). Hasil disimpan di
``data/trends_web.json`` (lihat ``trends_web``) dan dipakai panel Trends.
"""
from __future__ import annotations

import logging
import os
import random
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from . import core, keyword_research, trends_web
from .trends_web import WebFormatError, parse_fxqlme

log = logging.getLogger("cerekey.trends_runner")

CDP_PORT = int(os.environ.get("TRENDWATCH_CDP_PORT", "9222"))
WINDOWS = [(col.replace("naik_", ""), tr) for tr, col in keyword_research.WINDOWS]
LOGIN_MIN_RISING = 15   # tanpa login Google memberi 3 sampai 8 Rising; dengan login 30
FRESH_HOURS = 20
FETCH_WAIT_SECONDS = 40


class RunnerError(RuntimeError):
    pass


class LoginNeeded(RunnerError):
    pass


class Busy(RunnerError):
    pass


# --------------------------------------------------------------------------- Chrome

def chrome_path() -> str | None:
    env = os.environ.get("TRENDWATCH_CHROME", "").strip()
    if env:
        return env if os.path.exists(env) else None
    cands = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def profile_dir() -> str:
    return os.environ.get("TRENDWATCH_CHROME_PROFILE") or os.path.join(os.path.expanduser("~"), ".cerekey-chrome")


def cdp_ready(port: int = CDP_PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


def available() -> dict:
    """Apakah fitur ini bisa dipakai di komputer ini."""
    try:
        import playwright  # noqa: F401
        has_pw = True
    except ImportError:
        has_pw = False
    path = chrome_path()
    return {"chrome": bool(path), "playwright": has_pw, "siap": bool(path) and has_pw}


def ensure_chrome(port: int = CDP_PORT, launcher=subprocess.Popen, wait: float = 25.0) -> None:
    """Pastikan Chrome (profil khusus, port debug) berjalan; buka bila belum."""
    if cdp_ready(port):
        return
    path = chrome_path()
    if not path:
        raise RunnerError("Chrome tidak ditemukan di komputer ini. Pasang Google Chrome, atau isi TRENDWATCH_CHROME dengan lokasi chrome.exe.")
    os.makedirs(profile_dir(), exist_ok=True)
    launcher([path, f"--remote-debugging-port={port}", f"--user-data-dir={profile_dir()}", "--no-first-run",
              "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--lang=en-US", "https://accounts.google.com/"])
    end = time.time() + wait
    while time.time() < end:
        if cdp_ready(port):
            return
        time.sleep(0.5)
    raise RunnerError("Chrome dibuka tetapi tidak merespons. Tutup semua jendela Chrome lalu coba lagi.")


class BrowserFetcher:
    """Membuka halaman Explore Trends di tab Chrome dan membaca jawaban ``fXqlme``."""

    def __init__(self, port: int = CDP_PORT, geo: str = "ID"):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RunnerError("Playwright belum terpasang: jalankan  pip install playwright") from exc
        self.geo, self._pw = geo, sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
            self.page = ctx.new_page()
        except Exception as exc:  # noqa: BLE001
            self._pw.stop()
            raise RunnerError(f"Tidak bisa tersambung ke Chrome: {exc}") from exc
        self._bodies: list[str] = []

        def on_resp(r):
            if "batchexecute" in r.url and "fXqlme" in r.url:
                try:
                    self._bodies.append(r.text())
                except Exception:  # noqa: BLE001
                    pass
        self.page.on("response", on_resp)

    def fetch(self, term: str, time_range: str) -> dict | None:
        url = "https://trends.google.com/explore?" + urllib.parse.urlencode(
            {"q": term, "geo": self.geo, "date": time_range, "hl": "en"})
        for attempt in range(3):
            self._bodies.clear()
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:  # noqa: BLE001 - halaman lambat: tetap tunggu jawabannya
                pass
            for _ in range(FETCH_WAIT_SECONDS):
                if self._bodies:
                    break
                self.page.wait_for_timeout(1000)
            if self._bodies:
                self.page.wait_for_timeout(1000)
                try:
                    return parse_fxqlme(self._bodies[-1])
                except WebFormatError as exc:
                    log.warning("Bentuk jawaban Trends tidak dikenali: %s", exc)
                    return None
            self.page.wait_for_timeout(8000 * (attempt + 1))  # kemungkinan 429
        return None

    def close(self) -> None:
        try:
            self.page.close()
        finally:
            self._pw.stop()


# --------------------------------------------------------------------------- pengambilan

def check_login(fetch) -> None:
    """Uji satu kata: tanpa login Google, daftar Rising kecil. Raise LoginNeeded bila begitu."""
    r = fetch("soal cpns", "now 1-d")
    if r is None:
        raise RunnerError("Google Trends tidak memberi data (mungkin dibatasi sementara). Coba lagi beberapa menit lagi.")
    if len(r["rising"]) < LOGIN_MIN_RISING:
        raise LoginNeeded("Daftar Trends masih terbatas karena Chrome itu belum login ke Google. Login ke akun Google di jendela Chrome "
                          "yang terbuka, lalu klik tombolnya lagi.")


def collect(slugs: list[str], fetch, *, umur_jam: float = FRESH_HOURS, ulangi: bool = False, progress=None,
            sleep=None, jeda: float = 6.0, now=time.time, windows=None) -> dict:
    """Ambil semua topik pada ``slugs``; hasil ditulis ke berkas setelah tiap topik. ``fetch(term, time_range)``."""
    sleep = sleep or time.sleep
    windows = windows or WINDOWS
    store = dict(trends_web.load())
    res = {"diambil": 0, "gagal": 0, "dilewati": 0}
    total = sum(len(core.keywords(s)) for s in slugs)
    n = 0
    for slug in slugs:
        for seed in core.keywords(slug):
            n += 1
            entry = dict(store.get(trends_web.key(slug, seed), {}))
            if not ulangi and all(w in entry and now() - float(entry[w].get("diambil", 0)) < umur_jam * 3600 for w, _ in windows):
                res["dilewati"] += 1
                if progress:
                    progress(n, total, f"{seed} (sudah segar)")
                continue
            if progress:
                progress(n, total, seed)
            for w, tr in windows:
                r = fetch(seed.lower(), tr)
                if r is None:
                    res["gagal"] += 1  # data lama (bila ada) dipertahankan
                else:
                    entry[w] = {"rising": r["rising"], "top": r["top"], "diambil": now()}
                    res["diambil"] += 1
                sleep(jeda + random.random() * 3)
            store[trends_web.key(slug, seed)] = entry
            trends_web.write(store)
    return res


# --------------------------------------------------------------------------- pekerjaan latar belakang

_lock = threading.Lock()
_job: dict = {"running": False}


def job_state() -> dict:
    with _lock:
        return dict(_job)


def _set(**kw) -> None:
    with _lock:
        _job.update(kw)


def run_job(platform: str | None, *, ulangi: bool, fetcher_factory=None, ensure=None) -> None:
    ensure = ensure or ensure_chrome
    fetcher = None
    try:
        ensure()
        fetcher = (fetcher_factory or BrowserFetcher)()
        check_login(fetcher.fetch)
        slugs = [platform] if platform else list(core.CORE)
        result = collect(slugs, fetcher.fetch, ulangi=ulangi,
                         progress=lambda n, t, cur: _set(done=n, total=t, current=cur))
        _set(result=result)
    except LoginNeeded as exc:
        _set(error=str(exc), perlu_login=True)
    except RunnerError as exc:
        _set(error=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("Pengambilan data Trends lengkap gagal")
        _set(error=f"Galat tak terduga: {exc}")
    finally:
        if fetcher is not None:
            try:
                fetcher.close()
            except Exception:  # noqa: BLE001
                pass
        _set(running=False, finished_at=time.time())


def start_job(platform: str | None = None, *, ulangi: bool = False, fetcher_factory=None, ensure=None, background: bool = True) -> dict:
    with _lock:
        if _job.get("running"):
            raise Busy("Pengambilan data Trends lengkap sedang berjalan.")
        _job.clear()
        _job.update(running=True, platform=platform, started_at=time.time(), done=0, total=0, current="", error=None,
                    perlu_login=False, result=None)
    args = (platform, )
    kw = dict(ulangi=ulangi, fetcher_factory=fetcher_factory, ensure=ensure)
    if background:
        threading.Thread(target=run_job, args=args, kwargs=kw, name="trends-web", daemon=True).start()
    else:
        run_job(*args, **kw)
    return job_state()
