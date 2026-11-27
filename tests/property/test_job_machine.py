"""S2 stateful property: random sequences of events never break the job rules (test strategy 5.3).

The code under test (core/jobs.py, core/record.py) is compared with a separate, simple model written
here as plain if/else, from the state diagram in design 3.4.
"""

import os
import uuid

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from closeout.core.events import Event
from closeout.core.jobs import Refused, next_state
from closeout.core.record import marks, recorded_with_confirmation

pytestmark = [pytest.mark.slice("S2"), pytest.mark.req("R-REC-1")]
N = 1000 if os.environ.get("HYPOTHESIS_PROFILE") == "nightly" else 100

KINDS = [
    "job.needs_a_look",
    "job.fact_fixed",
    "job.dropped",
    "job.ready",
    "job.approved",
    "job.answered",
    "job.homeowner_confirmed",
    "job.correction_accepted",
    "job.correction_rejected",
    "job.stopped",
    "job.no_answer",
    "job.on_hold",
    "job.resent",
    "job.undone",
    "job.recorded",
    "job.retracted",
    "job.contractor_confirmed",
    "permit.matched",
    "job.message_delivered",
]


def model_next(s: str, k: str) -> str | None:
    """The simple model. None means refused."""
    if s in ("dropped", "stopped", "retracted"):
        return None
    if k in ("job.contractor_confirmed", "permit.matched", "job.message_delivered"):
        return s
    if s == "imported":
        return {"job.needs_a_look": "needs_a_look", "job.ready": "ready"}.get(k)
    if s == "needs_a_look":
        return {"job.fact_fixed": "imported", "job.dropped": "dropped"}.get(k)
    if s == "ready":
        return "asking_homeowner" if k == "job.approved" else None
    if s == "asking_homeowner":
        return {"job.answered": "answered", "job.no_answer": "no_answer", "job.on_hold": "on_hold"}.get(k)
    if s == "answered":
        return {
            "job.answered": "answered",
            "job.homeowner_confirmed": "confirmed",
            "job.stopped": "stopped",
            "job.correction_accepted": "answered",
            "job.correction_rejected": "needs_a_look",
            "job.needs_a_look": "needs_a_look",
        }.get(k)
    if s == "no_answer":
        return "answered" if k == "job.answered" else None
    if s == "on_hold":
        return {"job.resent": "asking_homeowner", "job.answered": "answered", "job.stopped": "stopped"}.get(k)
    if s == "confirmed":
        return {"job.recorded": "recorded", "job.undone": "ready"}.get(k)
    if s == "recorded":
        return "retracted" if k == "job.retracted" else None
    raise AssertionError(s)


class JobMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.state = "imported"
        self.round = 1
        self.events = [Event(event_id=uuid.uuid4(), stream="job-x", seq=1, kind="job.imported", payload={})]
        self.sent_after_stop = False

    def _event(self, kind: str) -> Event:
        p: dict[str, object] = {"round": self.round}
        if kind in ("job.homeowner_confirmed", "job.answered", "job.undone"):
            p["command_id"] = str(uuid.uuid4())
        if kind == "job.contractor_confirmed":
            p["approval_id"] = str(uuid.uuid4())
        if kind == "permit.matched":
            p["pk"] = "P1"
        return Event(event_id=uuid.uuid4(), stream="job-x", seq=len(self.events) + 1, kind=kind, payload=p)

    @rule(kind=st.sampled_from(KINDS))
    def apply(self, kind: str) -> None:
        got = next_state(self.state, kind)
        want = model_next(self.state, kind)
        if want is None:
            assert isinstance(got, Refused), (self.state, kind, got)
            return
        assert got == want, (self.state, kind, got, want)
        self.events.append(self._event(kind))
        if kind == "job.undone":
            self.round += 1  # a new round starts when the job re-enters ready
        self.state = want

    @invariant()
    def recorded_always_had_a_confirmation_in_its_round(self) -> None:
        assert recorded_with_confirmation(self.events)

    @invariant()
    def marks_only_when_confirmed_or_recorded(self) -> None:
        m = marks(self.events)
        if self.state not in ("confirmed", "recorded"):
            assert m == []
        else:
            assert any(x.kind == "homeowner_confirmed" for x in m)

    @invariant()
    def an_undone_rounds_confirmation_never_shows(self) -> None:
        undone = {e.payload["round"] for e in self.events if e.kind == "job.undone"}
        shown = {mk.proof_id for mk in marks(self.events) if mk.kind == "homeowner_confirmed"}
        for e in self.events:
            if e.kind == "job.homeowner_confirmed" and e.payload["round"] in undone:
                assert e.payload["command_id"] not in shown


TestJobMachine = JobMachine.TestCase
TestJobMachine.settings = settings(max_examples=N, stateful_step_count=40, deadline=None)
