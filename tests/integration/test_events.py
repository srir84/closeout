"""S2: the add-only event record (design 3.9, R-REC-1, R-DATA-1)."""

import copy
import json
import uuid
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.exc import DBAPIError

from closeout import db
from closeout.activities.permits import apply_sync
from closeout.events import SeqClash, append_event, append_next, read_stream

pytestmark = [pytest.mark.slice("S2")]
ROWS = json.loads((Path(__file__).parents[2] / "data/seed/permits_1329.json").read_text())


@pytest.mark.req("R-REC-1")
def test_trigger_blocks_update_and_delete(database: str) -> None:
    with db.tx() as c:
        append_next(c, "job-a", "job.imported", {}, "cmd:a:job.imported")
    for sql in ("UPDATE events SET kind='job.ready'", "DELETE FROM events"):
        with pytest.raises(DBAPIError, match="add-only"), db.tx() as c:
            db.run(c, sql)
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM events") == 1


@pytest.mark.req("R-REC-1")
def test_a_retried_append_adds_nothing(database: str) -> None:
    with db.tx() as c:
        e1 = append_event(c, "job-b", 0, "job.imported", {}, "cmd:b1:job.imported")
        e2 = append_event(c, "job-b", 0, "job.imported", {}, "cmd:b1:job.imported")
        e3 = append_next(c, "job-b", "job.imported", {}, "cmd:b1:job.imported")
        assert e1 == e2 == e3
        assert db.scalar(c, "SELECT count(*) FROM events WHERE stream='job-b'") == 1


@pytest.mark.req("R-REC-1")
def test_a_seq_clash_raises_and_the_transaction_goes_on(database: str) -> None:
    with db.tx() as c:
        append_event(c, "job-c", 0, "job.imported", {}, "k1")
        with pytest.raises(SeqClash):
            append_event(c, "job-c", 0, "job.ready", {}, "k2")
        e = append_next(c, "job-c", "job.ready", {}, "k2")
        assert e.seq == 2
        assert [x.kind for x in read_stream(c, "job-c")] == ["job.imported", "job.ready"]


def test_payloads_are_checked(database: str) -> None:
    with db.tx() as c, pytest.raises(ValueError):
        append_next(c, "job-d", "job.answered", {"answer": "confirm", "name": "Dana"}, "k")
    with db.tx() as c, pytest.raises(ValueError, match="unknown event kind"):
        append_next(c, "job-d", "job.teleported", {}, "k")


@pytest.mark.req("R-REC-1")
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(keys=st.lists(st.sampled_from(["k1", "k2", "k3", "k4"]), min_size=1, max_size=12))
def test_adding_duplicates_changes_nothing(database: str, keys: list[str]) -> None:
    stream = f"job-{uuid.uuid4()}"
    with db.tx() as c:
        for k in keys:
            append_next(
                c,
                stream,
                "job.message_delivered",
                {"outbox_id": str(uuid.uuid5(uuid.NAMESPACE_URL, k)), "step": "first", "state": "sent"},
                f"{stream}:{k}",
            )
        got = read_stream(c, stream)
    assert len(got) == len(set(keys))
    assert [e.seq for e in got] == list(range(1, len(got) + 1))


@pytest.mark.req("R-DATA-1")
def test_permit_events_backfilled_from_the_seed(seeded: str) -> None:
    with db.tx() as c:
        n_permits = db.scalar(c, "SELECT count(*) FROM permits")
        assert db.scalar(c, "SELECT count(*) FROM events WHERE kind='permit.issued'") == n_permits
        assert db.scalar(c, "SELECT count(*) FROM events WHERE kind='permit.final_recorded'") == db.scalar(
            c, "SELECT count(*) FROM permits WHERE final_on IS NOT NULL"
        )


@pytest.mark.req("R-DATA-1")
def test_final_approval_in_a_sync_adds_one_final_recorded_event(seeded: str) -> None:
    first = copy.deepcopy(ROWS[:100])
    with db.tx() as c:
        db.run(c, "DELETE FROM permit_parcel; DELETE FROM permits WHERE town='1329'")
        db.run(c, "ALTER TABLE events DISABLE TRIGGER events_add_only")
        db.run(c, "DELETE FROM events WHERE stream LIKE 'permit-1327%'")
        db.run(c, "ALTER TABLE events ENABLE TRIGGER events_add_only")
        apply_sync(c, "1329", first, date(2026, 11, 27))
    second = copy.deepcopy(first)
    second.pop(10)
    target = next(r for r in second if not r.get("certdate"))
    target["certdate"] = "2026-11-20T00:00:00.000"
    second += copy.deepcopy(ROWS[100:105])
    with db.tx() as c:
        before = db.scalar(c, "SELECT count(*) FROM events WHERE kind='permit.final_recorded'")
        out = apply_sync(c, "1329", second, date(2026, 11, 27))
        assert (out.rows_new, out.rows_changed, out.rows_missing) == (5, 1, 1)
        finals = db.all_rows(
            c,
            "SELECT stream FROM events WHERE kind='permit.final_recorded' AND stream=:s",
            s=f"permit-{target['pk']}",
        )
        assert len(finals) == 1
        new_finals = db.scalar(c, "SELECT count(*) FROM events WHERE kind='permit.final_recorded'") - before
        assert new_finals == 1 + sum(1 for r in ROWS[100:105] if r.get("certdate"))
        assert (
            db.scalar(
                c,
                "SELECT count(*) FROM events WHERE kind='permit.changed' AND stream=:s",
                s=f"permit-{target['pk']}",
            )
            == 1
        )
        out2 = apply_sync(c, "1329", second, date(2026, 11, 27))
        assert out2.rows_changed == 0
        assert (
            db.scalar(
                c,
                "SELECT count(*) FROM events WHERE kind='permit.final_recorded' AND stream=:s",
                s=f"permit-{target['pk']}",
            )
            == 1
        )
