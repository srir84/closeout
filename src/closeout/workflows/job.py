"""The Job workflow: one long-lived workflow per job (design 3.4, 3.5, 3.8). Decisions only; I/O is in activities.

S4 builds: read the invoice twice -> find the parcel -> check facts and contractor -> ready or needs_a_look,
and the fix_fact / approve_facts / drop commands. Later slices add permit match, approval, outreach and undo.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from closeout.activities import common, reading
    from closeout.activities import jobs as acts
    from closeout.core.jobs import Refused, homeowner_started_next, next_state
    from closeout.workflows.commands import CommandQueue
    from closeout.workflows.payloads import ClockIn, CommandRef, CommandResult
    from closeout.workflows.util import DB_RETRY, DB_TIMEOUT

MODEL_RETRY = RetryPolicy(maximum_attempts=2, non_retryable_error_types=["BadInput", "ReplayMiss"])


class JobIn(BaseModel):
    job_id: UUID
    clock: ClockIn
    light: bool = False


class JobOut(BaseModel):
    job_id: UUID
    status: str
    round: int


def command_kinds() -> dict[str, tuple[str, ...]]:
    """Which commands are allowed in which states (design 3.5)."""
    return {
        "fix_fact": ("needs_a_look",),
        "approve_facts": ("needs_a_look",),
        "drop": ("needs_a_look",),
    }


@workflow.defn(name="Job")
class Job:
    def __init__(self) -> None:
        self.q = CommandQueue()
        self.status: str | None = None
        self.round = 1
        self.origin = "contractor"
        self.inp: JobIn | None = None
        self.document_id: UUID | None = None

    # --- commands --------------------------------------------------------------------------------
    def _allowed(self, ref: CommandRef) -> str | None:
        states = command_kinds().get(ref.kind)
        if states is None:
            return "unknown_command"
        if self.status not in states:
            return f"not_allowed_in_{self.status}"
        return None

    @workflow.update
    async def command(self, ref: CommandRef) -> CommandResult:
        return await self.q.submit(ref)

    @command.validator
    def _validate(self, ref: CommandRef) -> None:
        self.q.validate(ref, self._allowed)

    @workflow.query
    def state(self) -> str:
        return self.status or "starting"

    # --- helpers ---------------------------------------------------------------------------------
    async def _record(self, kind: str, key: str, **payload: object) -> None:
        assert self.inp is not None
        step = homeowner_started_next if self.origin == "homeowner" else next_state
        to = step(self.status, kind)
        if isinstance(to, Refused):
            raise ApplicationError(f"{kind} in {self.status}: {to.reason}", non_retryable=True)
        payload.setdefault("round", self.round)
        await workflow.execute_activity(
            acts.record,
            acts.RecordIn(job_id=self.inp.job_id, kind=kind, payload=dict(payload), event_key=key),
            start_to_close_timeout=DB_TIMEOUT,
            retry_policy=DB_RETRY,
        )
        self.status = to

    async def _db(self, fn, arg):  # type: ignore[no-untyped-def]
        return await workflow.execute_activity(
            fn, arg, start_to_close_timeout=DB_TIMEOUT, retry_policy=DB_RETRY
        )

    def _key(self, what: str) -> str:
        assert self.inp is not None
        return f"wf:job-{self.inp.job_id}:{self.round}:{what}"

    async def _read(self) -> None:
        """Reader A then reader B, each on its own queue, one read at a time per reader."""
        assert self.inp is not None
        if self.document_id is None:
            return
        readers = ["A"] if self.inp.light else ["A", "B"]
        for r in readers:
            try:
                await workflow.execute_activity(
                    reading.read_document,
                    reading.ReadIn(document_id=self.document_id, reader=r),
                    task_queue=f"model-reads-{r}",
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=MODEL_RETRY,
                )
            except ActivityError:
                pass  # a failed read counts as invalid: the person is asked
        await self._db(
            reading.decide_facts, reading.FactsIn(job_id=self.inp.job_id, document_id=self.document_id)
        )

    async def _settle(self, key: str) -> None:
        """From imported: ready if nothing needs a person, else needs_a_look with the first reason."""
        assert self.inp is not None
        await self._db(acts.match_job_address, acts.JobRef(job_id=self.inp.job_id))
        check: acts.CheckOut = await self._db(acts.check_job, acts.JobRef(job_id=self.inp.job_id))
        if check.reasons:
            await self._record(
                "job.needs_a_look",
                f"{key}:needs_a_look",
                reason=check.reasons[0],
                review_item_id=str(check.review_item_ids[0]) if check.review_item_ids else None,
            )
        else:
            await self._record("job.ready", f"{key}:ready")
        await self.on_settled()

    async def on_settled(self) -> None:
        """Hook for later slices (permit match)."""
        return None

    async def _finish(self, ref: CommandRef, ok: bool, code: str) -> None:
        result = CommandResult(command_id=ref.command_id, ok=ok, code=code, state=self.status)
        await workflow.execute_activity(
            common.finish_command, result, start_to_close_timeout=DB_TIMEOUT, retry_policy=DB_RETRY
        )
        self.q.finish(result)

    async def handle(self, ref: CommandRef) -> None:
        assert self.inp is not None
        ckey = f"cmd:{ref.command_id}"
        if ref.kind in ("fix_fact", "approve_facts"):
            out: acts.CommandOut = await self._db(
                acts.apply_fix, acts.CommandIn(job_id=self.inp.job_id, command_id=ref.command_id)
            )
            if not out.ok:
                await self._finish(ref, False, out.code)
                return
            await self._record(
                "job.fact_fixed",
                f"{ckey}:job.fact_fixed",
                fact_id=str(ref.ids.get("fact_id", ref.command_id)),
                field=ref.codes.get("field", "all"),
            )
            await self._settle(ckey)
            await self._finish(ref, True, out.code)
        elif ref.kind == "drop":
            await self._record("job.dropped", f"{ckey}:job.dropped", command_id=str(ref.command_id))
            await self._finish(ref, True, "dropped")
        else:
            await self._finish(ref, False, "unknown_command")

    def is_open(self) -> bool:
        return self.status not in ("dropped", "stopped", "retracted")

    # --- main ------------------------------------------------------------------------------------
    @workflow.run
    async def run(self, inp: JobIn) -> JobOut:
        self.inp = inp
        prep: acts.PrepOut = await self._db(acts.prepare_job, acts.JobRef(job_id=inp.job_id))
        self.origin = prep.origin
        self.document_id = prep.document_id
        await self._record("job.imported", f"wf:job-{inp.job_id}:imported", origin=self.origin)
        await self._read()
        await self._settle(f"wf:job-{inp.job_id}:1:first")
        while self.is_open():
            await workflow.wait_condition(lambda: self.q.has_work() or not self.is_open())
            if self.q.has_work():
                await self.handle(self.q.pop())
        await workflow.wait_condition(workflow.all_handlers_finished)
        return JobOut(job_id=inp.job_id, status=self.status or "imported", round=self.round)
