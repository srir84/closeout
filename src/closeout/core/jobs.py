"""Job states (design 3.4). Pure. Every move is one function: next_state(state, event)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from closeout.core.events import Event

STATES = (
    "imported",
    "needs_a_look",
    "dropped",
    "ready",
    "asking_homeowner",
    "asking_contractor",
    "answered",
    "confirmed",
    "stopped",
    "no_answer",
    "on_hold",
    "recorded",
    "retracted",
)
FINAL = ("dropped", "stopped", "retracted")
ASKING = ("asking_homeowner", "asking_contractor")


@dataclass(frozen=True)
class Refused:
    reason: str


def moves() -> dict[str, dict[str, str]]:
    """event kind -> {from state -> to state}. A function, not a module constant, so mutation tests reach it."""
    answerable = {
        "asking_homeowner": "answered",
        "asking_contractor": "answered",
        "answered": "answered",
        "no_answer": "answered",
        "on_hold": "answered",
    }
    return {
        "job.needs_a_look": {"imported": "needs_a_look", "answered": "needs_a_look"},
        "job.fact_fixed": {"needs_a_look": "imported"},
        "job.dropped": {"needs_a_look": "dropped"},
        "job.ready": {"imported": "ready"},
        "job.approved": {"ready": "asking_homeowner"},
        "job.answered": answerable,
        "job.homeowner_confirmed": {"answered": "confirmed"},
        "job.correction_accepted": {"answered": "answered"},
        "job.correction_rejected": {"answered": "needs_a_look"},
        "job.stopped": {"answered": "stopped", "on_hold": "stopped"},
        "job.no_answer": {"asking_homeowner": "no_answer", "asking_contractor": "no_answer"},
        "job.on_hold": {"asking_homeowner": "on_hold", "asking_contractor": "on_hold"},
        "job.resent": {"on_hold": "asking_homeowner"},
        "job.undone": {"confirmed": "ready"},
        "job.recorded": {"confirmed": "recorded"},
        "job.retracted": {"recorded": "retracted"},
    }


def info_kinds() -> frozenset[str]:
    """Events that are recorded but don't move the job."""
    return frozenset(
        {
            "job.contractor_confirmed",
            "job.message_delivered",
            "job.corrected",
            "job.price_visibility_set",
            "permit.matched",
        }
    )


def next_state(state: str | None, kind: str) -> str | Refused:
    if state is None:
        if kind == "job.imported":
            return "imported"
        return Refused("not_imported")
    if state in FINAL:
        return Refused("job_closed")
    if kind == "job.imported":
        return Refused("already_imported")
    if kind in info_kinds():
        return state
    table = moves().get(kind)
    if table is None:
        return Refused("unknown_event")
    to = table.get(state)
    if to is None:
        return Refused(f"not_allowed_in_{state}")
    return to


def homeowner_started_next(state: str | None, kind: str) -> str | Refused:
    """S9: a homeowner-started job asks the contractor instead of the homeowner."""
    if state == "ready" and kind == "job.approved":
        return "asking_contractor"
    if state == "on_hold" and kind == "job.resent":
        return "asking_contractor"
    return next_state(state, kind)


def status_from_events(events: Iterable[Event], origin: str = "contractor") -> str | None:
    """Replay a job's events. A move that's refused is an error: the record must always be consistent."""
    step = homeowner_started_next if origin == "homeowner" else next_state
    state: str | None = None
    for e in events:
        to = step(state, e.kind)
        if isinstance(to, Refused):
            raise ValueError(f"event {e.seq} {e.kind} refused in state {state}: {to.reason}")
        state = to
    return state


def current_round(events: Iterable[Event]) -> int:
    return max((e.round for e in events), default=1)


def is_final(state: str | None) -> bool:
    return state in FINAL
