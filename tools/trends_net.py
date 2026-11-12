"""Diagnosa: buka halaman Explore Google Trends di browser sungguhan dan catat permintaan jaringan (XHR) yang memuat
Rising/Top queries, untuk melihat endpoint yang dipakai situsnya."""
import json
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

term = sys.argv[1] if len(sys.argv) > 1 else "soal cpns"
date = sys.argv[2] if len(sys.argv) > 2 else "now 1-d"
url = "https://trends.google.com/trends/explore?" + urllib.parse.urlencode({"q": term, "geo": "ID", "date": date, "hl": "id"})
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(locale="id-ID", timezone_id="Asia/Jakarta", viewport={"width": 1400, "height": 1800})
    pg = ctx.new_page()
    seen = []

    def on_resp(r):
        u = r.url
        if "trends.google.com" in u and ("/api/" in u or "batchexecute" in u or "/_/" in u):
            try:
                body = r.text()
            except Exception as e:  # noqa: BLE001
                body = f"<{e}>"
            seen.append((r.request.method, u, r.request.post_data, body))

    pg.on("response", on_resp)
    pg.goto(url, wait_until="networkidle", timeout=90000)
    pg.wait_for_timeout(8000)
    print("TITLE:", pg.title(), "| URL:", pg.url)
    txt = pg.inner_text("body")
    i = txt.find("Rising")
    print("PAGE TEXT around Rising:", txt[max(0, i - 200): i + 900].replace("\n", " | ") if i >= 0 else txt[:500])
    for m, u, post, body in seen:
        print("\n###", m, u[:300])
        if post:
            print("POST:", post[:600])
        print("BODY:", body[:1200].replace("\n", " "))
    b.close()
