"""Cleaning permit rows from data.nj.gov `w9se-dmra` (design 3.1, R-DATA-1, R-DATA-4). Pure."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel

BAD_PERMIT_NUMBERS = ("MISSING", "+A")
COST_FLAG_OVER = Decimal(50_000_000)


def fee_trades() -> list[tuple[str, str]]:
    """Fee column -> trade. A fee over zero means the permit covers that trade."""
    return [
        ("buildfee", "building"),
        ("plumbfee", "plumbing"),
        ("electfee", "electrical"),
        ("firefee", "fire"),
    ]


class CleanPermit(BaseModel):
    pk: str
    town: str
    permit_no: str | None
    block: str
    lot: str
    permit_type: str | None
    trades: list[str]
    use_group: str | None
    issued_on: date | None
    final_on: date | None
    final_type: str | None
    cost: Decimal | None
    sq_ft: Decimal | None
    processed_on: date | None
    row_hash: str
    bad_fields: list[str]


def row_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def clean_date(value: Any, today: date) -> tuple[date | None, bool]:
    """A source date, or (None, True) when it's unreadable or outside 1950 .. today + 1 year."""
    if value in (None, ""):
        return None, False
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "")).date()
    except ValueError:
        return None, True
    latest = date(today.year + 1, today.month, min(today.day, 28))
    if d < date(1950, 1, 1) or d > latest:
        return None, True
    return d, False


def clean_decimal(value: Any) -> tuple[Decimal | None, bool]:
    if value in (None, ""):
        return None, False
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None, True
    if not d.is_finite() or d < 0:
        return None, True
    return d, False


def trades_from_fees(row: dict[str, Any]) -> list[str]:
    out = []
    for col, trade in fee_trades():
        fee, bad = clean_decimal(row.get(col))
        if not bad and fee is not None and fee > 0:
            out.append(trade)
    return out


def text(value: Any) -> str | None:
    s = str(value).strip() if value is not None else ""
    return s or None


def clean_permit(row: dict[str, Any], today: date) -> CleanPermit:
    bad: list[str] = []
    dates: dict[str, date | None] = {}
    for ours, src in (("issued_on", "permitdate"), ("final_on", "certdate"), ("processed_on", "processdate")):
        d, flagged = clean_date(row.get(src), today)
        dates[ours] = d
        if flagged:
            bad.append(ours)
    cost, bad_cost = clean_decimal(row.get("constcost"))
    if bad_cost:
        bad.append("cost")
    elif cost is not None and cost > COST_FLAG_OVER:
        bad.append("cost_over_50m")
    sq_ft, bad_sq = clean_decimal(row.get("squarefeet"))
    if bad_sq:
        bad.append("sq_ft")
    permit_no = text(row.get("permitno"))
    if permit_no is None or permit_no.upper() in BAD_PERMIT_NUMBERS:
        bad.append("permit_no")
    return CleanPermit(
        pk=str(row["pk"]),
        town=str(row.get("treasurycode", "")),
        permit_no=permit_no,
        block=(text(row.get("block")) or "").upper(),
        lot=(text(row.get("lot")) or "").upper(),
        permit_type=text(row.get("permittypedesc")),
        trades=trades_from_fees(row),
        use_group=text(row.get("usegroup")),
        issued_on=dates["issued_on"],
        final_on=dates["final_on"],
        final_type=text(row.get("certtypedesc")),
        cost=cost,
        sq_ft=sq_ft,
        processed_on=dates["processed_on"],
        row_hash=row_hash(row),
        bad_fields=bad,
    )


def block_lot_key(block: str, lot: str) -> tuple[str, str]:
    return (block or "").strip().upper(), (lot or "").strip().upper()
