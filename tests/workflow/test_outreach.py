"""S7: outreach inside the Job workflow, with time skipped (R-OUT-3, R-OUT-4, R-OUT-5, R-REC-3).

Fake activities stand in for the database and the sender. Every message the workflow asks perform to queue is
"delivered" at the workflow time the fake sees it, so reminder timing can be checked exactly.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowUpdateFailedError
from temporalio.service import RPCError
from temporalio.worker import Worker

from closeout.act import PerformIn, PerformOut
from closeout.activities import answers, outbox, permit_match, reading
from closeout.activities import jobs as acts
from closeout.core.jobs import Refused, next_state
from closeout.workflows.job import Job, JobIn
from closeout.workflows.payloads import ClockIn, CommandRef, CommandResult, Delivered

pytestmark = [pytest.mark.slice("S7")]
DAY = timedelta(days=1)


class World:
    def __init__(self, refuse: dict[str, str] | None = None, late_undo: bool = False) -> None:
        self.status: str | None = None
        self.events: list[str] = []
        self.sends: list[tuple[str, datetime]] = []
        self.outbox: dict[uuid.UUID, tuple[str, datetime]] = {}
        self.stops: list[str] = []
        self.reviews: list[str] = []
        self.refuse = refuse or {}
        self.late_undo = late_undo
        self.answer_payload: dict[str, object] = {}

    def activities(self) -> list:
        w = self

        @activity.defn(name="record")
        async def record(inp: acts.RecordIn) -> acts.RecordOut:
            to = next_state(w.status, inp.kind)
            assert not isinstance(to, Refused), (w.status, inp.kind)
            w.status = to
            w.events.append(inp.kind)
            return acts.RecordOut(status=to, seq=len(w.events))

        @activity.defn(name="prepare_job")
        async def prepare_job(inp: acts.JobRef) -> acts.PrepOut:
            return acts.PrepOut(document_id=uuid.uuid4(), origin="contractor")

        @activity.defn(name="read_document")
        async def read_document(inp: reading.ReadIn) -> reading.ReadOut:
            return reading.ReadOut(read_id=uuid.uuid4(), valid=True)

        @activity.defn(name="decide_facts")
        async def decide_facts(inp: reading.FactsIn) -> reading.FactsOut:
            return reading.FactsOut(kept=9, asked=0, unconfirmed=0)

        @activity.defn(name="match_job_address")
        async def match_job_address(inp: acts.JobRef) -> acts.MatchOut:
            return acts.MatchOut(pin="9999_1_1", reason="matched_exact")

        @activity.defn(name="check_job")
        async def check_job(inp: acts.JobRef) -> acts.CheckOut:
            return acts.CheckOut(reasons=[], review_item_ids=[])

        @activity.defn(name="match_permit")
        async def match_permit(inp: permit_match.MatchIn) -> permit_match.MatchPermitOut:
            return permit_match.MatchPermitOut(result="on_file", pks=["p1"], already_marked=False)

        @activity.defn(name="finish_command")
        async def finish_command(r: CommandResult) -> None:
            return None

        @activity.defn(name="perform_send")
        async def perform_send(inp: PerformIn) -> PerformOut:
            now = activity.info().current_attempt_scheduled_time
            if inp.step in w.refuse:
                return PerformOut(ok=False, code=w.refuse[inp.step], command_id=uuid.uuid4())
            oid = uuid.uuid4()
            w.outbox[oid] = (inp.step, now)
            w.sends.append((inp.step, now))
            return PerformOut(ok=True, code="queued", outbox_id=oid, command_id=uuid.uuid4())

        @activity.defn(name="outbox_state")
        async def outbox_state(inp: outbox.OutboxRef) -> Delivered:
            step, at = w.outbox[inp.outbox_id]
            return Delivered(outbox_id=inp.outbox_id, state="sent", sent_at=at, round=1)

        @activity.defn(name="check_approvals")
        async def check_approvals(inp: outbox.ApprovalCheckIn) -> outbox.ApprovalCheckOut:
            return outbox.ApprovalCheckOut(ok=True, code="ok")

        @activity.defn(name="stop_sending")
        async def stop_sending(inp: outbox.StopIn) -> int:
            w.stops.append(inp.reason)
            if inp.review_kind:
                w.reviews.append(inp.review_kind)
            return 0

        @activity.defn(name="apply_answer")
        async def apply_answer(inp: acts.CommandIn) -> answers.AnswerOut:
            return answers.AnswerOut(price_visible=True)

        @activity.defn(name="correction_rejected")
        async def correction_rejected(inp: acts.CommandIn) -> None:
            w.reviews.append("correction_rejected")

        @activity.defn(name="saved_undo")
        async def saved_undo(inp: answers.SavedUndoIn) -> answers.LateUndo:
            return answers.LateUndo(command_id=uuid.uuid4() if w.late_undo else None)

        @activity.defn(name="start_round")
        async def start_round(inp: answers.RoundIn) -> int:
            return 0

        return [
            record,
            prepare_job,
            read_document,
            decide_facts,
            match_job_address,
            check_job,
            match_permit,
            finish_command,
            perform_send,
            outbox_state,
            check_approvals,
            stop_sending,
            apply_answer,
            correction_rejected,
            saved_undo,
            start_round,
        ]


@contextlib.asynccontextmanager
async def job(env, w: World):
    tq = f"tq-{uuid.uuid4()}"
    acts_list = w.activities()
    workers = [Worker(env.client, task_queue=tq, workflows=[Job], activities=acts_list)]
    for r in ("A", "B"):
        workers.append(Worker(env.client, task_queue=f"model-reads-{r}", activities=[acts_list[2]]))
    async with contextlib.AsyncExitStack() as st:
        for wk in workers:
            await st.enter_async_context(wk)
        now = await env.get_current_time()
        jid = uuid.uuid4()
        h = await env.client.start_workflow(
            Job.run,
            JobIn(job_id=jid, clock=ClockIn(epoch_real=now, epoch_virtual=now, scale=1)),
            id=f"job-{jid}",
            task_queue=tq,
        )
        yield h


def cmd(kind: str, saved_at: datetime | None = None, **codes: str) -> CommandRef:
    ids = {s: uuid.uuid4() for s in ("first", "reminder_1", "reminder_2")} if kind == "approve" else {}
    return CommandRef(
        command_id=uuid.uuid4(), kind=kind, saved_at=saved_at or datetime.now(UTC), ids=ids, codes=codes
    )


async def send(h, kind: str, **codes: str):
    c = cmd(kind, **codes)
    return await h.execute_update(Job.command, c, id=str(c.command_id))


async def until_state(
    h, w: World, state: str, env, step: timedelta = timedelta(seconds=10), limit: int = 400
):
    for _ in range(limit):
        if w.status == state:
            return
        await env.sleep(step)
    raise AssertionError(f"status {w.status}, wanted {state}; events {w.events}")


async def approved(h, w: World, env) -> None:
    await until_state(h, w, "ready", env)
    r = await send(h, "approve")
    assert r.ok
    await until_state(h, w, "asking_homeowner", env)
    for _ in range(50):
        if "job.message_delivered" in w.events:
            return
        await env.sleep(timedelta(seconds=10))


def steps(w: World) -> list[str]:
    return [s for s, _ in w.sends]


@pytest.mark.req("R-OUT-3")
async def test_confirm_at_once_then_recorded_after_the_undo_window(ts_env) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        r = await send(h, "answer", answer="confirm", round="1")
        assert r.ok and r.state == "confirmed"
        out = await h.result()
    assert out.status == "recorded" and steps(w) == ["first"]
    assert (
        w.events[-3:] == ["job.answered", "job.price_visibility_set", "job.homeowner_confirmed"][-3:]
        or w.events[-1] == "job.recorded"
    )
    assert "answered" in w.stops


@pytest.mark.req("R-OUT-4")
async def test_no_answer_two_reminders_timed_from_delivery_then_closed(ts_env) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        out = await h.result()
    assert steps(w) == ["first", "reminder_1", "reminder_2"]
    first = w.sends[0][1]
    assert w.sends[1][1] - first >= 3 * DAY and w.sends[1][1] - first < 3 * DAY + timedelta(minutes=30)
    assert w.sends[2][1] - first >= 7 * DAY and w.sends[2][1] - first < 7 * DAY + timedelta(minutes=30)
    assert out.status == "no_answer"
    assert "job.no_answer" in w.events


@pytest.mark.req("R-OUT-4")
@pytest.mark.parametrize(("days", "emails"), [(4, 2), (8, 3), (20, 3)])
async def test_answers_after_reminders_and_late(ts_env, days: int, emails: int) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        await ts_env.sleep(days * DAY)
        r = await send(h, "answer", answer="confirm", round="1")
        assert r.ok
        out = await h.result()
    assert out.status == "recorded" and len(w.sends) == emails


@pytest.mark.req("R-OUT-4")
async def test_an_answer_on_day_31_is_too_late(ts_env) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        await ts_env.sleep(31 * DAY)
        with pytest.raises((WorkflowUpdateFailedError, RPCError)):
            await send(h, "answer", answer="confirm", round="1")
        assert (await h.result()).status == "no_answer"


@pytest.mark.req("R-OUT-3")
async def test_correction_then_contractor_accepts(ts_env) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        r = await send(h, "answer", answer="correct", round="1")
        assert r.state == "answered"
        r2 = await send(h, "accept_correction")
        assert r2.ok and r2.state == "confirmed"
        assert (await h.result()).status == "recorded"
    assert "job.corrected" in w.events and "job.correction_accepted" in w.events


@pytest.mark.req("R-OUT-3")
async def test_correction_rejected_needs_a_look(ts_env) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        await send(h, "answer", answer="correct", round="1")
        r = await send(h, "reject_correction")
        assert r.state == "needs_a_look"
        await h.terminate("done")
    assert "correction_rejected" in w.reviews


@pytest.mark.req("R-OUT-3")
async def test_the_homeowner_changes_the_answer(ts_env) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        await send(h, "answer", answer="correct", round="1")
        r = await send(h, "answer", answer="confirm", round="1")
        assert r.state == "confirmed"
        assert (await h.result()).status == "recorded"


@pytest.mark.req("R-OUT-3")
@pytest.mark.parametrize(("answer", "review"), [("decline", None), ("not_mine", "not_my_house")])
async def test_decline_and_not_my_house_stop_everything(ts_env, answer: str, review: str | None) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        r = await send(h, "answer", answer=answer, round="1")
        assert r.state == "stopped"
        assert (await h.result()).status == "stopped"
    assert steps(w) == ["first"]
    assert (review in w.reviews) if review else w.reviews == []


@pytest.mark.req("R-OUT-5")
async def test_unsubscribe_midway_sends_no_more_reminders(ts_env) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        r = await send(h, "unsubscribed")
        assert r.ok and r.state == "asking_homeowner"  # unsubscribing doesn't change the job
        out = await h.result()
    assert steps(w) == ["first"] and out.status == "no_answer"


@pytest.mark.req("R-OUT-5")
async def test_a_suppressed_reminder_just_stops(ts_env) -> None:
    w = World(refuse={"reminder_1": "suppressed", "reminder_2": "suppressed"})
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        assert (await h.result()).status == "no_answer"
    assert steps(w) == ["first"] and "job.on_hold" not in w.events


async def test_a_refused_reminder_puts_the_job_on_hold(ts_env) -> None:
    w = World(refuse={"reminder_1": "over_limit"})
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        await until_state(h, w, "on_hold", ts_env, step=timedelta(hours=6))
        r = await send(h, "ops_stop")
        assert r.state == "stopped"
    assert "on_hold" in w.reviews


@pytest.mark.req("R-OUT-1")
async def test_an_answer_from_an_old_round_is_refused(ts_env) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        with pytest.raises(WorkflowUpdateFailedError):
            await send(h, "answer", answer="confirm", round="2")
        await h.terminate("done")


@pytest.mark.req("R-REC-3")
@pytest.mark.slice("S8")
async def test_undo_in_time_starts_a_new_round(ts_env) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        await send(h, "answer", answer="confirm", round="1")
        r = await send(h, "undo")
        assert r.ok and r.state == "ready"
        with pytest.raises(WorkflowUpdateFailedError):  # an old-round link after an undo
            await send(h, "answer", answer="confirm", round="1")
        r = await send(h, "approve")
        assert r.ok
        await until_state(h, w, "asking_homeowner", ts_env)
        await send(h, "answer", answer="confirm", round="2")
        out = await h.result()
    assert out.status == "recorded" and out.round == 2 and "job.undone" in w.events


@pytest.mark.req("R-REC-3")
@pytest.mark.slice("S8")
async def test_undo_too_late_is_refused(ts_env) -> None:
    w = World()
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        await send(h, "answer", answer="confirm", round="1")
        late = cmd("undo", saved_at=(await ts_env.get_current_time()) + timedelta(minutes=11))
        with pytest.raises(WorkflowUpdateFailedError):
            await h.execute_update(Job.command, late, id=str(late.command_id))
        assert (await h.result()).status == "recorded"


@pytest.mark.req("R-REC-3")
@pytest.mark.slice("S8")
async def test_undo_pressed_in_time_but_delivered_late_still_counts(ts_env) -> None:
    w = World(late_undo=True)
    async with job(ts_env, w) as h:
        await approved(h, w, ts_env)
        await send(h, "answer", answer="confirm", round="1")
        await until_state(h, w, "ready", ts_env, step=timedelta(minutes=1))
        await h.terminate("done")
    assert "job.undone" in w.events and "job.recorded" not in w.events
