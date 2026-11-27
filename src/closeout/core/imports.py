"""Contractor import files: the CSV columns and their checks (design 3.16, R-IMP-1). Pure."""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from closeout.core.clean import clean_date, clean_money, work_types

MAX_JOBS = 50


def columns() -> tuple[tuple[str, bool], ...]:
    """(column, required)."""
    return (
        ("job_ref", True),
        ("homeowner_name", True),
        ("homeowner_email", True),
        ("job_address", True),
        ("work_type", True),
        ("same_size_replacement", False),
        ("detached_1_2_family", False),
        ("finish_date", False),
        ("total", False),
        ("invoice_file", True),
    )


class Row(BaseModel):
    line: int
    job_ref: str
    homeowner_name: str
    homeowner_email: str
    job_address: str
    work_type: str
    same_size_replacement: bool | None
    detached_1_2_family: bool | None
    finish_date: date | None
    total: Decimal | None
    invoice_file: str


class Rejected(BaseModel):
    line: int
    job_ref: str | None
    reason: str  # a code shown with a plain message


class Parsed(BaseModel):
    rows: list[Row]
    rejected: list[Rejected]
    error: str | None = None


def messages() -> dict[str, str]:
    return {
        "missing_header": "The first line must name the columns.",
        "missing_columns": "Some required columns are missing.",
        "too_many_jobs": f"A batch can have at most {MAX_JOBS} jobs.",
        "not_utf8": "The CSV file must be UTF-8 text.",
        "missing_value": "A required value is empty.",
        "bad_email": "The homeowner email must be a made-up address ending in .example or .test.",
        "bad_work_type": "The work type must be one of: " + ", ".join(work_types()) + ".",
        "bad_yes_no": "Use yes or no.",
        "statement_not_allowed": "Same-size and detached-home statements are only for roof and window jobs.",
        "bad_date": "The finish date isn't a date we can read.",
        "bad_total": "The total isn't an amount we can read.",
        "duplicate_ref": "This job number appears twice in the file.",
        "already_imported": "This job number was already imported.",
        "missing_file": "The invoice file named in this row wasn't uploaded.",
        "bad_file": "The invoice file was refused.",
        "text_too_long": "A value is longer than 200 characters.",
    }


EMAIL_OK = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.(example|test)$")


def yes_no(v: str) -> bool | None | str:
    s = v.strip().lower()
    if s == "":
        return None
    if s in ("yes", "y", "true"):
        return True
    if s in ("no", "n", "false"):
        return False
    return "bad"


def parse_csv(data: bytes, today: date, already: set[str] | None = None) -> Parsed:
    already = already or set()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return Parsed(rows=[], rejected=[], error="not_utf8")
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    if not header:
        return Parsed(rows=[], rejected=[], error="missing_header")
    if any(c not in header for c, req in columns() if req):
        return Parsed(rows=[], rejected=[], error="missing_columns")
    raw = [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader]
    if len(raw) > MAX_JOBS:
        return Parsed(rows=[], rejected=[], error="too_many_jobs")
    rows: list[Row] = []
    rejected: list[Rejected] = []
    seen: set[str] = set()
    for i, r in enumerate(raw, start=2):
        ref = r.get("job_ref") or None
        reason = check_row(r, today)
        if reason is None and ref in seen:
            reason = "duplicate_ref"
        if reason is None and ref in already:
            reason = "already_imported"
        if reason is not None:
            rejected.append(Rejected(line=i, job_ref=ref, reason=reason))
            continue
        assert ref is not None
        seen.add(ref)
        rows.append(to_row(i, r, today))
    return Parsed(rows=rows, rejected=rejected)


def check_row(r: dict[str, str], today: date) -> str | None:
    for c, req in columns():
        if req and not r.get(c):
            return "missing_value"
        if len(r.get(c, "")) > 200:
            return "text_too_long"
    if not EMAIL_OK.match(r["homeowner_email"].lower()):
        return "bad_email"
    if r["work_type"].strip().lower() not in work_types():
        return "bad_work_type"
    for c in ("same_size_replacement", "detached_1_2_family"):
        v = yes_no(r.get(c, ""))
        if v == "bad":
            return "bad_yes_no"
        if v is not None and r["work_type"].strip().lower() not in ("roof", "windows"):
            return "statement_not_allowed"
    if r.get("finish_date") and not clean_date(r["finish_date"], today).ok:
        return "bad_date"
    if r.get("total") and not clean_money(r["total"]).ok:
        return "bad_total"
    return None


def to_row(line: int, r: dict[str, str], today: date) -> Row:
    ss, dd = yes_no(r.get("same_size_replacement", "")), yes_no(r.get("detached_1_2_family", ""))
    fd = clean_date(r["finish_date"], today).value if r.get("finish_date") else None
    tot = clean_money(r["total"]).value if r.get("total") else None
    return Row(
        line=line,
        job_ref=r["job_ref"],
        homeowner_name=r["homeowner_name"],
        homeowner_email=r["homeowner_email"].strip().lower(),
        job_address=r["job_address"],
        work_type=r["work_type"].strip().lower(),
        same_size_replacement=ss if isinstance(ss, bool) else None,
        detached_1_2_family=dd if isinstance(dd, bool) else None,
        finish_date=fd if isinstance(fd, date) else None,
        total=tot if isinstance(tot, Decimal) else None,
        invoice_file=r["invoice_file"],
    )
