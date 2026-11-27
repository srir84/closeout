"""S4: the import CSV (design 3.16, R-IMP-1)."""

from datetime import date
from decimal import Decimal

import pytest

from closeout.core.imports import MAX_JOBS, messages, parse_csv

pytestmark = [pytest.mark.slice("S4"), pytest.mark.req("R-IMP-1")]
TODAY = date(2026, 11, 27)
HEAD = "job_ref,homeowner_name,homeowner_email,job_address,work_type,same_size_replacement,detached_1_2_family,finish_date,total,invoice_file\n"


def csv(*rows: str) -> bytes:
    return (HEAD + "\n".join(rows) + "\n").encode()


GOOD = 'A1,Dana Whitfield,dana@sampleton.example,14 Osprey Ln,kitchen,,,2025-03-28,"48,250.00",a1.png'


def test_a_good_row() -> None:
    p = parse_csv(csv(GOOD), TODAY)
    assert p.error is None and p.rejected == []
    r = p.rows[0]
    assert (r.job_ref, r.homeowner_email, r.work_type, r.finish_date, r.total, r.invoice_file) == (
        "A1",
        "dana@sampleton.example",
        "kitchen",
        date(2025, 3, 28),
        Decimal("48250.00"),
        "a1.png",
    )
    assert r.same_size_replacement is None and r.line == 2


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ("A2,,d@x.example,1 A St,deck,,,,,a.png", "missing_value"),
        ("A2,Dana,dana@gmail.com,1 A St,deck,,,,,a.png", "bad_email"),  # publish-check: allow
        ("A2,Dana,dana@x.example,1 A St,garage,,,,,a.png", "bad_work_type"),
        ("A2,Dana,dana@x.example,1 A St,roof,maybe,,,,a.png", "bad_yes_no"),
        ("A2,Dana,dana@x.example,1 A St,deck,yes,,,,a.png", "statement_not_allowed"),
        ("A2,Dana,dana@x.example,1 A St,deck,,,soon,,a.png", "bad_date"),
        ("A2,Dana,dana@x.example,1 A St,deck,,,,lots,a.png", "bad_total"),
        ("A2,Dana,dana@x.example," + "1 A St" * 40 + ",deck,,,,,a.png", "text_too_long"),
    ],
)
def test_a_bad_row_is_listed_and_the_batch_goes_on(row: str, reason: str) -> None:
    p = parse_csv(csv(GOOD, row), TODAY)
    assert [r.job_ref for r in p.rows] == ["A1"]
    assert [(x.line, x.reason) for x in p.rejected] == [(3, reason)]
    assert reason in messages()


def test_roof_statements_are_read() -> None:
    p = parse_csv(csv("R1,Sam,sam@x.example,16 Gull Rock Rd,roof,yes,no,,,r.png"), TODAY)
    assert (p.rows[0].same_size_replacement, p.rows[0].detached_1_2_family) == (True, False)


def test_duplicates_and_already_imported() -> None:
    p = parse_csv(csv(GOOD, GOOD, GOOD.replace("A1", "A9")), TODAY, already={"A9"})
    assert [r.job_ref for r in p.rows] == ["A1"]
    assert [x.reason for x in p.rejected] == ["duplicate_ref", "already_imported"]


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"\xff\xfe\x00bad", "not_utf8"),
        (b"", "missing_header"),
        (b"job_ref,homeowner_name\nA1,Dana\n", "missing_columns"),
        (csv(*[GOOD.replace("A1", f"A{i}") for i in range(MAX_JOBS + 1)]), "too_many_jobs"),
    ],
)
def test_file_level_errors(data: bytes, error: str) -> None:
    p = parse_csv(data, TODAY)
    assert p.error == error and p.rows == []


def test_fifty_jobs_is_allowed_and_bom_is_ok() -> None:
    p = parse_csv(b"\xef\xbb\xbf" + csv(*[GOOD.replace("A1", f"A{i}") for i in range(MAX_JOBS)]), TODAY)
    assert len(p.rows) == MAX_JOBS and p.error is None
