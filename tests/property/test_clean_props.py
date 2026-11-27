"""S3 properties: cleaners round-trip formatted values and never keep anything out of range (5.3)."""

import os
import unicodedata
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from closeout.core.clean import EMAIL_RX, URL_RX, clean_date, clean_money, clean_text

pytestmark = [pytest.mark.slice("S3"), pytest.mark.req("R-IMP-5")]
N = 2000 if os.environ.get("HYPOTHESIS_PROFILE") == "nightly" else 200
TODAY = date(2026, 11, 27)

cents = st.integers(min_value=0, max_value=500_000_000)


@settings(max_examples=N)
@given(cents, st.sampled_from(["{:,.2f}", "${:,.2f}", "{:.2f}", "$ {:,.2f} USD"]))
def test_formatted_money_round_trips(c: int, fmt: str) -> None:
    amount = Decimal(c) / 100
    assert clean_money(fmt.format(amount)).value == amount


@settings(max_examples=N)
@given(st.decimals(allow_nan=True, allow_infinity=True, places=2))
def test_money_out_of_range_is_never_kept(d: Decimal) -> None:
    c = clean_money(str(d))
    if c.ok:
        assert Decimal(0) <= c.value <= Decimal(5_000_000)  # type: ignore[operator]
    else:
        assert c.value is None


@settings(max_examples=N)
@given(
    st.dates(min_value=date(1900, 1, 1), max_value=date(2100, 1, 1)),
    st.sampled_from(["%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%d %b %Y"]),
)
def test_dates_round_trip_or_are_refused_by_range(d: date, fmt: str) -> None:
    c = clean_date(d.strftime(fmt), TODAY)
    if date(1950, 1, 1) <= d <= TODAY + timedelta(days=30):
        assert c.value == d
    else:
        assert c.value is None


@pytest.mark.req("R-SAFE-1")
@settings(max_examples=N)
@given(st.text(max_size=400))
def test_cleaned_text_never_holds_links_emails_or_control_characters(s: str) -> None:
    c = clean_text(s)
    if c.value is None:
        return
    v = str(c.value)
    assert len(v) <= 200
    assert not EMAIL_RX.search(v) and not URL_RX.search(v) and "://" not in v
    assert not any(unicodedata.category(ch)[0] == "C" for ch in v)
