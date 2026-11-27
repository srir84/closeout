"""S1: permit cleaning — dates (R-DATA-4), trades from fees, bad fields."""

from datetime import date
from decimal import Decimal

import pytest

from closeout.core.permitdata import (
    block_lot_key,
    clean_date,
    clean_decimal,
    clean_permit,
    row_hash,
    trades_from_fees,
)

pytestmark = [pytest.mark.slice("S1")]
TODAY = date(2026, 11, 27)


def row(**kw):
    base = {
        "pk": "999",
        "treasurycode": "1329",
        "permitno": "25-0001",
        "block": "35",
        "lot": "8",
        "permittypedesc": "Alteration",
        "buildfee": "0",
        "plumbfee": "0",
        "electfee": "0",
        "firefee": "0",
        "permitdate": "2025-03-01T00:00:00.000",
        "constcost": "1000",
        "squarefeet": "0",
    }
    base.update(kw)
    return base


# Named made-up bad rows (Manasquan has none today).
BAD_DATES = {
    "year 1949": "1949-12-31T00:00:00.000",
    "year 0201 typo": "0201-05-01T00:00:00.000",
    "two years ahead": "2028-01-01T00:00:00.000",
    "not a date": "31/02/2025",
    "garbage": "N/A",
}


@pytest.mark.req("R-DATA-4")
@pytest.mark.parametrize("name", list(BAD_DATES))
def test_bad_dates_are_stored_empty_and_flagged(name: str) -> None:
    p = clean_permit(row(permitdate=BAD_DATES[name]), TODAY)
    assert p.issued_on is None
    assert "issued_on" in p.bad_fields


@pytest.mark.req("R-DATA-4")
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1950-01-01T00:00:00.000", date(1950, 1, 1)),
        ("2027-11-27T00:00:00.000", date(2027, 11, 27)),
        ("2025-03-01", date(2025, 3, 1)),
    ],
)
def test_good_dates_at_the_edges(value: str, expected: date) -> None:
    assert clean_date(value, TODAY) == (expected, False)


def test_one_year_ahead_is_the_last_good_day() -> None:
    assert clean_date("2027-11-28", TODAY) == (None, True)
    assert clean_date("", TODAY) == (None, False)
    assert clean_date(None, TODAY) == (None, False)


@pytest.mark.parametrize(
    ("fees", "trades"),
    [
        ({}, []),
        ({"buildfee": "120"}, ["building"]),
        ({"plumbfee": "45", "electfee": "60"}, ["plumbing", "electrical"]),
        ({"firefee": "0.01"}, ["fire"]),
        ({"buildfee": "-5"}, []),
        ({"buildfee": "abc"}, []),
        (
            {"buildfee": "1", "plumbfee": "1", "electfee": "1", "firefee": "1"},
            ["building", "plumbing", "electrical", "fire"],
        ),
    ],
)
def test_trades_from_fee_columns(fees: dict, trades: list) -> None:
    assert trades_from_fees(row(**fees)) == trades


@pytest.mark.parametrize("bad_no", ["MISSING", "+A", "", None])
def test_bad_permit_numbers_flagged(bad_no) -> None:
    assert "permit_no" in clean_permit(row(permitno=bad_no), TODAY).bad_fields


def test_cost_flags() -> None:
    assert clean_permit(row(constcost="60000000"), TODAY).bad_fields == ["cost_over_50m"]
    assert clean_permit(row(constcost="50000000"), TODAY).bad_fields == []
    p = clean_permit(row(constcost="1,250.50"), TODAY)
    assert p.cost == Decimal("1250.50")
    assert "cost" in clean_permit(row(constcost="lots"), TODAY).bad_fields
    assert clean_decimal("-1") == (None, True)
    assert clean_decimal("NaN") == (None, True)


def test_fields_copied_from_the_source() -> None:
    p = clean_permit(
        row(
            certdate="2025-04-01T00:00:00.000",
            certtypedesc="Certificate of Approval",
            usegroup="R-5",
            processdate="2025-05-01T00:00:00.000",
            squarefeet="120",
            block=" 35 ",
            lot="8a",
        ),
        TODAY,
    )
    assert (p.final_on, p.final_type, p.use_group, p.processed_on) == (
        date(2025, 4, 1),
        "Certificate of Approval",
        "R-5",
        date(2025, 5, 1),
    )
    assert (
        p.sq_ft == Decimal(120) and p.block == "35" and p.lot == "8A" and p.town == "1329" and p.pk == "999"
    )
    assert p.permit_type == "Alteration" and p.permit_no == "25-0001"


def test_row_hash_changes_with_any_field() -> None:
    assert row_hash(row()) == row_hash(row())
    assert row_hash(row()) != row_hash(row(certdate="2025-04-01T00:00:00.000"))
    assert block_lot_key(" 35 ", "8a") == ("35", "8A")


def test_row_hash_vector() -> None:
    import hashlib

    assert row_hash({"b": 1, "a": "x"}) == hashlib.sha256(b'{"a":"x","b":1}').hexdigest()
    assert clean_permit(row(), TODAY).row_hash == row_hash(row())


def test_more_fields() -> None:
    p = clean_permit(row(permitdate="2025-03-01T00:00:00Z", squarefeet="lots", block="4a", lot="12b"), TODAY)
    assert p.issued_on == date(2025, 3, 1)
    assert p.bad_fields == ["sq_ft"]
    assert (p.block, p.lot) == ("4A", "12B")
    q = clean_permit({"pk": 5, "permitno": "1"}, TODAY)
    assert (q.town, q.block, q.lot, q.pk, q.issued_on, q.cost) == ("", "", "", "5", None, None)
    assert clean_decimal("") == (None, False)
    assert block_lot_key("4a ", None) == ("4A", "")  # type: ignore[arg-type]
    assert block_lot_key(None, " 7c") == ("", "7C")  # type: ignore[arg-type]


def test_a_year_ahead_on_the_29th_of_february() -> None:
    assert clean_date("2029-02-28", date(2028, 2, 29)) == (date(2029, 2, 28), False)
    assert clean_date("2027-02-28", date(2026, 2, 28)) == (date(2027, 2, 28), False)
