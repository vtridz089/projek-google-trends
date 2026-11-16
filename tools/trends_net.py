"""Diagnosa: buka halaman Explore Google Trends di browser sungguhan dan catat permintaan jaringan (XHR) yang memuat
Rising/Top queries, untuk melihat endpoint yang dipakai situsnya."""
import json
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

term = sys.argv[1] if len(sys.argv) > 1 else "soal cpns"
date = sys.argv[2] if len(sys.argv) > 2 else "now 1-d"
hl = sys.argv[3] if len(sys.argv) > 3 else "id"
url = "https://trends.google.com/trends/explore?" + urllib.parse.urlencode({"q": term, "geo": "ID", "date": date, "hl": hl})
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(locale=("en-US" if hl == "en" else "id-ID"), timezone_id="Asia/Jakarta", viewport={"width": 1400, "height": 1800})
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
    for attempt in range(6):
        pg.goto(url, wait_until="networkidle", timeout=90000)
        pg.wait_for_timeout(6000)
        if "429" not in pg.title():
            break
        print(f"percobaan {attempt + 1}: 429, tunggu lalu ulangi")
        pg.wait_for_timeout(10000 * (attempt + 1))
    pg.wait_for_timeout(6000)
    print("TITLE:", pg.title(), "| URL:", pg.url)
    txt = pg.inner_text("body")
    i = txt.find("Rising")
    print("PAGE TEXT around Rising:", txt[max(0, i - 200): i + 900].replace("\n", " | ") if i >= 0 else txt[:500])
    for m, u, post, body in seen:
        print("\n###", m, u[:300])
        if post:
            print("POST:", post[:600])
        if "relatedsearches" in u:
            print("FULLURL:", urllib.parse.unquote(u)[:1500])
            try:
                data = json.loads(body[body.index("{"):])
                for n, rl in enumerate((data.get("default") or {}).get("rankedList") or []):
                    print(f"RANKEDLIST[{n}] n={len(rl.get('rankedKeyword') or [])}:",
                          [(x.get("query") or (x.get("topic") or {}).get("title"), x.get("value"), x.get("formattedValue")) for x in rl.get("rankedKeyword") or []])
            except Exception as e:  # noqa: BLE001
                print("PARSE:", e)
        if "batchexecute" in u:
            import re
            print("RPC:", re.findall(r"rpcids=([A-Za-z0-9,]+)", u), "| berisi fXqlme:", "fXqlme" in body)
            if "fXqlme" in body:
                print("FXQLME-JUMLAH-BARIS:", body.count('\\"'), "| contoh:", body[:500].replace("\n", " "))
        print("BODY:", body[:300].replace("\n", " "))
    b.close()
