"""Pengumpul Rising/Top Trends per keyword inti: memanggil ``fetch(kata, kurun)`` (kini SerpApi) dan menyimpan hasil ke
``data/trends_web.json`` (lihat ``trends_web``) setelah tiap topik, tanpa menimpa data lama bila pengambilan gagal."""
from __future__ import annotations

import logging
import random
import time

from . import core, keyword_research, trends_web

log = logging.getLogger("cerekey.trends_runner")

WINDOWS = [(col.replace("naik_", ""), tr) for tr, col in keyword_research.WINDOWS]
FRESH_HOURS = 20


class RunnerError(RuntimeError):
    pass


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
