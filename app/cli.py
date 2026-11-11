"""Perintah baris untuk pengambilan data terjadwal (dipakai GitHub Actions).

    python -m app.cli collect --geo ID          # ambil tren hari ini, perbarui CSV
    python -m app.cli import --csv data/snapshots.csv
    python -m app.cli export --csv data/snapshots.csv
    python -m app.cli discover [--add TOPIK] [--windows 4]   # pantau Watchlist, simpan ke CSV
    python -m app.cli export-discovery                       # tulis Watchlist + hasil ke CSV

Hanya memakai pustaka standar, jadi tidak perlu `pip install` di runner.
Kode keluar 0 = sukses, 1 = ada negara yang gagal diambil (sehingga job
GitHub Actions ikut gagal dan Anda mendapat notifikasi, bukan gagal diam-diam).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from . import db
from .collector import DEMO_GEO, CollectorError, get_collector, target_geo
from .timeutil import today_local

log = logging.getLogger("trendwatch.cli")


def _default_csv() -> str:
    return os.environ.get("TRENDWATCH_CSV", "data/snapshots.csv")


def cmd_collect(args: argparse.Namespace) -> int:
    db.init_db()
    csv_path = None if args.no_csv else args.csv

    # Muat riwayat dari CSV dulu, supaya status naik/turun dihitung terhadap data lama.
    if csv_path and os.path.exists(csv_path):
        imported, skipped = db.import_csv(csv_path)
        log.info("Riwayat dimuat dari %s: %d baris (%d dilewati)", csv_path, imported, skipped)

    try:
        collector = get_collector(args.source)
    except ValueError as exc:
        print(f"[GAGAL] {exc}", file=sys.stderr)
        return 2

    day = today_local()
    geos = list(dict.fromkeys(target_geo(g, args.source) for g in args.geo))
    failures = 0
    saved_real = 0
    for geo in geos:
        try:
            items = collector.fetch(geo, day)
        except CollectorError as exc:
            print(f"[GAGAL] {geo}: {exc}", file=sys.stderr)
            failures += 1
            continue
        except Exception as exc:  # kesalahan tak terduga, jangan hentikan negara lain
            print(f"[GAGAL] {geo}: kesalahan tak terduga: {exc!r}", file=sys.stderr)
            failures += 1
            continue
        saved = db.save_items(geo, day, items)
        if geo != DEMO_GEO:
            saved_real += 1
        print(f"[OK] {geo}: {saved} keyword tersimpan untuk {day}")

    if csv_path and (saved_real or os.path.exists(csv_path)):
        rows = db.dump_csv(csv_path)
        print(f"CSV diperbarui: {csv_path} ({rows} baris)")

    return 1 if failures else 0


def _watch_csv() -> str:
    return os.environ.get("TRENDWATCH_WATCHLIST_CSV", "data/watchlist.csv")


def _disc_csv() -> str:
    return os.environ.get("TRENDWATCH_DISCOVERY_CSV", "data/discovery.csv")


def cmd_discover(args: argparse.Namespace) -> int:
    """Riset setiap topik aktif di Watchlist dan simpan keyword yang naik ke Daily Discovery.

    Kegagalan satu topik tidak menghentikan yang lain. Kode keluar 1 hanya bila SEMUA topik gagal
    (job Actions merah); kegagalan sebagian diberi anotasi peringatan tetapi job tetap hijau,
    karena Google sering menolak sebagian permintaan dari server."""
    from . import explore_service
    from .explore import ExploreError

    db.init_db()
    db.discovery_init()
    csv_on = not (args.no_csv or args.demo)
    if csv_on:
        loaded = db.discovery_import_csv(_watch_csv(), _disc_csv())
        log.info("Riwayat discovery dimuat: %s", loaded)

    for topic in args.add or []:
        topic = topic.strip()
        if not topic or len(topic) > 100 or "," in topic or "\n" in topic:
            print(f"[GAGAL] Topik tidak valid (satu topik, maks 100 karakter): {topic!r}", file=sys.stderr)
            return 2
        if args.demo:  # uji coba: jangan menyentuh database
            continue
        w = db.watchlist_add(topic, args.watch_geo.upper(), 0)
        print(f"[OK] Watchlist: {w['topic']} ({w['geo']})")

    if args.demo and args.add:
        topics = [{"id": 0, "topic": t.strip(), "geo": args.watch_geo.upper(), "category": 0, "is_active": 1}
                  for t in args.add]
    else:
        topics = [w for w in db.watchlist_list() if w["is_active"]]
    if not topics:
        print("Watchlist kosong atau semua nonaktif. Tambahkan topik dengan --add atau lewat halaman Daily Discovery.")
        if csv_on and args.add is not None:
            db.discovery_dump_csv(_watch_csv(), _disc_csv())
        return 0

    day = today_local().isoformat()
    ok = failed = saved_total = 0
    for w in topics:
        label = f"{w['topic']} ({w['geo']})"
        try:
            res = explore_service.run_research(w["topic"], w["geo"], w["category"], expand=0, demo=args.demo,
                                               refresh=args.refresh, windows=args.windows)
        except ExploreError as exc:
            print(f"[GAGAL] {label}: {exc}" + (f" Saran: {exc.hint}" if exc.hint else ""), file=sys.stderr)
            failed += 1
            continue
        except Exception as exc:  # kesalahan tak terduga pada satu topik tidak boleh menghentikan yang lain
            print(f"[GAGAL] {label}: kesalahan tak terduga: {exc!r}", file=sys.stderr)
            failed += 1
            continue
        if res.get("stale") and not args.demo:
            # Sumber gagal dan yang tersedia hanya cache lama: jangan simpan data kemarin sebagai data hari ini.
            print(f"[GAGAL] {label}: sumber data gagal dan hanya ada cache lama; tidak disimpan.", file=sys.stderr)
            failed += 1
            continue
        counts, days = db.discovery_history_counts(res["topic"], res["geo"], res["category"], day)
        res = explore_service.finalize_research(res, counts, days)
        rows = [{"keyword": r["query"], "source": "research",
                 "trend_category": "trending" if r["breakout"] else "emerging",
                 "interest_score": r["max_value"], "opportunity_score": r["peluang"]["skor"],
                 "trend_change": "Breakout" if r["breakout"] else f"+{r['max_value']}%"}
                for r in res["items"] if r["rising_count"] > 0]
        saved = db.discovery_save(w["id"], day, rows) if not args.demo else 0
        saved_total += saved
        baru = sum(1 for r in res["items"] if r["rising_count"] > 0 and r.get("belum_pernah"))
        print(f"[OK] {label}: {len(rows)} keyword naik, {saved} tersimpan"
              + (f", {baru} belum pernah tersimpan" if days else " (belum ada riwayat pembanding)"))
        for e in res.get("errors", []):
            print(f"::warning::{label}: gagal mengambil {e['scope']}: {e['error']}")
        ok += 1

    if csv_on and (ok or args.add or os.path.exists(_disc_csv())):  # topik yang baru ditambahkan jangan hilang walau Google menolak
        w_rows, d_rows = db.discovery_dump_csv(_watch_csv(), _disc_csv())
        print(f"CSV diperbarui: {_watch_csv()} ({w_rows} topik), {_disc_csv()} ({d_rows} baris)")
    if failed:
        print(f"::warning::{failed} dari {len(topics)} topik gagal diambil hari ini.")
    print(f"Selesai: {ok} topik berhasil, {failed} gagal, {saved_total} keyword tersimpan untuk {day}.")
    return 1 if ok == 0 else 0


def cmd_export_discovery(args: argparse.Namespace) -> int:
    db.init_db()
    db.discovery_init()
    w_rows, d_rows = db.discovery_dump_csv(args.watchlist_csv, args.discovery_csv)
    print(f"Diekspor {w_rows} topik ke {args.watchlist_csv} dan {d_rows} baris ke {args.discovery_csv}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    if not os.path.exists(args.csv):
        print(f"[GAGAL] Berkas tidak ditemukan: {args.csv}", file=sys.stderr)
        return 1
    db.init_db()
    imported, skipped = db.import_csv(args.csv)
    print(f"Diimpor {imported} baris dari {args.csv} ({skipped} dilewati)")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    db.init_db()
    rows = db.dump_csv(args.csv)
    print(f"Diekspor {rows} baris ke {args.csv}")
    return 0


def cmd_core_refresh(args: argparse.Namespace) -> int:
    """Riset ulang keyword inti (saran Google + Trends) dan simpan hasilnya. Cocok dijadwalkan harian (cron / GitHub Actions)."""
    from . import core, platforms
    db.init_db()
    core.init()
    if args.export:
        core.import_json(args.export)  # mulai dari hasil terakhir di repo; yang gagal diambil tetap memakai data lama
    slugs = [p.slug for p in platforms.PLATFORMS] if args.platform in (None, "all") else [args.platform]
    bad = 0
    for slug in slugs:
        if slug not in core.CORE:
            print(f"Platform tidak dikenal: {slug}")
            return 2
        try:
            r = core.refresh(slug, geo=args.geo, demo=args.demo)
        except core.CoreBusy as exc:
            print(exc); return 1
        print(f"{slug}: {r['diperbarui']} diperbarui, {r['gagal']} gagal, {r['dilewati']} dilewati")
        for e in r["errors"]:
            print("  -", e)
        bad += r["gagal"] + r["dilewati"]
    if args.export:
        print(f"{core.export_json(args.export)} topik ditulis ke {args.export}")
    # Beberapa topik memang tidak punya data (pencarian terlalu kecil di Google Trends); itu bukan kegagalan job.
    total = sum(len(core.keywords(sl)) for sl in slugs)
    return 1 if bad > max(2, total // 10) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="TrendWatch CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="ambil tren hari ini dan simpan")
    p.add_argument("--geo", action="append",
                   help="kode negara, boleh diulang (default: TRENDWATCH_GEO atau ID)")
    p.add_argument("--source", default=os.environ.get("TRENDWATCH_SOURCE", "rss"),
                   help="rss (nyata) atau mock (contoh)")
    p.add_argument("--csv", default=_default_csv(), help="berkas CSV riwayat")
    p.add_argument("--no-csv", action="store_true", help="jangan baca/tulis CSV")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("import", help="muat CSV ke database")
    p.add_argument("--csv", default=_default_csv())
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("discover", help="pantau topik Watchlist dan simpan keyword yang naik")
    p.add_argument("--add", action="append", help="tambahkan topik ke Watchlist dulu (boleh diulang)")
    p.add_argument("--watch-geo", default=os.environ.get("TRENDWATCH_GEO", "ID"),
                   help="negara untuk topik yang ditambahkan lewat --add (default: ID)")
    p.add_argument("--windows", type=int, default=int(os.environ.get("TRENDWATCH_DISCOVER_WINDOWS", "4")),
                   choices=[1, 2, 3, 4], help="jumlah rentang waktu per topik (24 jam, 7, 30, 90 hari; default 4)")
    p.add_argument("--refresh", action="store_true", help="abaikan cache")
    p.add_argument("--demo", action="store_true", help="coba alur dengan data contoh; tidak menyimpan apa pun")
    p.add_argument("--no-csv", action="store_true", help="jangan baca/tulis CSV")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("export-discovery", help="tulis Watchlist dan hasil discovery ke CSV")
    p.add_argument("--watchlist-csv", default=_watch_csv())
    p.add_argument("--discovery-csv", default=_disc_csv())
    p.set_defaults(func=cmd_export_discovery)

    p = sub.add_parser("core-refresh", help="ambil ulang keyword inti semua platform (atau satu platform)")
    p.add_argument("--platform", default="all", help="slug platform (mis. jadiasn) atau 'all'")
    p.add_argument("--geo", default="ID")
    p.add_argument("--demo", action="store_true", help="isi dengan data contoh (bukan dari Google)")
    p.add_argument("--export", metavar="BERKAS", help="tulis hasil ke berkas JSON (mis. data/core_research.json)")
    p.set_defaults(func=cmd_core_refresh)

    p = sub.add_parser("export", help="tulis database ke CSV")
    p.add_argument("--csv", default=_default_csv())
    p.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    if args.command == "collect" and not args.geo:
        args.geo = [os.environ.get("TRENDWATCH_GEO", "ID")]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
