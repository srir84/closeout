from datetime import UTC, datetime, timedelta

import pytest

from closeout.core.clock import WAITS, Clock

pytestmark = [pytest.mark.slice("S0")]

MONDAY = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
REAL = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("scale", "real_seconds", "virtual_expected"),
    [
        (1, 0, MONDAY),
        (1, 3600, MONDAY + timedelta(hours=1)),
        (17280, 5, MONDAY + timedelta(days=1)),
        (1440, 60, MONDAY + timedelta(days=1)),
        (17280, 15, MONDAY + timedelta(days=3)),
    ],
)
def test_virtual_formula(scale: float, real_seconds: float, virtual_expected: datetime) -> None:
    c = Clock(epoch_real=REAL, epoch_virtual=MONDAY, scale=scale)
    assert c.virtual(REAL + timedelta(seconds=real_seconds)) == virtual_expected
    assert c.real(virtual_expected) == REAL + timedelta(seconds=real_seconds)


@pytest.mark.parametrize(
    ("scale", "wait", "floor", "expected"),
    [
        (1, timedelta(days=3), None, timedelta(days=3)),
        (17280, timedelta(days=3), None, timedelta(seconds=15)),
        (1440, timedelta(days=7), None, timedelta(minutes=7)),
        (17280, WAITS.undo_window, WAITS.undo_floor, timedelta(seconds=60)),
        (1, WAITS.undo_window, WAITS.undo_floor, timedelta(minutes=10)),
        (17280, WAITS.backup_poll, WAITS.backup_poll_floor, timedelta(seconds=5)),
    ],
)
def test_real_wait_with_floor(
    scale: float, wait: timedelta, floor: timedelta | None, expected: timedelta
) -> None:
    c = Clock(epoch_real=REAL, epoch_virtual=MONDAY, scale=scale)
    assert c.real_wait(wait, floor) == expected


def test_real_until_never_negative() -> None:
    c = Clock(epoch_real=REAL, epoch_virtual=MONDAY, scale=17280)
    assert c.real_until(REAL + timedelta(seconds=100), MONDAY) == timedelta(0)
    assert c.real_until(REAL, MONDAY + timedelta(days=1)) == timedelta(seconds=5)
    assert c.real_until(REAL + timedelta(seconds=100), MONDAY, floor=timedelta(seconds=5)) == timedelta(
        seconds=5
    )


def test_bad_clocks_refused() -> None:
    with pytest.raises(ValueError):
        Clock(epoch_real=REAL, epoch_virtual=MONDAY, scale=0)
    with pytest.raises(ValueError):
        Clock(epoch_real=datetime(2026, 1, 1), epoch_virtual=MONDAY, scale=1)  # noqa: DTZ001
    with pytest.raises(ValueError, match="^wait must not be negative$"):
        Clock(epoch_real=REAL, epoch_virtual=MONDAY, scale=1).real_wait(timedelta(seconds=-1))


def test_zero_wait_is_allowed() -> None:
    assert Clock(epoch_real=REAL, epoch_virtual=MONDAY, scale=2).real_wait(timedelta(0)) == timedelta(0)
