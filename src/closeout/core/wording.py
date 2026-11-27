"""Words the product never uses (R-COR-3). Pure. Runs on every page and email in tests, and in the sender."""

from __future__ import annotations

import re


def banned() -> tuple[str, ...]:
    return ("open permit", "unpermitted", "un-permitted", "illegal", "violation")


def check_text(s: str) -> list[str]:
    """The banned phrases found in s (case and spacing ignored)."""
    low = re.sub(r"\s+", " ", s.lower())
    return [b for b in banned() if b in low]
