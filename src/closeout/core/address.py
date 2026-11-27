"""Address cleanup and matching (design 3.2, R-IMP-2). Pure.

- Upper-case, collapse spaces, drop the town and ZIP.
- Shorten suffixes (Lane -> LN, Street -> ST, ...).
- Split out the house number (with any letter, like 12A) and the unit (Unit 3, #3, Apt 3).
- Match only when the house number and unit are exactly equal. The street must then be equal,
  or the single close match (rapidfuzz ratio >= 92) among that town's streets.
- Number ranges, several parcels with the same address, or a unit on a condo parcel -> needs a look.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel
from rapidfuzz import fuzz


def suffixes() -> dict[str, str]:
    """Long street suffixes and their short forms. Words already short are left alone."""
    return {
        "STREET": "ST",
        "STR": "ST",
        "AVENUE": "AVE",
        "AV": "AVE",
        "ROAD": "RD",
        "LANE": "LN",
        "DRIVE": "DR",
        "COURT": "CT",
        "PLACE": "PL",
        "BOULEVARD": "BLVD",
        "TERRACE": "TER",
        "TERR": "TER",
        "CIRCLE": "CIR",
        "PARKWAY": "PKWY",
        "HIGHWAY": "HWY",
        "TRAIL": "TRL",
        "SQUARE": "SQ",
    }


def directions() -> dict[str, str]:
    return {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}


# A town, state or ZIP at the end of the line, after a comma or a space.
TOWN_TAIL = re.compile(r"[\s,]+(?:\d{5}(?:-\d{4})?|MANASQUAN|SAMPLETON|NJ|NEW JERSEY)$")
UNIT_RX = re.compile(r"(?:,|\s)\s*(?:UNIT|APT|APARTMENT|STE|SUITE|#)\s*#?\s*([A-Z0-9-]+)\s*$")
NUMBER_RX = re.compile(r"^(\d+[A-Z]?)(?:\s*-\s*(\d+[A-Z]?))?\s+(.*)$")


class Address(BaseModel):
    house_no: str | None
    house_no_end: str | None = None  # set for a number range like 10-12
    street: str
    unit: str | None = None

    @property
    def norm(self) -> str:
        num = self.house_no or ""
        if self.house_no_end:
            num = f"{num}-{self.house_no_end}"
        text = f"{num} {self.street}".strip()
        return f"{text} UNIT {self.unit}" if self.unit else text

    @property
    def is_range(self) -> bool:
        return self.house_no_end is not None


def _clean_text(raw: str) -> str:
    s = re.sub(r"[^A-Z0-9#,\- ]", " ", raw.upper())
    s = re.sub(r"\s+", " ", s).strip(" ,")
    while (shorter := TOWN_TAIL.sub("", s)) != s:
        s = shorter
    return s


def _street_words(street: str) -> str:
    words = street.split()
    sfx, dirs = suffixes(), directions()
    last = len(words) - 1
    out = []
    for i, w in enumerate(words):
        if i == last and w in sfx:
            out.append(sfx[w])
        elif i in (0, last) and w in dirs:
            out.append(dirs[w])
        else:
            out.append(w)
    return " ".join(out)


def parse(raw: str) -> Address:
    """Clean one address line. Never changes or drops the house number or unit."""
    s = _clean_text(raw)
    unit = None
    m = UNIT_RX.search(s)
    if m:
        unit = m.group(1)
        s = s[: m.start()]
    s = " ".join(s.replace(",", " ").split())
    m2 = NUMBER_RX.match(s)
    if not m2:
        return Address(house_no=None, street=_street_words(s), unit=unit)
    return Address(
        house_no=m2.group(1), house_no_end=m2.group(2), street=_street_words(m2.group(3)), unit=unit
    )


def normalise(raw: str) -> str:
    return parse(raw).norm


class ParcelAddress(BaseModel):
    pin: str
    address_norm: str
    house_no: str | None
    street: str
    unit: str | None
    condo: bool = False


@dataclass(frozen=True)
class Match:
    pin: str | None
    reason: (
        str  # matched_exact | matched_close | no_number | number_range | several | condo_unit | none | unsure
    )


def street_of(parcels: Iterable[ParcelAddress]) -> set[str]:
    return {p.street for p in parcels}


def match(raw: str, parcels: Sequence[ParcelAddress], fuzzy_min: float = 92.0) -> Match:
    """Find the one parcel for an address, or say why not."""
    a = parse(raw)
    if a.house_no is None:
        return Match(None, "no_number")
    if a.is_range:
        return Match(None, "number_range")
    same_number = [p for p in parcels if p.house_no == a.house_no and p.unit == a.unit]
    exact = [p for p in same_number if p.street == a.street]
    if exact:
        return _one(exact, a, "matched_exact")
    streets = sorted(street_of(parcels))
    close = [s for s in streets if fuzz.ratio(s, a.street) >= fuzzy_min]
    if len(close) != 1:
        return Match(None, "unsure" if close else "none")
    candidates = [p for p in same_number if p.street == close[0]]
    if not candidates:
        return Match(None, "none")
    return _one(candidates, a, "matched_close")


def _one(candidates: Sequence[ParcelAddress], a: Address, reason: str) -> Match:
    if len(candidates) > 1:
        return Match(None, "several")
    p = candidates[0]
    if a.unit and p.condo:
        return Match(None, "condo_unit")
    return Match(p.pin, reason)
