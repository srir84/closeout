"""TownRequest: ask the town clerk about one permit's final approval (design 3.8, R-TOWN-2).

drafted -> (owner approves both messages) -> request sent -> wait 7 business days -> one reminder ->
wait 5 more business days -> no_reply. A late reply is still accepted for 60 days after the first delivery.
The clerk replies by uploading a document; it is read twice and shown only if both reads agree.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from closeout.act import PerformIn, PerformOut
    from closeout.activities import common, outbox, reading, town
    from closeout.core.clock import WAITS, Clock
    from closeout.core.dates import add_business_days
    from closeout.workflows.commands import CommandQueue
    from closeout.workflows.payloads import ClockIn, CommandRef, CommandResult, Delivered
    from closeout.workflows.util import DB_RETRY, DB_TIMEOUT, to_clock, wait_until

MODEL_RETRY = RetryPolicy(maximum_attempts=2, non_retryable_error_types=["BadInput", "ReplayMiss"])
STEPS = ("request", "reminder")


class TownIn(BaseModel):
    request_id: UUID
    clock: ClockIn


class TownOut(BaseModel):
    request_id: UUID
    status: str
    result: str | None


@workflow.defn(name="TownRequest")
class TownRequest:
    def __init__(self) -> None:
        self.q = CommandQueue()
        self.status = "drafted"
        self.inp: TownIn | None = None
        self.clock: Clock | None = None
        self.approvals: dict[str, UUID] = {}
        self.outbox: dict[str, UUID] = {}
        self.delivered: dict[UUID, Delivered] = {}
        self.first_sent_at: datetime | None = None
        self.result: str | None = None

    def _allowed(self, ref: CommandRef) -> str | None:
        if ref.kind == "approve":
            if self.status != "drafted":
                return f"not_allowed_in_{self.status}"
            return None if set(STEPS) <= set(ref.ids) else "missing_approvals"
        if ref.kind == "reply":
            if self.status not in ("approved", "sent", "reminded", "no_reply"):
                return f"not_allowed_in_{self.status}"
            if "document_id" not in ref.ids:
                return "missing_document"
            if self.first_sent_at is not None and ref.saved_at > self._real_at(
                WAITS.town_late_window, self.first_sent_at
            ):
                return "too_late"
            return None
        return "unknown_command"

    @workflow.update
    async def command(self, ref: CommandRef) -> CommandResult:
        return await self.q.submit(ref)

    @command.validator
    def _validate(self, ref: CommandRef) -> None:
        self.q.validate(ref, self._allowed)

    @workflow.signal(name="delivered")
    def on_delivered(self, d: Delivered) -> None:
        if d.outbox_id in self.outbox.values():
            self.delivered[d.outbox_id] = d

    @workflow.query
    def state(self) -> str:
        return self.status

    # --- helpers ---------------------------------------------------------------------------------
    def _real_at(self, virtual_wait: timedelta, since_real: datetime) -> datetime:
        assert self.clock is not None
        return self.clock.real(self.clock.virtual(since_real) + virtual_wait)

    def _business_deadline(self, since_real: datetime, days: int) -> datetime:
        """`days` business days after a real instant, counted on virtual dates (design 3.2, 3.3)."""
        assert self.clock is not None
        v = self.clock.virtual(since_real)
        target = add_business_days(v.date(), days)
        return self.clock.real(v + timedelta(days=(target - v.date()).days))

    async def _db(self, fn, arg):  # type: ignore[no-untyped-def]
        return await workflow.execute_activity(
            fn, arg, start_to_close_timeout=DB_TIMEOUT, retry_policy=DB_RETRY
        )

    async def _set(self, status: str) -> None:
        assert self.inp is not None
        await self._db(town.town_status, town.TownStatusIn(request_id=self.inp.request_id, status=status))
        self.status = status

    async def _finish(self, ref: CommandRef, ok: bool, code: str) -> None:
        r = CommandResult(command_id=ref.command_id, ok=ok, code=code, state=self.status)
        await workflow.execute_activity(
            common.finish_command, r, start_to_close_timeout=DB_TIMEOUT, retry_policy=DB_RETRY
        )
        self.q.finish(r)

    async def _send(self, step: str) -> str:
        assert self.inp is not None
        out: PerformOut = await self._db(
            outbox.perform_send,
            PerformIn(
                approval_id=self.approvals[step],
                action="send_email",
                target=f"town-{self.inp.request_id}",
                round=1,
                step=step,
            ),
        )
        if out.ok and out.outbox_id is not None:
            self.outbox[step] = out.outbox_id
            return "queued"
        return out.code

    async def _delivery(self, step: str) -> Delivered | None:
        """Wait for the result of one message, handling clerk replies that arrive meanwhile."""
        assert self.clock is not None
        oid = self.outbox[step]
        poll = self.clock.real_wait(WAITS.backup_poll, WAITS.backup_poll_floor)
        while self.result is None:
            await wait_until(lambda: oid in self.delivered or self.q.has_work(), workflow.now() + poll)
            # A reply that is already accepted goes first: it can't be dropped by a delivery result (review 9).
            if self.q.has_work():
                await self.handle(self.q.pop())
                continue
            if oid in self.delivered:
                return self.delivered[oid]
            d: Delivered = await self._db(outbox.outbox_state, outbox.OutboxRef(outbox_id=oid))
            if self.q.has_work():
                continue
            if d.state in ("sent", "failed", "unknown", "cancelled"):
                return d
        return None

    async def _idle_until(self, deadline: datetime) -> None:
        while self.result is None:
            await wait_until(lambda: self.q.has_work(), deadline)
            if self.q.has_work():
                await self.handle(self.q.pop())
                continue
            return

    async def handle(self, ref: CommandRef) -> None:
        assert self.inp is not None
        stale = self._allowed(ref)
        if stale is not None:
            await self._finish(ref, False, stale)
            return
        if ref.kind == "approve":
            self.approvals = {s: ref.ids[s] for s in STEPS}
            await self._set("approved")
            await self._finish(ref, True, "approved")
            return
        # A reply: read it twice, then the both-reads-agree rule decides what is shown.
        doc = ref.ids["document_id"]
        for r in ("A", "B"):
            try:
                await workflow.execute_activity(
                    reading.read_document,
                    reading.ReadIn(document_id=doc, reader=r, purpose="town_reply"),
                    task_queue=f"model-reads-{r}",
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=MODEL_RETRY,
                )
            except ActivityError:
                pass  # a failed read: the result will be unclear, for ops
        out: town.ReplyOut = await self._db(
            town.decide_reply,
            town.ReplyIn(request_id=self.inp.request_id, document_id=doc, command_id=ref.command_id),
        )
        await self._db(town.stop_town_sending, town.TownRef(request_id=self.inp.request_id))
        self.result = out.result
        self.status = "replied"
        await self._finish(ref, True, out.result)

    async def _hold(self, code: str) -> None:
        assert self.inp is not None
        await self._db(town.town_hold, town.TownStatusIn(request_id=self.inp.request_id, status=code))
        await self._set("closed")

    # --- main ------------------------------------------------------------------------------------
    @workflow.run
    async def run(self, inp: TownIn) -> TownOut:
        self.inp = inp
        self.clock = to_clock(inp.clock)
        out = await self._story(inp)
        # Every way out settles what is still queued and frees the permit for a new request (review 9, 10).
        while self.q.has_work():
            await self._finish(self.q.pop(), False, "request_closed")
        await self._db(town.town_ended, town.TownRef(request_id=inp.request_id))
        await workflow.wait_condition(workflow.all_handlers_finished)
        return out

    async def _story(self, inp: TownIn) -> TownOut:
        await workflow.wait_condition(lambda: self.q.has_work())
        while self.status == "drafted":
            await self.handle(self.q.pop())
            if self.status == "drafted":
                await workflow.wait_condition(lambda: self.q.has_work())
        code = await self._send("request")
        if code != "queued":
            await self._hold(code)
            return TownOut(request_id=inp.request_id, status=self.status, result=None)
        d = await self._delivery("request")
        if self.result is None:
            if d is None or d.state != "sent" or d.sent_at is None:
                await self._hold(d.state if d else "no_delivery")
                return TownOut(request_id=inp.request_id, status=self.status, result=None)
            self.first_sent_at = d.sent_at
            await self._set("sent")
            await self._idle_until(self._business_deadline(d.sent_at, WAITS.town_first_wait_bdays))
        if self.result is None:
            code = await self._send("reminder")
            if code == "queued":
                rd = await self._delivery("reminder")
                if self.result is None and rd is not None and rd.state == "sent" and rd.sent_at is not None:
                    await self._set("reminded")
                    await self._idle_until(self._business_deadline(rd.sent_at, WAITS.town_second_wait_bdays))
        if self.result is None:
            await self._set("no_reply")
            assert self.first_sent_at is not None
            await self._idle_until(self._real_at(WAITS.town_late_window, self.first_sent_at))
        return TownOut(request_id=inp.request_id, status=self.status, result=self.result)
