"""S2: mark rules (design 3.9, R-REC-2)."""

import uuid

import pytest

from closeout.core.record import job_view, marks, recorded_with_confirmation
from tests.helpers.events import Stream, happy

pytestmark = [pytest.mark.slice("S2"), pytest.mark.req("R-REC-2")]


def kinds(s: Stream) -> list[str]:
    return [m.kind for m in marks(s.events)]


def test_three_marks_once_confirmed_and_recorded() -> None:
    s = happy()
    assert kinds(s) == ["homeowner_confirmed", "contractor_confirmed", "permit_on_file"]
    s.add("job.recorded")
    assert kinds(s) == ["homeowner_confirmed", "contractor_confirmed", "permit_on_file"]


def test_no_marks_before_confirmation() -> None:
    s = Stream().add("job.imported").add("job.ready").add("permit.matched", pk="P1")
    s.add("job.approved", approval_ids=[]).add("job.contractor_confirmed", approval_id=str(uuid.uuid4()))
    assert kinds(s) == []
    s.add("job.answered", answer="decline")
    assert kinds(s) == []


def test_two_marks_without_a_permit() -> None:
    assert kinds(happy(permit=None)) == ["homeowner_confirmed", "contractor_confirmed"]


def test_undo_hides_the_rounds_marks_then_round_two_shows_its_own() -> None:
    s = happy()
    s.add("job.undone", 1)
    assert kinds(s) == []
    assert job_view(s.events).status == "ready"
    s.add("job.approved", 2, approval_ids=[])
    assert kinds(s) == []
    s.add("job.answered", 2, answer="confirm").add("job.homeowner_confirmed", 2)
    assert kinds(s) == [
        "homeowner_confirmed",
        "permit_on_file",
    ]  # round 2's contractor approval not yet confirmed
    s.add("job.contractor_confirmed", 2, approval_id=str(uuid.uuid4()))
    assert kinds(s) == ["homeowner_confirmed", "contractor_confirmed", "permit_on_file"]
    s.add("job.recorded", 2)
    assert job_view(s.events).round == 2


def test_an_old_round_confirmation_never_counts() -> None:
    s = happy()
    s.add("job.undone", 1).add("job.approved", 2, approval_ids=[]).add("job.answered", 2, answer="confirm")
    old = [e for e in s.events if e.kind == "job.homeowner_confirmed"][0]
    assert old.round == 1
    assert kinds(s) == []


def test_retracted_shows_nothing_and_is_hidden() -> None:
    s = happy().add("job.recorded").add("job.retracted")
    assert kinds(s) == []
    v = job_view(s.events)
    assert v.visible is False and v.status == "retracted"


def test_mark_proofs_point_at_their_records() -> None:
    s = happy()
    by = {m.kind: m for m in marks(s.events)}
    hc = next(e for e in s.events if e.kind == "job.homeowner_confirmed")
    cc = next(e for e in s.events if e.kind == "job.contractor_confirmed")
    assert (by["homeowner_confirmed"].proof_kind, by["homeowner_confirmed"].proof_id) == (
        "answer",
        hc.payload["command_id"],
    )
    assert (by["contractor_confirmed"].proof_kind, by["contractor_confirmed"].proof_id) == (
        "approval",
        cc.payload["approval_id"],
    )
    assert (by["permit_on_file"].proof_kind, by["permit_on_file"].proof_id) == ("permit", "P1")
    assert by["permit_on_file"].picked_by_contractor is False


def test_contractor_link_and_homeowner_upload_proofs() -> None:
    cmd = str(uuid.uuid4())
    s = Stream().add("job.imported", origin="homeowner").add("job.ready")
    s.add("job.approved", approval_ids=[]).add("job.answered", answer="confirm")
    s.add("job.homeowner_confirmed", via="upload").add("job.contractor_confirmed", via="link", command_id=cmd)
    by = {m.kind: m for m in marks(s.events)}
    assert by["homeowner_confirmed"].proof_kind == "upload"
    assert (by["contractor_confirmed"].proof_kind, by["contractor_confirmed"].proof_id) == ("link", cmd)


def test_picked_permit_says_so() -> None:
    s = (
        Stream()
        .add("job.imported")
        .add("job.ready")
        .add("permit.matched", pk="P9", picked_by_contractor=True)
    )
    happy(s=s)
    assert next(m for m in marks(s.events) if m.kind == "permit_on_file").picked_by_contractor is True


def test_recorded_needs_a_confirmation_in_the_same_round() -> None:
    assert recorded_with_confirmation(happy().add("job.recorded").events)
    s = happy()
    s.add("job.undone", 1).add("job.approved", 2, approval_ids=[]).add("job.answered", 2, answer="confirm")
    s.add("job.homeowner_confirmed", 2).add("job.recorded", 2)
    assert recorded_with_confirmation(s.events)
    fake = Stream()
    fake.add("job.homeowner_confirmed", 1)
    fake.add("job.recorded", 2)
    assert not recorded_with_confirmation(fake.events)


def test_price_visibility_follows_the_latest_choice() -> None:
    s = happy()
    assert job_view(s.events).price_visible is False
    s.add("job.price_visibility_set", price_visible=True)
    assert job_view(s.events).price_visible is True
    s.add("job.price_visibility_set", price_visible=False)
    assert job_view(s.events).price_visible is False
