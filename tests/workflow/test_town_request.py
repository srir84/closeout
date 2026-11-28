"""S11: the TownRequest workflow with time skipped (R-TOWN-2): reply, reply after the reminder, late reply, none."""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowUpdateFailedError
from temporalio.worker import Worker

from closeout.act import PerformIn, PerformOut
from closeout.activities import common, outbox, reading, town
from closeout.workflows.payloads import ClockIn, CommandRef, CommandResult, Delivered
from closeout.workflows.town_request import TownIn, TownRequest

pytestmark = [pytest.mark.slice("S11"), pytest.mark.req("R-TOWN-2")]
MONDAY = datetime(2026, 3, 2, 9, tzinfo=UTC)


class Town:
    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.sends: list[tuple[str, datetime]] = []
        self.outbox: dict[uuid.UUID, tuple[str, datetime]] = {}
        self.reads = 0

    def activities(self) -> list:
        t = self

        @activity.defn(name="town_status")
        async def town_status(inp: town.TownStatusIn) -> None:
            t.statuses.append(inp.status)

        @activity.defn(name="perform_send")
        async def perform_send(inp: PerformIn) -> PerformOut:
            now = activity.info().current_attempt_scheduled_time
            oid = uuid.uuid4()
            t.outbox[oid] = (inp.step, now)
            t.sends.append((inp.step, now))
            return PerformOut(ok=True, code="queued", outbox_id=oid, command_id=uuid.uuid4())

        @activity.defn(name="outbox_state")
        async def outbox_state(inp: outbox.OutboxRef) -> Delivered:
            _, at = t.outbox[inp.outbox_id]
            return Delivered(outbox_id=inp.outbox_id, state="sent", sent_at=at, round=1)

        @activity.defn(name="read_document")
        async def read_document(inp: reading.ReadIn) -> reading.ReadOut:
            t.reads += 1
            return reading.ReadOut(read_id=uuid.uuid4(), valid=True)

        @activity.defn(name="decide_reply")
        async def decide_reply(inp: town.ReplyIn) -> town.ReplyOut:
            return town.ReplyOut(result="final_on_file", reason="agree", result_date=None)

        @activity.defn(name="stop_town_sending")
        async def stop_town_sending(inp: town.TownRef) -> int:
            return 0

        @activity.defn(name="town_hold")
        async def town_hold(inp: town.TownStatusIn) -> None:
            t.statuses.append("hold:" + inp.status)

        @activity.defn(name="finish_command")
        async def finish_command(r: CommandResult) -> None:
            return None

        return [
            town_status,
            perform_send,
            outbox_state,
            read_document,
            decide_reply,
            stop_town_sending,
            town_hold,
            finish_command,
        ]


@contextlib.asynccontextmanager
async def request(env, t: Town):
    tq = f"tq-{uuid.uuid4()}"
    a = t.activities()
    workers = [Worker(env.client, task_queue=tq, workflows=[TownRequest], activities=a)]
    for r in ("A", "B"):
        workers.append(Worker(env.client, task_queue=f"model-reads-{r}", activities=[a[3]]))
    async with contextlib.AsyncExitStack() as st:
        for w in workers:
            await st.enter_async_context(w)
        now = await env.get_current_time()
        rid = uuid.uuid4()
        h = await env.client.start_workflow(
            TownRequest.run,
            TownIn(request_id=rid, clock=ClockIn(epoch_real=now, epoch_virtual=MONDAY, scale=1)),
            id=f"town-{rid}",
            task_queue=tq,
        )
        c = CommandRef(
            command_id=uuid.uuid4(),
            kind="approve",
            saved_at=now,
            ids={"request": uuid.uuid4(), "reminder": uuid.uuid4()},
        )
        assert (await h.execute_update(TownRequest.command, c, id=str(c.command_id))).ok
        yield h


async def reply(h, env):
    c = CommandRef(
        command_id=uuid.uuid4(),
        kind="reply",
        saved_at=await env.get_current_time(),
        ids={"document_id": uuid.uuid4()},
    )
    return await h.execute_update(TownRequest.command, c, id=str(c.command_id))


async def test_reply_on_business_day_4(ts_env) -> None:
    t = Town()
    async with request(ts_env, t) as h:
        await ts_env.sleep(timedelta(days=4))  # Mon -> Fri: business day 4
        r = await reply(h, ts_env)
        assert (r.ok, r.code) == (True, "final_on_file")
        out = await h.result()
    assert out.status == "replied" and [s for s, _ in t.sends] == ["request"] and t.reads == 2


async def test_reminder_after_7_business_days_then_a_reply(ts_env) -> None:
    t = Town()
    async with request(ts_env, t) as h:
        await ts_env.sleep(timedelta(days=12))
        assert [s for s, _ in t.sends] == ["request", "reminder"]
        gap = t.sends[1][1] - t.sends[0][1]
        assert (
            timedelta(days=9) <= gap < timedelta(days=9, hours=1)
        )  # Mon 2 Mar + 7 business days = Wed 11 Mar
        await reply(h, ts_env)
        assert (await h.result()).status == "replied"
    assert t.statuses[:3] == ["approved", "sent", "reminded"]


async def test_no_reply_then_a_late_reply_in_the_60_day_window(ts_env) -> None:
    t = Town()
    async with request(ts_env, t) as h:
        await ts_env.sleep(timedelta(days=20))
        assert "no_reply" in t.statuses
        await ts_env.sleep(timedelta(days=20))
        r = await reply(h, ts_env)
        assert r.ok
        assert (await h.result()).status == "replied"


async def test_no_reply_at_all(ts_env) -> None:
    t = Town()
    async with request(ts_env, t) as h:
        out = await h.result()
    assert out.status == "no_reply" and len(t.sends) == 2


async def test_a_reply_after_60_days_is_too_late(ts_env) -> None:
    t = Town()
    async with request(ts_env, t) as h:
        await ts_env.sleep(timedelta(days=59))
        late = CommandRef(
            command_id=uuid.uuid4(),
            kind="reply",
            saved_at=(await ts_env.get_current_time()) + timedelta(days=2),
            ids={"document_id": uuid.uuid4()},
        )
        with pytest.raises(WorkflowUpdateFailedError):
            await h.execute_update(TownRequest.command, late, id=str(late.command_id))
        assert (await h.result()).status == "no_reply"
    assert common
