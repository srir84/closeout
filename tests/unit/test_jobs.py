"""S2: every job state move (design 3.4), written out independently of the code's table."""

import pytest

from closeout.core.events import Event
from closeout.core.jobs import FINAL, STATES, Refused, homeowner_started_next, next_state, status_from_events
from tests.helpers.events import Stream

pytestmark = [pytest.mark.slice("S2"), pytest.mark.req("R-REC-1")]

# (from, event, to) — the arrows of the state diagram in design 3.4.
ALLOWED = [
    (None, "job.imported", "imported"),
    ("imported", "job.needs_a_look", "needs_a_look"),
    ("needs_a_look", "job.fact_fixed", "imported"),
    ("needs_a_look", "job.dropped", "dropped"),
    ("imported", "job.ready", "ready"),
    ("ready", "job.approved", "asking_homeowner"),
    ("asking_homeowner", "job.answered", "answered"),
    ("answered", "job.answered", "answered"),
    ("answered", "job.homeowner_confirmed", "confirmed"),
    ("answered", "job.correction_accepted", "answered"),
    ("answered", "job.correction_rejected", "needs_a_look"),
    ("answered", "job.needs_a_look", "needs_a_look"),
    ("answered", "job.stopped", "stopped"),
    ("asking_homeowner", "job.no_answer", "no_answer"),
    ("no_answer", "job.answered", "answered"),
    ("asking_homeowner", "job.on_hold", "on_hold"),
    ("on_hold", "job.resent", "asking_homeowner"),
    ("on_hold", "job.answered", "answered"),
    ("on_hold", "job.stopped", "stopped"),
    ("confirmed", "job.recorded", "recorded"),
    ("confirmed", "job.undone", "ready"),
    ("recorded", "job.retracted", "retracted"),
    ("asking_contractor", "job.answered", "answered"),
    ("asking_contractor", "job.no_answer", "no_answer"),
    ("asking_contractor", "job.on_hold", "on_hold"),
]
MOVING = sorted({k for _, k, _ in ALLOWED})


@pytest.mark.parametrize(("frm", "kind", "to"), ALLOWED)
def test_allowed_moves(frm, kind, to) -> None:
    assert next_state(frm, kind) == to


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("kind", MOVING)
def test_every_other_move_is_refused(state: str, kind: str) -> None:
    if (
        state,
        kind,
    ) in {(f, k) for f, k, _ in ALLOWED}:
        return
    got = next_state(state, kind)
    assert isinstance(got, Refused), (state, kind, got)


@pytest.mark.parametrize("state", FINAL)
@pytest.mark.parametrize("kind", MOVING + ["job.contractor_confirmed", "job.message_delivered"])
def test_final_states_never_move(state: str, kind: str) -> None:
    assert next_state(state, kind) == Refused("job_closed")


@pytest.mark.parametrize(
    "kind",
    [
        "job.contractor_confirmed",
        "job.message_delivered",
        "job.corrected",
        "job.price_visibility_set",
        "permit.matched",
    ],
)
@pytest.mark.parametrize("state", ["ready", "asking_homeowner", "answered", "recorded"])
def test_information_events_keep_the_state(kind: str, state: str) -> None:
    assert next_state(state, kind) == state


def test_refusal_reasons() -> None:
    assert next_state(None, "job.ready") == Refused("not_imported")
    assert next_state("ready", "job.imported") == Refused("already_imported")
    assert next_state("ready", "job.launch") == Refused("unknown_event")
    assert next_state("ready", "job.recorded") == Refused("not_allowed_in_ready")


def test_homeowner_started_jobs_ask_the_contractor() -> None:
    assert homeowner_started_next("ready", "job.approved") == "asking_contractor"
    assert homeowner_started_next("on_hold", "job.resent") == "asking_contractor"
    assert homeowner_started_next("answered", "job.homeowner_confirmed") == "confirmed"
    assert next_state("on_hold", "job.resent") == "asking_homeowner"


def test_status_from_events_replays_and_rejects_impossible_records() -> None:
    s = Stream().add("job.imported").add("job.ready").add("job.approved", approval_ids=[])
    assert status_from_events(s.events) == "asking_homeowner"
    assert status_from_events([]) is None
    bad = Stream().add("job.imported").add("job.recorded")
    with pytest.raises(ValueError, match="refused in state imported"):
        status_from_events(bad.events)
    h = Stream().add("job.imported", origin="homeowner").add("job.ready").add("job.approved", approval_ids=[])
    assert status_from_events(h.events, origin="homeowner") == "asking_contractor"
    assert isinstance(h.events[0], Event)


def test_homeowner_rules_only_change_their_two_moves() -> None:
    assert isinstance(homeowner_started_next("on_hold", "job.approved"), Refused)
    assert isinstance(homeowner_started_next("ready", "job.resent"), Refused)
    assert homeowner_started_next(None, "job.imported") == "imported"


def test_current_round_of_an_empty_record_is_one() -> None:
    from closeout.core.jobs import current_round, is_final

    assert current_round([]) == 1
    assert current_round(Stream().add("job.imported").add("job.ready", 3).events) == 3
    assert is_final("stopped") and not is_final("recorded") and not is_final(None)
