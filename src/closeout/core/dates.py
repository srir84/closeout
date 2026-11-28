"""Business days (design 3.2): weekends and NJ state holidays from a fixed list (2024-2030) are skipped.

After 2030 only weekends are skipped. Always used on virtual dates (design 3.3). Pure.
"""

from __future__ import annotations

from datetime import date, timedelta


def nj_holidays() -> frozenset[date]:
    """New Jersey state holidays as observed, 2024-2030 (fixed list)."""
    days = {
        2024: [
            "01-01",
            "01-15",
            "02-12",
            "02-19",
            "03-29",
            "05-27",
            "06-19",
            "07-04",
            "09-02",
            "10-14",
            "11-05",
            "11-11",
            "11-28",
            "11-29",
            "12-25",
        ],
        2025: [
            "01-01",
            "01-20",
            "02-12",
            "02-17",
            "04-18",
            "05-26",
            "06-19",
            "07-04",
            "09-01",
            "10-13",
            "11-04",
            "11-11",
            "11-27",
            "11-28",
            "12-25",
        ],
        2026: [
            "01-01",
            "01-19",
            "02-12",
            "02-16",
            "04-03",
            "05-25",
            "06-19",
            "07-03",
            "09-07",
            "10-12",
            "11-03",
            "11-11",
            "11-26",
            "11-27",
            "12-25",
        ],
        2027: [
            "01-01",
            "01-18",
            "02-12",
            "02-15",
            "03-26",
            "05-31",
            "06-18",
            "07-05",
            "09-06",
            "10-11",
            "11-02",
            "11-11",
            "11-25",
            "11-26",
            "12-24",
        ],
        2028: [
            "01-01",
            "01-17",
            "02-11",
            "02-21",
            "04-14",
            "05-29",
            "06-19",
            "07-04",
            "09-04",
            "10-09",
            "11-07",
            "11-10",
            "11-23",
            "11-24",
            "12-25",
        ],
        2029: [
            "01-01",
            "01-15",
            "02-12",
            "02-19",
            "03-30",
            "05-28",
            "06-19",
            "07-04",
            "09-03",
            "10-08",
            "11-06",
            "11-12",
            "11-22",
            "11-23",
            "12-25",
        ],
        2030: [
            "01-01",
            "01-21",
            "02-12",
            "02-18",
            "04-19",
            "05-27",
            "06-19",
            "07-04",
            "09-02",
            "10-14",
            "11-05",
            "11-11",
            "11-28",
            "11-29",
            "12-25",
        ],
    }
    return frozenset(date.fromisoformat(f"{y}-{md}") for y, mds in days.items() for md in mds)


LAST_LISTED_YEAR = 2030


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nj_holidays()


def add_business_days(start: date, n: int) -> date:
    """The date n business days after start (start itself doesn't count). n must be at least 1."""
    if n < 1:
        raise ValueError("n must be at least 1")
    d = start
    left = n
    while left:
        d += timedelta(days=1)
        if is_business_day(d):
            left -= 1
    return d


def beyond_holiday_list(d: date) -> bool:
    """True when only weekends can be skipped (the caller logs a warning)."""
    return d.year > LAST_LISTED_YEAR
