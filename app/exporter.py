"""Mengubah baris database menjadi teks CSV untuk diunduh (dibuka di Excel)."""
from __future__ import annotations

import csv
import io

from .db import CSV_FIELDS

# Sel yang diawali karakter ini dibaca Excel/Sheets sebagai rumus. Judul berita
# berasal dari luar, jadi diberi awalan apostrof agar tidak dieksekusi.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value):
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def rows_to_csv(rows: list[dict], sanitize: bool = True) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {k: (_safe(row.get(k, "")) if sanitize else row.get(k, "")) for k in CSV_FIELDS}
        )
    return buf.getvalue()
