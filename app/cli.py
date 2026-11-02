"""Perintah baris untuk pengambilan data terjadwal (dipakai GitHub Actions).

    python -m app.cli collect --geo ID          # ambil tren hari ini, perbarui CSV
    python -m app.cli import --csv data/snapshots.csv
    python -m app.cli export --csv data/snapshots.csv

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
