"""S3: two-read agreement (R-IMP-3) and the same-contractor check (R-IMP-4)."""

from datetime import date
from decimal import Decimal

import pytest

from closeout.core.compare import decide_reads, name_key, other_contractor, same_contractor, same_value

pytestmark = [pytest.mark.slice("S3"), pytest.mark.req("R-IMP-3")]
TODAY = date(2026, 11, 27)
READ = {
    "contractor_name": "Inlet Roofing Co.",
    "contractor_license": "13VH00077302",
    "contractor_email": "estimates@inletroof.example",
    "customer_name": "Lena Ostrowski",
    "job_address": "11 Sandpiper Dr",
    "work_type": "roof",
    "completion_date": "2025-12-24",
    "total_amount": 19300.0,
    "warranty_years": 10,
}


def by_field(decisions):
    return {d.field: d for d in decisions}


def test_both_agree_after_cleanup_means_kept() -> None:
    b = dict(
        READ,
        contractor_name="INLET ROOFING",
        job_address="11 Sandpiper Drive",
        completion_date="12/24/2025",
        total_amount="$19,300.00",
        customer_name="lena ostrowski",
        contractor_license="13 VH 00077302",
    )
    d = by_field(decide_reads(READ, b, TODAY))
    assert all(x.status == "kept" for x in d.values()), {k: v.reason for k, v in d.items()}
    assert d["total_amount"].value == Decimal("19300.00")


@pytest.mark.parametrize(
    ("field", "other"),
    [
        ("total_amount", 19400.0),
        ("completion_date", "2025-12-23"),
        ("customer_name", "Lena Ostrowsky"),
        ("job_address", "13 Sandpiper Dr"),
        ("work_type", "windows"),
        ("warranty_years", 5),
        ("contractor_license", "13VH00077303"),
    ],
)
def test_a_disagreement_is_asked_with_both_values(field: str, other) -> None:
    d = by_field(decide_reads(READ, dict(READ, **{field: other}), TODAY))[field]
    assert (d.status, d.reason, d.value) == ("asked", "disagree", None)
    assert d.value_a is not None and d.value_b is not None


def test_money_within_a_cent_agrees() -> None:
    assert same_value("total_amount", Decimal("100.00"), Decimal("100.01"))
    assert not same_value("total_amount", Decimal("100.00"), Decimal("100.02"))
    assert not same_value("total_amount", None, Decimal("1"))


def test_invalid_reads_are_asked() -> None:
    d = by_field(decide_reads(dict(READ, total_amount="lots"), READ, TODAY))
    assert (d["total_amount"].status, d["total_amount"].reason) == ("asked", "invalid_a")
    d = by_field(decide_reads(READ, dict(READ, completion_date="1900-01-01"), TODAY))
    assert (d["completion_date"].status, d["completion_date"].reason) == ("asked", "invalid_b")
    d = by_field(decide_reads(READ, None, TODAY))
    assert all(x.status == "asked" for x in d.values())
    d = by_field(decide_reads(None, None, TODAY))
    assert all(x.reason == "invalid_a" for x in d.values())


@pytest.mark.req("R-IMP-3")
def test_light_mode_keeps_no_fact_without_a_person() -> None:
    d = decide_reads(READ, None, TODAY, light=True)
    assert {x.status for x in d} == {"unconfirmed"}
    assert all(x.reason == "one_read" for x in d)
    assert by_field(d)["total_amount"].value == Decimal("19300.00")
    bad = by_field(decide_reads(dict(READ, total_amount="lots"), None, TODAY, light=True))
    assert bad["total_amount"].value is None and bad["total_amount"].status == "unconfirmed"


def test_name_key_drops_company_suffixes_and_punctuation() -> None:
    assert name_key("Tidewater Electric LLC") == name_key("tidewater electric, l.l.c") == "tidewater electric"
    assert name_key("Harbor & Pine Builders, Inc.") == "harbor & pine builders"
    assert name_key("Co Co") == ""


@pytest.mark.req("R-IMP-4")
@pytest.mark.slice("S4")
@pytest.mark.parametrize(
    ("lic", "name", "same"),
    [
        ("13VH01234567", None, True),
        ("13VH01234568", None, True),  # within one character
        ("13VH01234588", None, False),
        (None, "Harbor and Pine Builders", True),
        (None, "Harbor & Pine Builders LLC", True),
        (None, "Bayside Plumbing & Heating", False),
        ("13VH00551920", "Bayside Plumbing & Heating", False),
        (None, None, False),
    ],
)
def test_same_contractor(lic, name, same: bool) -> None:
    assert same_contractor(lic, name, "13VH01234567", "Harbor & Pine Builders") is same
    assert other_contractor(lic, name, "13VH01234567", "Harbor & Pine Builders") is (
        not same and (lic or name) is not None
    )


def test_non_latin_names_are_compared_not_emptied() -> None:
    assert name_key("王芳") == "王芳"
    assert not same_value("customer_name", "王芳", "李明")
    assert same_value("customer_name", "王芳", "王芳")
    assert not same_value("customer_name", "LLC", "Inc.")  # both empty after suffixes: never agree
    assert name_key("José Núñez") == "josé núñez"


@pytest.mark.req("R-IMP-3")
def test_a_total_changed_by_hand_is_asked_even_when_both_reads_agree() -> None:
    from closeout.core.compare import apply_amount_marks

    d = decide_reads(READ, READ, TODAY)
    asked = {
        x.field: x for x in apply_amount_marks(d, {"amount_crossed_out": True, "amount_handwritten": True})
    }
    assert (asked["total_amount"].status, asked["total_amount"].reason) == ("asked", "changed_by_hand")
    assert asked["total_amount"].value is None and asked["completion_date"].status == "kept"
    clean = {
        x.field: x for x in apply_amount_marks(d, {"amount_crossed_out": False, "amount_handwritten": True})
    }
    assert clean["total_amount"].status == "kept"  # handwriting alone (carbon copies) isn't a change
    failed = {x.field: x for x in apply_amount_marks(d, None)}
    assert failed["total_amount"].status == "asked"  # no answer from the look: ask
