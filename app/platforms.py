"""Daftar platform. Setiap platform punya dashboard sendiri (/p/<slug>) dan kata pencarian untuk berita.

``news`` adalah kata yang dicari di Google Berita untuk platform itu (mis. JadiASN -> "ASN").
"""
from __future__ import annotations

from typing import NamedTuple


class Platform(NamedTuple):
    slug: str
    name: str
    news: str


PLATFORMS: tuple[Platform, ...] = (
    Platform("jagoukp", "JagoUKP", "UKP"),
    Platform("jadiasn", "JadiASN", "ASN"),
    Platform("jadibumn", "JadiBUMN", "BUMN"),
    Platform("jadicerebrum", "Cerebrum", "Cerebrum"),
    Platform("jadipppk", "JadiPPPK", "PPPK"),
    Platform("jadiprajurit", "JadiPRAJURIT", "PRAJURIT"),
    Platform("jadipolisi", "JadiPOLISI", "POLISI"),
    Platform("jadipcpm", "JadiPCPM", "PCPM"),
    Platform("jagotpa", "JagoTPA", "TPA"),
    Platform("jadibeasiswa", "JadiBEASISWA", "BEASISWA"),
    Platform("jadisekdin", "JadiSEKDIN", "SEKDIN"),
    Platform("psikoteskerja", "Psikoteskerja", "Psikotes kerja"),
    Platform("toeflacademy", "ToeflAcademy", "TOEFL"),
    Platform("jadiojk", "JadiOJK", "OJK"),
    Platform("jadippg", "JadiPPG", "PPG"),
)

_BY_SLUG = {p.slug: p for p in PLATFORMS}


def get(slug: str) -> Platform | None:
    return _BY_SLUG.get((slug or "").strip().lower())


def public(p: Platform) -> dict:
    return {"slug": p.slug, "name": p.name, "news": p.news}
