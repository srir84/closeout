"""S4: the Job workflow's decisions, with fake activities (time-skipping kit)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from temporalio import activity
from temporalio.client import WorkflowUpdateFailedError

from closeout.activities import jobs as acts
from closeout.activities import permit_match, reading
from closeout.workflows.job import Job, JobIn
from closeout.workflows.payloads import ClockIn, CommandRef, CommandResult

pytestmark = [pytest.mark.slice("S4")]
NOW = datetime(2026, 3, 2, 9, tzinfo=UTC)


class Fakes:
    def __init__(
        self, reasons: list[list[str]], fix_ok: bool = True, permit: tuple[str, list[str]] = ("none", [])
    ) -> None:
        self.permit = permit
        self.reasons = list(reasons)
        self.fix_ok = fix_ok
        self.recorded: list[str] = []
        self.reads: list[str] = []
        self.finished: list[CommandResult] = []

    def all(self) -> list:
        f = self

        @activity.defn(name="record")
        async def record(inp: acts.RecordIn) -> acts.RecordOut:
            f.recorded.append(inp.kind)
            return acts.RecordOut(status="x", seq=len(f.recorded))

        @activity.defn(name="prepare_job")
        async def prepare_job(inp: acts.JobRef) -> acts.PrepOut:
            return acts.PrepOut(document_id=uuid.uuid4(), origin="contractor")

        @activity.defn(name="read_document")
        async def read_document(inp: reading.ReadIn) -> reading.ReadOut:
            f.reads.append(inp.reader)
            return reading.ReadOut(read_id=uuid.uuid4(), valid=True)

        @activity.defn(name="decide_facts")
        async def decide_facts(inp: reading.FactsIn) -> reading.FactsOut:
            return reading.FactsOut(kept=9, asked=0, unconfirmed=0)

        @activity.defn(name="match_job_address")
        async def match_job_address(inp: acts.JobRef) -> acts.MatchOut:
            return acts.MatchOut(pin="9999_1_1", reason="matched_exact")

        @activity.defn(name="check_job")
        async def check_job(inp: acts.JobRef) -> acts.CheckOut:
            return acts.CheckOut(reasons=f.reasons.pop(0) if f.reasons else [], review_item_ids=[])

        @activity.defn(name="apply_fix")
        async def apply_fix(inp: acts.CommandIn) -> acts.CommandOut:
            return acts.CommandOut(ok=f.fix_ok, code="fixed" if f.fix_ok else "bad_value")

        @activity.defn(name="match_permit")
        async def match_permit(inp: permit_match.MatchIn) -> permit_match.MatchPermitOut:
            return permit_match.MatchPermitOut(result=f.permit[0], pks=f.permit[1], already_marked=False)

        @activity.defn(name="mark_closed")
        async def mark_closed(inp: acts.JobRef) -> None:
            return None

        @activity.defn(name="finish_command")
        async def finish_command(r: CommandResult) -> None:
            f.finished.append(r)

        return [
            record,
            prepare_job,
            read_document,
            decide_facts,
            match_job_address,
            check_job,
            apply_fix,
            finish_command,
            match_permit,
            mark_closed,
        ]


async def start(env, fakes: Fakes, light: bool = False):
    from temporalio.worker import Worker

    tq = f"tq-{uuid.uuid4()}"
    ws = [Worker(env.client, task_queue=tq, workflows=[Job], activities=fakes.all())]
    for r in ("A", "B"):
        ws.append(Worker(env.client, task_queue=f"model-reads-{r}", activities=[fakes.all()[2]]))
    return tq, ws


async def run_job(env, fakes: Fakes, light: bool = False, script=None):
    import contextlib

    tq, ws = await start(env, fakes)
    async with contextlib.AsyncExitStack() as st:
        for w in ws:
            await st.enter_async_context(w)
        jid = uuid.uuid4()
        h = await env.client.start_workflow(
            Job.run,
            JobIn(job_id=jid, clock=ClockIn(epoch_real=NOW, epoch_virtual=NOW, scale=1), light=light),
            id=f"job-{jid}",
            task_queue=tq,
        )
        if script:
            await script(h)
        else:
            await env.sleep(1)
        return h


def ref(kind: str, **codes: str) -> CommandRef:
    return CommandRef(command_id=uuid.uuid4(), kind=kind, saved_at=NOW, codes=codes)


async def wait_state(h, state: str) -> None:
    import asyncio

    for _ in range(100):
        if await h.query(Job.state) == state:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(await h.query(Job.state))


@pytest.mark.req("R-IMP-3")
async def test_clean_job_reads_twice_and_becomes_ready(ts_env) -> None:
    f = Fakes([[]])

    async def script(h):
        await wait_state(h, "ready")
        await h.terminate("done")

    await run_job(ts_env, f, script=script)
    assert f.recorded == ["job.imported", "job.ready"]
    assert f.reads == ["A", "B", "B"]  # both invoice reads, then the amount-marks look


@pytest.mark.req("R-IMP-3")
async def test_light_mode_reads_once_and_needs_the_review_screen(ts_env) -> None:
    f = Fakes([["light_review"], []])

    async def script(h):
        await wait_state(h, "needs_a_look")
        r = await h.execute_update(Job.command, ref("approve_facts"), id="a1")
        assert r.ok and r.state == "ready"
        await h.terminate("done")

    await run_job(ts_env, f, light=True, script=script)
    assert f.reads == ["A"]
    assert f.recorded == ["job.imported", "job.needs_a_look", "job.fact_fixed", "job.ready"]


@pytest.mark.req("R-OPS-1")
async def test_needs_a_look_then_fixes_until_ready(ts_env) -> None:
    f = Fakes([["address_unmatched"], ["facts_asked"], []])

    async def script(h):
        await wait_state(h, "needs_a_look")
        r1 = await h.execute_update(Job.command, ref("fix_fact", field="job_address"), id="f1")
        assert (r1.ok, r1.state) == (True, "needs_a_look")
        r2 = await h.execute_update(Job.command, ref("fix_fact", field="total_amount"), id="f2")
        assert (r2.ok, r2.state) == (True, "ready")
        again = await h.execute_update(Job.command, ref("fix_fact", field="total_amount"), id="f2")
        assert again == r2  # a re-sent click returns the first result
        with pytest.raises(WorkflowUpdateFailedError):
            await h.execute_update(Job.command, ref("fix_fact", field="x"), id="f3")  # not allowed in ready
        await h.terminate("done")

    await run_job(ts_env, f, script=script)
    assert f.recorded.count("job.fact_fixed") == 2 and f.recorded[-1] == "job.ready"


async def test_a_bad_fix_is_refused_and_changes_nothing(ts_env) -> None:
    f = Fakes([["facts_asked"]], fix_ok=False)

    async def script(h):
        await wait_state(h, "needs_a_look")
        r = await h.execute_update(Job.command, ref("fix_fact", field="total_amount"), id="b1")
        assert (r.ok, r.code, r.state) == (False, "bad_value", "needs_a_look")
        await h.terminate("done")

    await run_job(ts_env, f, script=script)
    assert "job.fact_fixed" not in f.recorded


async def test_drop_ends_the_workflow(ts_env) -> None:
    f = Fakes([["read_failed"]])

    async def script(h):
        await wait_state(h, "needs_a_look")
        r = await h.execute_update(Job.command, ref("drop"), id="d1")
        assert r.ok and r.state == "dropped"
        out = await h.result()
        assert out.status == "dropped"

    await run_job(ts_env, f, script=script)
    assert f.recorded[-1] == "job.dropped"


@pytest.mark.req("R-COR-1")
@pytest.mark.slice("S5")
async def test_one_permit_gives_the_mark_event(ts_env) -> None:
    f = Fakes([[]], permit=("on_file", ["p1"]))

    async def script(h):
        await wait_state(h, "ready")
        with pytest.raises(WorkflowUpdateFailedError):
            await h.execute_update(Job.command, ref("pick_permit", pk="p1"), id="p0")  # already marked
        await h.terminate("done")

    await run_job(ts_env, f, script=script)
    assert f.recorded == ["job.imported", "job.ready", "permit.matched"]


@pytest.mark.req("R-COR-1")
@pytest.mark.slice("S5")
async def test_several_permits_the_contractor_picks_one(ts_env) -> None:
    f = Fakes([[]], permit=("possible", ["p1", "p2"]))

    async def script(h):
        await wait_state(h, "ready")
        with pytest.raises(WorkflowUpdateFailedError):
            await h.execute_update(Job.command, ref("pick_permit", pk="p9"), id="p1")  # not a candidate
        r = await h.execute_update(Job.command, ref("pick_permit", pk="p2"), id="p2")
        assert (r.ok, r.code) == (True, "picked")
        await h.terminate("done")

    await run_job(ts_env, f, script=script)
    assert f.recorded == ["job.imported", "job.ready", "permit.matched"]


async def test_two_queued_commands_never_both_apply(ts_env) -> None:
    """Whichever runs first wins; the other is re-checked when its turn comes and refused, never stuck."""
    import asyncio

    f = Fakes([["facts_asked"], []])

    async def script(h):
        await wait_state(h, "needs_a_look")
        drop = asyncio.create_task(h.execute_update(Job.command, ref("drop"), id="q1"))
        fix = asyncio.create_task(
            h.execute_update(Job.command, ref("fix_fact", field="total_amount"), id="q2")
        )
        d, x = await asyncio.wait_for(asyncio.gather(drop, fix, return_exceptions=True), timeout=10)
        oks = [r for r in (d, x) if not isinstance(r, BaseException) and r.ok]
        assert len(oks) == 1
        if oks[0] is d:
            assert (await h.result()).status == "dropped"
            assert isinstance(x, BaseException) or x.code in ("job_closed", "not_allowed_in_dropped")
        else:
            assert isinstance(d, BaseException) or d.code == "not_allowed_in_ready"
            await h.terminate("done")

    await run_job(ts_env, f, script=script)
    assert not ("job.dropped" in f.recorded and "job.fact_fixed" in f.recorded)


async def test_an_idle_job_does_not_spin(ts_env) -> None:
    """While waiting for a person, the workflow does nothing: its history stays small."""
    import asyncio

    f = Fakes([[]])

    async def script(h):
        await wait_state(h, "ready")
        before = len((await h.fetch_history()).events)
        await asyncio.sleep(1.0)
        after = len((await h.fetch_history()).events)
        assert after == before < 80
        await h.signal(Job.permits_changed)
        await asyncio.sleep(0.5)
        grown = len((await h.fetch_history()).events) - after
        assert 0 < grown < 20  # one re-check, then quiet again
        await h.terminate("done")

    await run_job(ts_env, f, script=script)
