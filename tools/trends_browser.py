"""Ambil Rising/Top queries LENGKAP (antarmuka Trends baru) lewat browser sungguhan di komputer Anda.

Pakai:
  pip install playwright && playwright install chromium
  python tools/trends_browser.py "soal cpns" [--kurun now+1-d] [--chrome]

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
a = ap.parse_args()
url = "https://trends.google.com/trends/explore?" + urllib.parse.urlencode({"q": a.term, "geo": a.geo, "date": a.kurun, "hl": "en"})
bodies = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=False, channel="chrome" if a.chrome else None)
    pg = b.new_context(locale="en-US").new_page()
    pg.on("response", lambda r: bodies.append(r.text()) if "batchexecute" in r.url and "fXqlme" in r.url else None)
    for attempt in range(4):
        pg.goto(url, wait_until="networkidle", timeout=90000)
        pg.wait_for_timeout(5000)
        if bodies or "429" not in pg.title():
            break
        pg.wait_for_timeout(8000 * (attempt + 1))
    b.close()
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
