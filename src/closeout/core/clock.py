"""Virtual time (design 3.3). Pure: callers pass in every instant.

virtual(t) = epoch_virtual + (t - epoch_real) * scale

Virtual time is for business waits (reminders, town waits, business days, daily limits).
Security expiries never use it: they use the database's now() when checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, field_validator


class Clock(BaseModel):
    epoch_real: datetime
    epoch_virtual: datetime
    scale: float

    @field_validator("scale")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("scale must be positive")
        return v

    @field_validator("epoch_real", "epoch_virtual")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("clock epochs must be timezone-aware")
        return v

    def virtual(self, t: datetime) -> datetime:
        """The virtual instant that real instant t stands for."""
        return self.epoch_virtual + (t - self.epoch_real) * self.scale

    def real(self, v: datetime) -> datetime:
        """The real instant at which virtual instant v happens."""
        return self.epoch_real + (v - self.epoch_virtual) / self.scale

    def real_wait(self, virtual_wait: timedelta, floor: timedelta | None = None) -> timedelta:
        """How long to wait in real time for a virtual wait, never less than the floor."""
        if virtual_wait < timedelta(0):
            raise ValueError("wait must not be negative")
        wait = virtual_wait / self.scale
        return wait if floor is None else max(wait, floor)

    def real_until(
        self, now_real: datetime, virtual_target: datetime, floor: timedelta | None = None
    ) -> timedelta:
        """Real time from now until a virtual target (zero if it has passed), with an optional floor."""
        left = max(self.real(virtual_target) - now_real, timedelta(0))
        return left if floor is None else max(left, floor)


@dataclass(frozen=True)
class Waits:
    """The waits from the clock table in design 3.3, in virtual time unless noted."""

    reminder_1: timedelta = timedelta(days=3)
    reminder_2: timedelta = timedelta(days=7)
    last_wait: timedelta = timedelta(days=7)
    late_window: timedelta = timedelta(days=30)
    town_first_wait_bdays: int = 7
    town_second_wait_bdays: int = 5
    town_late_window: timedelta = timedelta(days=60)
    backup_poll: timedelta = timedelta(minutes=10)
    backup_poll_floor: timedelta = timedelta(seconds=5)  # real
    undo_window: timedelta = timedelta(minutes=10)
    undo_floor: timedelta = timedelta(seconds=60)  # real
    approval_expiry: timedelta = timedelta(days=30)  # real, never scaled
    answer_link_expiry: timedelta = timedelta(days=30)  # real, never scaled
    share_link_expiry: timedelta = timedelta(days=14)  # real, never scaled


WAITS = Waits()
