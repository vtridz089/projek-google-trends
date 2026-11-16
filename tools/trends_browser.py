"""Ambil Rising/Top queries LENGKAP (antarmuka Trends baru) lewat browser sungguhan di komputer Anda.

Pakai:
  pip install playwright && playwright install chromium
  python tools/trends_browser.py "soal cpns" [--kurun now+1-d] [--chrome] [--cdp http://localhost:9222]

Membuka halaman Explore Google Trends (jendela browser tampil), menunggu jawaban ``fXqlme``, lalu mencetak jumlah dan
isi Rising/Top. ``--chrome`` memakai Google Chrome yang terpasang (bukan Chromium bawaan Playwright).
"""
import argparse
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.trends_web import WebFormatError, format_change, parse_fxqlme  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("term")
ap.add_argument("--kurun", default="now 1-d", help='mis. "now 1-d", "now 7-d", "today 3-m"')
ap.add_argument("--geo", default="ID")
ap.add_argument("--chrome", action="store_true")
ap.add_argument("--cdp", metavar="URL", help="sambung ke Chrome Anda yang sudah berjalan, mis. http://localhost:9222 "
                "(Chrome dijalankan dengan --remote-debugging-port=9222 --user-data-dir=<folder khusus>)")
a = ap.parse_args()
url = "https://trends.google.com/explore?" + urllib.parse.urlencode({"q": a.term, "geo": a.geo, "date": a.kurun, "hl": "en"})
bodies = []
with sync_playwright() as p:
    if a.cdp:
        b = p.chromium.connect_over_cdp(a.cdp)
        ctx = b.contexts[0] if b.contexts else b.new_context()
        pg = ctx.new_page()
    else:
        b = p.chromium.launch(headless=False, channel="chrome" if a.chrome else None)
        pg = b.new_context(locale="en-US").new_page()
    seen = []

    def on_resp(r):
        if "trends.google.com" not in r.url or not any(k in r.url for k in ("batchexecute", "/api/")):
            return
        try:
            body = r.text()
        except Exception:  # noqa: BLE001
            body = ""
        seen.append((r.url.split("?")[0].split("trends.google.com")[-1], r.status))
        if "fXqlme" in r.url or '"fXqlme"' in body:
            bodies.append(body)

    pg.on("response", on_resp)
    for attempt in range(4):
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001 - halaman lambat: tetap tunggu jawaban datanya
            print("halaman lambat dibuka:", str(e).splitlines()[0][:80])
        for _ in range(40):          # tunggu sampai 40 detik untuk jawaban fXqlme
            if bodies:
                break
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(1500)
        if bodies or "429" not in pg.title():
            break
        pg.wait_for_timeout(8000 * (attempt + 1))
    title = pg.title()
    try:
        rows = pg.locator("text=/\\d+.{1,3}\\d+ of \\d+/").all_inner_texts()
    except Exception:  # noqa: BLE001
        rows = []
    pg.close() if a.cdp else b.close()
print("judul halaman:", title)
print("penomoran tabel di halaman (mis. '1-10 of 32'):", rows)
print("permintaan data yang terlihat:", sorted(set(seen)))
print("permintaan fXqlme ditangkap:", len(bodies))
for body in bodies:
    try:
        r = parse_fxqlme(body)
    except WebFormatError as e:
        print("GAGAL BACA:", e)
        continue
    print(f"'{r['term']}' {a.kurun}: RISING={len(r['rising'])} TOP={len(r['top'])}")
    print("  rising:", [(x["query"], format_change(x["change"])) for x in r["rising"][:10]])
    print("  top:", [(x["query"], x["interest"]) for x in r["top"][:10]])
if not bodies:
    print("Tidak ada permintaan fXqlme: halaman mungkin memakai tampilan lama atau diblokir (429).")
