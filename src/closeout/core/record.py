"""The home record: marks and views worked out from events (design 3.9, R-REC-1, R-REC-2). Pure.

Marks use only the current round's events and ignore undone rounds. A retracted job shows nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from closeout.core.events import Event
from closeout.core.jobs import current_round, status_from_events

MARK_STATES = ("confirmed", "recorded")


class Mark(BaseModel):
    kind: str  # homeowner_confirmed | contractor_confirmed | permit_on_file
    proof_kind: str  # answer | approval | link | permit | upload
    proof_id: str
    picked_by_contractor: bool = False


def mark_order() -> tuple[str, ...]:
    return ("homeowner_confirmed", "contractor_confirmed", "permit_on_file")


def _mark_for(e: Event, rnd: int) -> Mark | None:
    p = e.payload
    if e.kind == "job.homeowner_confirmed" and e.round == rnd:
        return Mark(
            kind="homeowner_confirmed",
            proof_kind="upload" if p.get("via") == "upload" else "answer",
            proof_id=str(p["command_id"]),
        )
    if e.kind == "job.contractor_confirmed" and e.round == rnd:
        via = str(p.get("via", "approval"))
        proof = p.get("approval_id") if via == "approval" else p.get("command_id")
        return Mark(kind="contractor_confirmed", proof_kind=via, proof_id=str(proof))
    if e.kind == "permit.matched":
        # A permit match belongs to the job, not a round: the town's record doesn't change on undo.
        return Mark(
            kind="permit_on_file",
            proof_kind="permit",
            proof_id=str(p["pk"]),
            picked_by_contractor=bool(p.get("picked_by_contractor", False)),
        )
    return None


def marks(events: Sequence[Event]) -> list[Mark]:
    """The marks a job shows now: only once confirmed, and only from the current round.

    An undo moves the job back to `ready`, so an undone round never shows; the next round's
    confirmations have the next round number.
    """
    if status_from_events(events) not in MARK_STATES:
        return []
    rnd = current_round(events)
    found: dict[str, Mark] = {}
    for e in events:
        m = _mark_for(e, rnd)
        if m is not None:
            found[m.kind] = m
    order = mark_order()
    return sorted(found.values(), key=lambda m: order.index(m.kind))


def recorded_with_confirmation(events: Sequence[Event]) -> bool:
    """R-REC-1: a job reaches `recorded` only after a homeowner confirmation in the same round."""
    for i, e in enumerate(events):
        if e.kind == "job.recorded":
            rnd = e.round
            if not any(x.kind == "job.homeowner_confirmed" and x.round == rnd for x in events[:i]):
                return False
    return True


class JobView(BaseModel):
    status: str | None
    round: int
    marks: list[Mark]
    price_visible: bool
    visible: bool


def job_view(events: Sequence[Event], origin: str = "contractor") -> JobView:
    status = status_from_events(events, origin)
    price_visible = False
    for e in events:
        if e.kind == "job.price_visibility_set":
            price_visible = bool(e.payload.get("price_visible"))
    return JobView(
        status=status,
        round=current_round(events),
        marks=marks(events),
        price_visible=price_visible,
        visible=status != "retracted",
    )
