"""Membaca jawaban antarmuka Google Trends yang baru (permintaan ``fXqlme`` pada ``batchexecute``).

Antarmuka ini memberi sampai 30 baris Rising dan 30 baris Top untuk satu kata, masing-masing dengan angka perubahan
(kolom "Change" di situs), jauh lebih banyak daripada jalur lama (``widgetdata/relatedsearches``: 3 sampai 8 Rising).
Jalur ini internal dan tidak didokumentasikan: bentuknya bisa berubah, jadi parser menolak (bukan menebak) bila bentuk
jawabannya tidak dikenali.

Bentuk jawaban: ``[[term, rising, top]]`` dengan
  rising = [[query, perubahan_persen, minat], ...]   (urut perubahan, terbesar dulu)
  top    = [[query, minat, perubahan_persen], ...]   (urut minat, terbesar dulu)
"""
from __future__ import annotations

import json

RPC = "fXqlme"


class WebFormatError(ValueError):
    pass


def _chunks(body: str):
    """Potongan JSON dari jawaban batchexecute (awalan ``)]}'``, lalu panjang dan JSON bergantian)."""
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("[["):
            try:
                yield json.loads(line)
            except ValueError:
                continue


def parse_fxqlme(body: str) -> dict:
    """Jawaban mentah ``fXqlme`` -> {"term", "rising": [...], "top": [...]}. Galat WebFormatError bila tak dikenali."""
    for chunk in _chunks(body):
        for item in chunk:
            if isinstance(item, list) and len(item) > 2 and item[0] == "wrb.fr" and item[1] == RPC and item[2]:
                try:
                    inner = json.loads(item[2])
                    term, rising, top = inner[0][0][0], inner[0][0][1], inner[0][0][2]
                    return {
                        "term": term,
                        "rising": [{"query": r[0], "change": int(r[1]), "interest": int(r[2])} for r in rising],
                        "top": [{"query": r[0], "interest": int(r[1]), "change": int(r[2])} for r in top],
                    }
                except (ValueError, IndexError, TypeError, KeyError) as exc:
                    raise WebFormatError(f"bentuk jawaban fXqlme tidak dikenali: {exc}") from exc
    raise WebFormatError("jawaban fXqlme tidak ditemukan")


def format_change(value: int) -> str:
    """Seperti kolom Change di situs: +130%."""
    return f"{value:+d}%"
