"""S11: business days (design 3.2): never land on a weekend or holiday, always move forward."""

import os
from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from closeout.core.dates import add_business_days, beyond_holiday_list, is_business_day, nj_holidays

pytestmark = [pytest.mark.slice("S11"), pytest.mark.req("R-TOWN-2")]
N = 2000 if os.environ.get("HYPOTHESIS_PROFILE") == "nightly" else 200


@settings(max_examples=N)
@given(st.dates(min_value=date(2024, 1, 1), max_value=date(2031, 12, 31)), st.integers(1, 60))
def test_never_a_weekend_or_holiday_and_always_forward(start: date, n: int) -> None:
    end = add_business_days(start, n)
    assert end > start
    assert end.weekday() < 5 and end not in nj_holidays()
    counted = sum(1 for i in range(1, (end - start).days + 1) if is_business_day(start + timedelta(days=i)))
    assert counted == n


def test_known_dates() -> None:
    assert add_business_days(date(2026, 3, 2), 7) == date(2026, 3, 11)  # Mon + 7 business days
    assert add_business_days(date(2026, 11, 24), 2) == date(
        2026, 11, 30
    )  # over Thanksgiving and Black Friday
    assert add_business_days(date(2026, 12, 24), 1) == date(2026, 12, 28)  # Christmas, then a weekend
    assert not is_business_day(date(2026, 7, 3)) and is_business_day(date(2026, 7, 6))
    assert len(nj_holidays()) == 7 * 15
    assert beyond_holiday_list(date(2031, 1, 1)) and not beyond_holiday_list(date(2030, 12, 31))
    with pytest.raises(ValueError):
        add_business_days(date(2026, 1, 1), 0)
