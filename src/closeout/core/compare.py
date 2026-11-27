"""Two-read agreement and the same-contractor check (design 3.2, R-IMP-3, R-IMP-4). Pure."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from closeout.core import address
from closeout.core.clean import Cleaned, clean_read, invoice_fields

NAME_SUFFIXES = ("llc", "inc", "co", "corp", "company", "ltd")


def name_key(s: str) -> str:
    # Any letter in any script counts, so two different non-Latin names never both become "".
    words = re.sub(r"[^\w& ]|_", " ", s.lower().replace(".", "")).split()
    while words and words[-1] in NAME_SUFFIXES:
        words.pop()
    return " ".join(words)


def same_value(field: str, a: object, b: object) -> bool:
    """Do two cleaned values agree? Dates equal, money within $0.01, names and addresses after cleanup."""
    if a is None or b is None:
        return False
    if field == "total_amount":
        assert isinstance(a, Decimal) and isinstance(b, Decimal)
        return abs(a - b) <= Decimal("0.01")
    if field in ("contractor_name", "customer_name"):
        ka, kb = name_key(str(a)), name_key(str(b))
        return bool(ka) and ka == kb
    if field == "job_address":
        return address.normalise(str(a)) == address.normalise(str(b))
    return a == b


class FactDecision(BaseModel):
    field: str
    status: str  # kept | asked | unconfirmed
    value: object | None
    value_a: object | None
    value_b: object | None
    reason: str  # agree | disagree | invalid_a | invalid_b | one_read


def decide(a: dict[str, Cleaned], b: dict[str, Cleaned] | None) -> list[FactDecision]:
    """Full mode: a fact is kept only when both reads agree after cleanup. Light mode (b is None): unconfirmed."""
    out = []
    for f in invoice_fields():
        ca = a[f]
        if b is None:
            out.append(
                FactDecision(
                    field=f,
                    status="unconfirmed",
                    value=ca.value if ca.ok else None,
                    value_a=ca.value,
                    value_b=None,
                    reason="one_read",
                )
            )
            continue
        cb = b[f]
        if not ca.ok:
            out.append(
                FactDecision(
                    field=f,
                    status="asked",
                    value=None,
                    value_a=ca.value,
                    value_b=cb.value,
                    reason="invalid_a",
                )
            )
        elif not cb.ok:
            out.append(
                FactDecision(
                    field=f,
                    status="asked",
                    value=None,
                    value_a=ca.value,
                    value_b=cb.value,
                    reason="invalid_b",
                )
            )
        elif same_value(f, ca.value, cb.value):
            out.append(
                FactDecision(
                    field=f, status="kept", value=ca.value, value_a=ca.value, value_b=cb.value, reason="agree"
                )
            )
        else:
            out.append(
                FactDecision(
                    field=f, status="asked", value=None, value_a=ca.value, value_b=cb.value, reason="disagree"
                )
            )
    return out


def decide_reads(
    read_a: dict[str, object] | None, read_b: dict[str, object] | None, today: date, light: bool = False
) -> list[FactDecision]:
    a = clean_read(read_a, today)
    return decide(a, None if light else clean_read(read_b, today))


def same_contractor(read_license: str | None, read_name: str | None, our_license: str, our_name: str) -> bool:
    """R-IMP-4: the invoice is the uploader's if the license is within one character, or the name ratio >= 0.85."""
    if read_license and Levenshtein.distance(read_license.upper(), our_license.upper()) <= 1:
        return True
    return bool(read_name) and fuzz.ratio(name_key(read_name or ""), name_key(our_name)) >= 85


def other_contractor(
    read_license: str | None, read_name: str | None, our_license: str, our_name: str
) -> bool:
    """A license or name was read and it isn't the uploader's: needs a look."""
    if not read_license and not read_name:
        return False
    return not same_contractor(read_license, read_name, our_license, our_name)
