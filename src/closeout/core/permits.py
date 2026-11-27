"""Permit match (design 3.2, R-COR-1, R-COR-2). Pure.

For a job, look for permits on the same parcel issued between 365 days before and 14 days after the
finish date, with the right type and the matching trade (from the permit's fee columns).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from pydantic import BaseModel

WINDOW_BEFORE = timedelta(days=365)
WINDOW_AFTER = timedelta(days=14)


class Permit(BaseModel):
    pk: str
    permit_type: str | None
    trades: list[str]
    issued_on: date | None
    final_on: date | None = None
    permit_no: str | None = None


class PermitMatch(BaseModel):
    result: str  # on_file | possible | no_details | none | no_check
    pks: list[str]
    statement_line: bool = False  # roof/windows: show the contractor's own statement (R-COR-2)


def rule(work_type: str) -> tuple[frozenset[str], frozenset[str] | None] | None:
    """(permit types that count, trades of which at least one must be present; None = any trades).

    A function, not a module-level table, so mutation tests reach it. None means: never matched.
    """
    alteration = frozenset({"Alteration"})
    if work_type == "addition":
        return frozenset({"Addition", "New"}), None
    if work_type == "deck":
        return frozenset({"Addition", "Alteration"}), frozenset({"building"})
    if work_type in ("kitchen", "bathroom"):
        return alteration, frozenset({"building", "plumbing", "electrical"})
    if work_type in ("windows", "roof"):
        return alteration, frozenset({"building"})
    if work_type == "hvac":
        return alteration, frozenset({"fire", "plumbing", "electrical"})
    if work_type == "plumbing":
        return alteration, frozenset({"plumbing"})
    if work_type == "electrical":
        return alteration, frozenset({"electrical"})
    return None


def in_window(p: Permit, finish: date) -> bool:
    return p.issued_on is not None and finish - WINDOW_BEFORE <= p.issued_on <= finish + WINDOW_AFTER


def counts(p: Permit, work_type: str) -> bool:
    r = rule(work_type)
    if r is None:
        return False
    types, trades = r
    if p.permit_type not in types:
        return False
    return trades is None or bool(trades & set(p.trades))


def match(
    work_type: str,
    finish: date | None,
    permits: Sequence[Permit],
    stated_like_for_like: bool | None = None,
    stated_detached: bool | None = None,
) -> PermitMatch:
    if rule(work_type) is None:
        return PermitMatch(result="no_check", pks=[])
    if finish is None:
        return PermitMatch(result="none", pks=[])
    window = sorted((p for p in permits if in_window(p, finish)), key=lambda p: (p.issued_on, p.pk))
    hits = [p.pk for p in window if counts(p, work_type)]
    types, _ = rule(work_type) or (frozenset(), None)
    statement = work_type in ("roof", "windows") and stated_like_for_like is True and stated_detached is True
    if len(hits) == 1:
        return PermitMatch(result="on_file", pks=hits)
    if len(hits) > 1:
        return PermitMatch(result="possible", pks=hits)
    blank = [p.pk for p in window if not p.trades and p.permit_type in types]
    if blank:
        return PermitMatch(result="no_details", pks=blank, statement_line=statement)
    return PermitMatch(result="none", pks=[], statement_line=statement)


def trade_words(trades: Sequence[str]) -> str:
    order = ["building", "plumbing", "electrical", "fire"]
    ts = [t for t in order if t in trades]
    if not ts:
        return ""
    if len(ts) == 1:
        return ts[0]
    return ", ".join(ts[:-1]) + " and " + ts[-1]


def headline(result: str) -> str:
    return {
        "on_file": "Town permit on file",
        "possible": "Possible permits",
        "no_details": "Permit with no work details",
        "none": "No matching permit found",
        "no_check": "No permit check for this kind of work",
    }[result]


def sentence(m: PermitMatch, permit: Permit | None) -> str:
    """The plain sentence shown under the result (R-COR-1)."""
    if m.result == "on_file" and permit is not None and permit.issued_on is not None:
        work = trade_words(permit.trades)
        what = f"A town permit with {work} work" if work else "A town permit"
        return f"{what} was issued for this address on {permit.issued_on.strftime('%-d %b %Y')}."
    if m.result == "possible":
        return "More than one town permit could cover this work. The contractor can pick the right one."
    if m.result == "no_details":
        return "A permit was issued for this address in this period. State data doesn't show what work it covered."
    if m.result == "none":
        return "No matching permit found in state data."
    return "No permit check for this kind of work."


STATEMENT_LINE = (
    "The contractor stated this was a same-size replacement on a detached one- or two-family home. "
    "These usually don't need a permit (state rules)."
)
FINAL_MISSING = "Final approval not in state data"
