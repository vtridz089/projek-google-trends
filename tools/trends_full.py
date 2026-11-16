"""Ambil Rising/Top queries LENGKAP (30 + 30 per kata, antarmuka Trends baru) untuk semua keyword inti.

PENTING: daftar lengkap hanya diberikan Google ke sesi yang sudah LOGIN. Di Chrome profil khusus di bawah, login dulu ke
akun Google (disarankan akun khusus, bukan akun pribadi: otomatisasi bisa membuat akun diminta verifikasi atau dibatasi).

Dijalankan di komputer Anda, lewat Chrome yang dibuka dengan port debug (lihat README bagian "Data Trends lengkap"):
  chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\\chrome-trends
  python tools/trends_full.py --cdp http://localhost:9222 [--platform jadiasn] [--ulangi]

Hasil disimpan ke data/trends_web.json (ditulis setelah tiap topik, jadi aman dihentikan dan bisa dilanjutkan).
Topik yang sudah diambil kurang dari --umur jam dilewati, kecuali memakai --ulangi.
Tiap topik membuka 3 halaman (24 jam, 7 hari, 90 hari) dengan jeda; 96 topik = sekitar 1 jam.
"""
import argparse
import os
import random
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import core, keyword_research, trends_web  # noqa: E402
from app.trends_web import WebFormatError, parse_fxqlme  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--cdp", required=True, metavar="URL", help="mis. http://localhost:9222")
ap.add_argument("--platform", help="slug platform (mis. jadiasn); kosong = semua")
ap.add_argument("--geo", default="ID")
ap.add_argument("--umur", type=float, default=20, help="lewati topik yang diambil kurang dari sekian jam lalu (bawaan 20)")
ap.add_argument("--ulangi", action="store_true", help="ambil ulang semua, abaikan umur data")
ap.add_argument("--jeda", type=float, default=6, help="jeda antar halaman, detik (bawaan 6)")
a = ap.parse_args()

from playwright.sync_api import sync_playwright  # noqa: E402

WINDOWS = [(col.replace("naik_", ""), tr) for tr, col in keyword_research.WINDOWS]
slugs = [a.platform] if a.platform else list(core.CORE)
store = trends_web.load().copy()


def fetch(pg, bodies, term, tr):
    url = "https://trends.google.com/explore?" + urllib.parse.urlencode({"q": term, "geo": a.geo, "date": tr, "hl": "en"})
    for attempt in range(4):
        bodies.clear()
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001 - halaman lambat: tetap tunggu jawaban datanya
            print("   (halaman lambat dibuka:", str(e).splitlines()[0][:80], ")")
        for _ in range(40):          # tunggu sampai 40 detik untuk jawaban fXqlme
            if bodies:
                break
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(1500)
        if bodies:
            try:
                return parse_fxqlme(bodies[-1])
            except WebFormatError as e:
                print("   bentuk jawaban tidak dikenali:", e)
                return None
        pg.wait_for_timeout(int(10000 * (attempt + 1)))  # kemungkinan 429: tunggu lalu ulangi
    return None


def main():
    done = failed = skipped = 0
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(a.cdp)
        ctx = b.contexts[0] if b.contexts else b.new_context()
        pg = ctx.new_page()
        bodies = []

        def on_resp(r):
            if "batchexecute" in r.url and "fXqlme" in r.url:
                try:
                    bodies.append(r.text())
                except Exception:  # noqa: BLE001
                    pass
        pg.on("response", on_resp)
        for slug in slugs:
            for seed in core.keywords(slug):
                k = trends_web.key(slug, seed)
                entry = store.get(k, {})
                fresh = all(w in entry and time.time() - float(entry[w].get("diambil", 0)) < a.umur * 3600 for w, _ in WINDOWS)
                if fresh and not a.ulangi:
                    skipped += 1
                    continue
                print(f"[{slug}] {seed}", flush=True)
                for w, tr in WINDOWS:
                    r = fetch(pg, bodies, seed.lower(), tr)
                    if r is None:
                        print(f"   {w}: GAGAL (data lama dipertahankan)")
                        failed += 1
                    else:
                        entry[w] = {"rising": r["rising"], "top": r["top"], "diambil": time.time()}
                        print(f"   {w}: rising={len(r['rising'])} top={len(r['top'])}")
                        done += 1
                    time.sleep(a.jeda + random.random() * 3)
                store[k] = entry
                trends_web.write(store)
        pg.close()
    print(f"selesai: {done} kurun diambil, {failed} gagal, {skipped} topik dilewati (masih segar). Berkas: {trends_web.path()}")


main()
