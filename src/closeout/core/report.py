"""The sale report and who sees what (design 3.12, R-REP-1, R-REP-2). Pure.

The access table is a function, so tests and mutation reach every cell. Pages and downloads both use it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

ROLES = ("owner", "buyers_agent", "buyer", "inspector", "contractor", "answer_link")
SHARE_ROLES = ("buyers_agent", "buyer", "inspector")
FIELDS = (
    "work",
    "contractor",
    "price",
    "invoice_file",
    "town_reply_file",
    "homeowner_contact",
    "permit_history",
)


def allowed(
    field: str, role: str, owner_allows_price: bool = False, homeowner_allowed_price: bool = False
) -> bool:
    """One cell of the access table in design 3.12."""
    if role not in ROLES or field not in FIELDS:
        return False
    if field in ("work", "contractor"):
        return True
    if field == "price":
        if role in ("owner", "contractor", "answer_link"):
            return True
        return owner_allows_price and homeowner_allowed_price
    if field == "invoice_file":
        return role in ("owner", "contractor")
    if field == "town_reply_file":
        return role == "owner"
    if field == "homeowner_contact":
        return role in ("owner", "contractor")
    if field == "permit_history":
        return role in ("owner", "buyers_agent", "buyer", "inspector", "contractor")
    return False


class ReportJob(BaseModel):
    job_id: str
    work_type: str
    finished: date | None
    contractor_name: str
    contractor_license: str
    marks: list[str]
    warranty_years: int | None
    price: Decimal | None
    price_visible: bool  # the homeowner's choice when they answered


class ReportPermit(BaseModel):
    permit_no: str | None
    permit_type: str | None
    trades: list[str]
    issued_on: date | None
    final_on: date | None
    town_result: str | None = (
        None  # from asking the town (S11): final_on_file / no_record / unclear / no_reply
    )
    town_result_date: date | None = None


class ShownJob(BaseModel):
    work_type: str
    finished: date | None
    contractor_name: str
    contractor_license: str
    marks: list[str]
    warranty_years: int | None
    price: Decimal | None


class Report(BaseModel):
    address: str
    jobs: list[ShownJob]
    permits: list[ReportPermit] | None
    role: str


def build(
    address: str,
    jobs: list[ReportJob],
    permits: list[ReportPermit],
    role: str,
    owner_allows_price: bool = False,
) -> Report:
    """Only recorded work with at least the homeowner's confirmation is in the report. Hidden values are absent."""
    shown = []
    for j in jobs:
        price_ok = allowed("price", role, owner_allows_price, j.price_visible)
        shown.append(
            ShownJob(
                work_type=j.work_type,
                finished=j.finished,
                contractor_name=j.contractor_name,
                contractor_license=j.contractor_license,
                marks=list(j.marks),
                warranty_years=j.warranty_years,
                price=j.price if price_ok else None,
            )
        )
    return Report(
        address=address, jobs=shown, permits=permits if allowed("permit_history", role) else None, role=role
    )
