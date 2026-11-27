"""Build event lists for tests."""

from __future__ import annotations

import uuid
from typing import Any

from closeout.core.events import Event


class Stream:
    def __init__(self, stream: str = "job-1") -> None:
        self.stream = stream
        self.events: list[Event] = []

    def add(self, kind: str, round_: int | None = None, **payload: Any) -> Stream:
        p = dict(payload)
        if round_ is not None:
            p["round"] = round_
        if kind in NEEDS_COMMAND and "command_id" not in p:
            p["command_id"] = str(uuid.uuid4())
        self.events.append(
            Event(event_id=uuid.uuid4(), stream=self.stream, seq=len(self.events) + 1, kind=kind, payload=p)
        )
        return self


NEEDS_COMMAND = {
    "job.homeowner_confirmed",
    "job.answered",
    "job.undone",
    "job.resent",
    "job.approved",
    "job.correction_accepted",
    "job.correction_rejected",
    "job.price_visibility_set",
    "job.corrected",
}


def happy(round_: int = 1, s: Stream | None = None, permit: str | None = "P1") -> Stream:
    """imported -> ready -> approved -> answered (confirm) -> confirmed -> recorded."""
    s = s or Stream()
    if not s.events:
        s.add("job.imported")
        s.add("job.ready", round_)
        if permit:
            s.add("permit.matched", round_, pk=permit)
    else:
        pass
    s.add("job.approved", round_, approval_ids=[str(uuid.uuid4())])
    s.add("job.contractor_confirmed", round_, approval_id=str(uuid.uuid4()))
    s.add("job.answered", round_, answer="confirm")
    s.add("job.homeowner_confirmed", round_)
    return s
