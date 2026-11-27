"""Scoring kept facts against the truth files (evals 4.0, 4.1). Numbers come from what was kept."""

from __future__ import annotations

from datetime import date
from typing import Any

from closeout.core.clean import clean_field, invoice_fields
from closeout.core.compare import FactDecision, same_value

TODAY = date(2026, 11, 27)


def truth_value(field: str, raw: Any) -> Any:
    c = clean_field(field, raw, TODAY)
    return c.value


def fact_right(field: str, value: Any, truth: Any) -> bool:
    tv = truth_value(field, truth)
    if tv is None:
        return value is None
    return same_value(field, value, tv)


def read_right(field: str, raw_read: dict[str, Any] | None, truth: Any) -> bool:
    if not raw_read:
        return False
    return fact_right(field, clean_field(field, raw_read.get(field), TODAY).value, truth)


def score_case(decisions: list[FactDecision], expected: dict[str, Any]) -> dict[str, Any]:
    wrong_kept = [
        d.field
        for d in decisions
        if d.status == "kept" and not fact_right(d.field, d.value, expected[d.field])
    ]
    asked = [d.field for d in decisions if d.status == "asked"]
    return {
        "wrong_kept": wrong_kept,
        "asked": asked,
        "kept": sum(d.status == "kept" for d in decisions),
        "fields": len(invoice_fields()),
    }
