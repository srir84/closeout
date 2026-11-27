"""S3: cleaning model output (R-IMP-5, R-SAFE-1)."""

from datetime import date
from decimal import Decimal

import pytest

from closeout.core.clean import (
    clean_date,
    clean_email,
    clean_field,
    clean_license,
    clean_money,
    clean_read,
    clean_text,
    clean_warranty,
    clean_work_type,
    work_types,
)

pytestmark = [pytest.mark.slice("S3"), pytest.mark.req("R-IMP-5")]
TODAY = date(2026, 11, 27)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2025-03-28", date(2025, 3, 28)),
        ("03/28/2025", date(2025, 3, 28)),
        ("3/28/25", date(2025, 3, 28)),
        ("March 28, 2025", date(2025, 3, 28)),
        ("Mar 28, 2025", date(2025, 3, 28)),
        ("28 March 2025", date(2025, 3, 28)),
        ("March 28th, 2025", date(2025, 3, 28)),
        ("  2025-03-28 ", date(2025, 3, 28)),
        ("2025/03/28", date(2025, 3, 28)),
        ("03-28-2025", date(2025, 3, 28)),
        ("Mar. 28, 2025", date(2025, 3, 28)),
        ("1950-01-01", date(1950, 1, 1)),
        ("2026-12-27", date(2026, 12, 27)),
    ],
)
def test_dates(raw: str, expected: date) -> None:
    assert clean_date(raw, TODAY).value == expected


@pytest.mark.parametrize(
    ("raw", "flag"),
    [
        ("1949-12-31", "bad_date_range"),
        ("2026-12-28", "bad_date_range"),
        ("soon", "bad_date"),
        ("", "missing"),
        (None, "missing"),
        ("2025-02-30", "bad_date"),
    ],
)
def test_bad_dates(raw, flag: str) -> None:
    c = clean_date(raw, TODAY)
    assert c.value is None and flag in c.flags and not c.ok


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (19300, Decimal("19300.00")),
        (19300.5, Decimal("19300.50")),
        ("$19,300.00", Decimal("19300.00")),
        ("19300", Decimal("19300.00")),
        (" 48,250.10 USD", Decimal("48250.10")),
        (0, Decimal("0.00")),
        (5_000_000, Decimal("5000000.00")),
        ("0.005", Decimal("0.01")),
    ],
)
def test_money(raw, expected: Decimal) -> None:
    c = clean_money(raw)
    assert c.value == expected and c.ok


@pytest.mark.parametrize("raw", [-1, 5_000_000.01, "lots", True, float("nan"), "1e999", ""])
def test_bad_money(raw) -> None:
    assert not clean_money(raw).ok


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13VH01234567", "13VH01234567"),
        ("13 VH 0123 4567", "13VH01234567"),
        ("NJ HIC #13VH01234567", "13VH01234567"),
        ("HIC Reg. No. 13VH01234567", "13VH01234567"),
        ("License: 13vh01234567", "13VH01234567"),
        ("13-VH-01234567", "13VH01234567"),
    ],
)
def test_license(raw: str, expected: str) -> None:
    assert clean_license(raw).value == expected


@pytest.mark.parametrize("raw", ["13VH0123456", "1VH012345678", "13VX01234567", "", None, "13VH012345678"])
def test_bad_license(raw) -> None:
    assert not clean_license(raw).ok


def test_work_types_and_warranty() -> None:
    assert work_types() == (
        "deck",
        "roof",
        "kitchen",
        "bathroom",
        "addition",
        "windows",
        "hvac",
        "plumbing",
        "electrical",
        "other",
    )
    assert clean_work_type(" Roof ").value == "roof"
    assert not clean_work_type("garage").ok and clean_work_type("").flags == ("missing",)
    assert clean_warranty(10).value == 10 and clean_warranty("5").value == 5
    assert clean_warranty(None).value == 0 and "none_stated" in clean_warranty(None).flags
    for bad in (2.5, -1, 51, "ten", True):
        assert not clean_warranty(bad).ok, bad


@pytest.mark.req("R-SAFE-1")
@pytest.mark.parametrize(
    ("raw", "value", "flag"),
    [
        ("Lena Ostrowski", "Lena Ostrowski", None),
        ("Lena https://evil.example/x Ostrowski", "Lena Ostrowski", "link_removed"),
        ("Lena www.evil.example Ostrowski", "Lena Ostrowski", "link_removed"),
        ("Lena pay-me.com Ostrowski", "Lena Ostrowski", "link_removed"),
        ("Lena billing@evil.example", "Lena", "email_removed"),
        ("Lena\x00\x1b[31m Ostrowski", "Lena[31m Ostrowski", "control_chars_removed"),
        ("A" * 250, "A" * 200, "truncated"),
    ],
)
def test_text_loses_links_emails_and_control_characters(raw: str, value: str, flag: str | None) -> None:
    c = clean_text(raw)
    assert c.value == value
    if flag:
        assert flag in c.flags
    assert "http" not in str(c.value) and "@" not in str(c.value)


@pytest.mark.req("R-SAFE-1")
def test_text_that_is_only_a_link_is_refused() -> None:
    assert not clean_text("http://evil.example").ok
    assert not clean_text("  ").ok


def test_email_only_compared_never_trusted() -> None:
    assert clean_email(" Office@HarborPine.example ").value == "office@harborpine.example"
    assert not clean_email("not an email").ok and not clean_email("").ok


def test_clean_read_covers_every_field() -> None:
    out = clean_read(
        {
            "contractor_name": "Inlet Roofing Co.",
            "contractor_license": "13VH00077302",
            "contractor_email": "estimates@inletroof.example",
            "customer_name": "Lena Ostrowski",
            "job_address": "11 Sandpiper Dr",
            "work_type": "roof",
            "completion_date": "2025-12-24",
            "total_amount": 19300.0,
            "warranty_years": 10,
        },
        TODAY,
    )
    assert all(c.ok for c in out.values())
    assert all(not c.ok for c in clean_read(None, TODAY).values())
    with pytest.raises(ValueError):
        clean_field("price", 1, TODAY)


EXACT = [
    (clean_money, (None,), None, ("missing",)),
    (clean_money, ("  ",), None, ("missing",)),
    (clean_money, (True,), None, ("bad_money",)),
    (clean_money, ("lots",), None, ("bad_money",)),
    (clean_money, ("-5",), None, ("bad_money_range",)),
    (clean_money, ("0",), Decimal("0.00"), ()),
    (clean_license, (None,), None, ("missing",)),
    (clean_license, ("  ",), None, ("missing",)),
    (clean_license, ("13VH0",), None, ("bad_license",)),
    (clean_work_type, ("garage",), None, ("bad_work_type",)),
    (clean_work_type, ("  ",), None, ("missing",)),
    (clean_warranty, ("",), 0, ("none_stated",)),
    (clean_warranty, (" None ",), 0, ("none_stated",)),
    (clean_warranty, ("NULL",), 0, ("none_stated",)),
    (clean_warranty, ("ten",), None, ("bad_warranty",)),
    (clean_warranty, (2.5,), None, ("bad_warranty",)),
    (clean_warranty, (True,), None, ("bad_warranty",)),
    (clean_email, ("x",), None, ("bad_email",)),
    (clean_email, (" ",), None, ("missing",)),
    (clean_text, ("  ",), None, ("missing",)),
    (clean_text, ("http://evil.example",), None, ("link_removed", "bad_empty_after_cleanup")),
    (clean_date, ("March 28 2025", TODAY), date(2025, 3, 28), ()),
    (clean_date, ("Mar 28 2025", TODAY), date(2025, 3, 28), ()),
    (clean_date, ("MARCH 28TH, 2025", TODAY), date(2025, 3, 28), ()),
    (clean_date, ("Mar 1st 2025", TODAY), date(2025, 3, 1), ()),
    (clean_date, ("1949-01-01", TODAY), None, ("bad_date_range",)),
]


@pytest.mark.parametrize(("fn", "args", "value", "flags"), EXACT, ids=lambda x: getattr(x, "__name__", None))
def test_exact_values_and_refusal_codes(fn, args, value, flags) -> None:
    c = fn(*args)
    assert (c.value, c.flags) == (value, flags)


@pytest.mark.req("R-SAFE-1")
@pytest.mark.parametrize(
    "raw",
    [
        "ftp://evil.dev/x",
        "javascript:alert(1)",
        "data:text/html,hi",
        "HTTP://X.EXAMPLE",
        "pay-here.shop/now",
        "file:///etc/passwd",
        "evil.app",
        "sftp://host.example",
    ],
)
def test_every_link_form_is_removed(raw: str) -> None:
    c = clean_text("Lena " + raw + " Ostrowski")
    assert c.value == "Lena Ostrowski", c.value
