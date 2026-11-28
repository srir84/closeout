"""The Job workflow: one long-lived workflow per job (design 3.4, 3.5, 3.8). Decisions only; I/O is in activities.

imported -> read twice -> parcel -> checks -> ready | needs_a_look -> (contractor approves) -> asking_homeowner
-> outreach: first email, reminders 3 and 7 virtual days after delivery, a last wait of 7 days, late answers until
day 30 -> answered -> confirmed -> undo window -> recorded. Every state is recorded as an event first.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from closeout.act import PerformIn, PerformOut
    from closeout.activities import answers, common, outbox, permit_match, reading
    from closeout.activities import jobs as acts
    from closeout.core.clock import WAITS, Clock
    from closeout.core.jobs import Refused, homeowner_started_next, next_state
    from closeout.workflows.commands import CommandQueue
    from closeout.workflows.payloads import ClockIn, CommandRef, CommandResult, Delivered
    from closeout.workflows.util import DB_RETRY, DB_TIMEOUT, to_clock, wait_until

MODEL_RETRY = RetryPolicy(maximum_attempts=2, non_retryable_error_types=["BadInput", "ReplayMiss"])
SEND_STEPS = ("first", "reminder_1", "reminder_2")
ASKING = ("asking_homeowner", "asking_contractor")
OPEN_ANSWER_STATES = ("asking_homeowner", "asking_contractor", "answered", "no_answer", "on_hold")


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
    before_recorded = (
        "imported",
        "needs_a_look",
        "ready",
        "asking_homeowner",
        "asking_contractor",
        "answered",
        "confirmed",
        "no_answer",
        "on_hold",
    )
    return {
        "fix_fact": ("needs_a_look",),
        "approve_facts": ("needs_a_look",),
        "drop": ("needs_a_look",),
        "pick_permit": before_recorded,
        "approve": ("ready",),
        "answer": OPEN_ANSWER_STATES,
        "accept_correction": ("answered",),
        "reject_correction": ("answered",),
        "undo": ("answered", "confirmed"),  # answered only in the moment a confirmation is being recorded
        "unsubscribed": (*before_recorded, "recorded"),
        "ops_stop": ("on_hold",),
    }


@workflow.defn(name="Job")
class Job:
    def __init__(self) -> None:
        self.q = CommandQueue()
        self.status: str | None = None
        self.round = 1
        self.origin = "contractor"
        self.inp: JobIn | None = None
        self.clock: Clock | None = None
        self.document_id: UUID | None = None
        self.permit_candidates: list[str] = []
        self.permit_marked = False
        self.permits_dirty = False
        # outreach, per round
        self.approvals: dict[str, UUID] = {}
        self.outbox: dict[str, UUID] = {}
        self.delivered: dict[UUID, Delivered] = {}
        self.first_sent_at: datetime | None = None
        self.unsubscribed = False
        self.correction_pending = False
        self.confirmed_at: datetime | None = None
        self.undo_deadline: datetime | None = None
        self.reminder2_at: datetime | None = None
        self.closed_reason: str | None = None

    # --- commands and signals --------------------------------------------------------------------
    def _allowed(self, ref: CommandRef) -> str | None:
        states = command_kinds().get(ref.kind)
        if states is None:
            return "unknown_command"
        if self.status not in states:
            return f"not_allowed_in_{self.status}"
        if ref.kind == "pick_permit" and (
            self.permit_marked or ref.codes.get("pk") not in self.permit_candidates
        ):
            return "not_a_candidate"
        if ref.kind in ("accept_correction", "reject_correction") and not self.correction_pending:
            return "no_correction"
        if ref.kind == "answer" and ref.codes.get("answer") not in (
            "confirm",
            "correct",
            "decline",
            "not_mine",
        ):
            return "bad_answer"
        if ref.kind == "answer" and ref.codes.get("round") != str(self.round):
            return "old_round"
        if (
            ref.kind == "answer"
            and self.first_sent_at is not None
            and ref.saved_at > self.real_at(WAITS.late_window, self.first_sent_at)
        ):
            return "too_late"  # 30 days after the first delivery, in every state (R-OUT-4)
        if ref.kind == "undo":
            if self.undo_deadline is None:
                return "not_confirmed"
            if ref.saved_at > self.undo_deadline:
                return "too_late"
        if ref.kind == "approve" and not set(self.steps()) <= set(ref.ids):
            return "missing_approvals"
        return None

    def steps(self) -> tuple[str, ...]:
        """A contractor's job: first email + 2 reminders. A homeowner-started job (S9): first email + 1 reminder."""
        return SEND_STEPS if self.origin == "contractor" else ("first", "reminder_1")

    @workflow.update
    async def command(self, ref: CommandRef) -> CommandResult:
        return await self.q.submit(ref)

    @command.validator
    def _validate(self, ref: CommandRef) -> None:
        self.q.validate(ref, self._allowed)

    @workflow.signal
    def permits_changed(self) -> None:
        """The permit sync found new or changed permits on this job's parcel (design 3.2, later permits)."""
        self.permits_dirty = True

    @workflow.signal(name="delivered")
    def on_delivered(self, d: Delivered) -> None:
        """From the sender. Ignored unless its outbox row belongs to this round (design 3.5)."""
        if d.round == self.round and d.outbox_id in self.outbox.values():
            self.delivered[d.outbox_id] = d

    @workflow.query
    def state(self) -> str:
        return self.status or "starting"

    # --- small helpers ---------------------------------------------------------------------------
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

    def _tkey(self, step: str, kind: str) -> str:
        assert self.inp is not None
        return f"timer:job-{self.inp.job_id}:{self.round}:{step}:{kind}"

    async def _finish(self, ref: CommandRef, ok: bool, code: str) -> None:
        result = CommandResult(command_id=ref.command_id, ok=ok, code=code, state=self.status)
        await workflow.execute_activity(
            common.finish_command, result, start_to_close_timeout=DB_TIMEOUT, retry_policy=DB_RETRY
        )
        self.q.finish(result)

    def is_open(self) -> bool:
        return self.status not in ("dropped", "stopped", "retracted") and self.closed_reason is None

    def real_at(self, virtual_wait: timedelta, since_real: datetime) -> datetime:
        """The real instant `virtual_wait` after the real instant `since_real` (virtual time, design 3.3)."""
        assert self.clock is not None
        return self.clock.real(self.clock.virtual(since_real) + virtual_wait)

    # --- reading, parcel and permit (S3-S5) ------------------------------------------------------
    async def _read(self) -> None:
        assert self.inp is not None
        if self.document_id is None:
            return
        looks = (
            [("A", "invoice")]
            if self.inp.light
            else [("A", "invoice"), ("B", "invoice"), ("B", "amount_marks")]
        )
        for r, purpose in looks:
            try:
                await workflow.execute_activity(
                    reading.read_document,
                    reading.ReadIn(document_id=self.document_id, reader=r, purpose=purpose),
                    task_queue=f"model-reads-{r}",
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=MODEL_RETRY,
                )
            except ActivityError:
                pass  # a failed read counts as invalid: the person is asked
        await self._db(
            reading.decide_facts,
            reading.FactsIn(job_id=self.inp.job_id, document_id=self.document_id, light=self.inp.light),
        )

    async def _settle(self, key: str) -> None:
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
            if self.approvals:  # contacted in this round before: back to ready means a new round
                await self._new_round("back_to_ready")
            await self._record("job.ready", f"{key}:ready")
        await self._match_permit()

    async def _match_permit(self) -> None:
        """R-COR-1: exactly one permit counts -> the mark. Several -> the contractor picks one."""
        assert self.inp is not None
        if self.permit_marked:
            return
        m: permit_match.MatchPermitOut = await self._db(
            permit_match.match_permit, permit_match.MatchIn(job_id=self.inp.job_id)
        )
        self.permit_candidates = m.pks if m.result == "possible" else []
        if m.already_marked:
            self.permit_marked = True
        elif m.result == "on_file":
            await self._record("permit.matched", f"match:job-{self.inp.job_id}:{m.pks[0]}", pk=m.pks[0])
            self.permit_marked = True

    # --- outreach (S6, S7) -------------------------------------------------------------------------
    async def _send(self, step: str) -> str:
        """Ask the permission check to queue one approved message. Returns 'queued' or the refusal code."""
        assert self.inp is not None
        out: PerformOut = await self._db(
            outbox.perform_send,
            PerformIn(
                approval_id=self.approvals[step],
                action="send_email",
                target=f"job-{self.inp.job_id}",
                round=self.round,
                step=step,
            ),
        )
        if out.ok and out.outbox_id is not None:
            self.outbox[step] = out.outbox_id
            return "queued"
        return out.code

    async def _wait_delivery(self, step: str) -> Delivered | None:
        """Wait for the sender's signal, checking the outbox on the backup schedule (design 3.7).

        Returns None if a command arrives first (the caller handles it and may wait again)."""
        assert self.clock is not None
        oid = self.outbox[step]
        poll = self.clock.real_wait(WAITS.backup_poll, WAITS.backup_poll_floor)
        while True:
            await wait_until(
                lambda: oid in self.delivered or self.q.has_work() or not self.is_open(),
                workflow.now() + poll,
            )
            if oid in self.delivered:
                return self.delivered[oid]
            if self.q.has_work() or not self.is_open():
                return None
            d: Delivered = await self._db(outbox.outbox_state, outbox.OutboxRef(outbox_id=oid))
            if d.state in ("sent", "failed", "unknown", "cancelled"):
                self.delivered[oid] = d
                return d

    async def _delivery(self, step: str) -> Delivered | None:
        """Wait for one message's result, handling commands that arrive meanwhile."""
        while True:
            d = await self._wait_delivery(step)
            if d is not None or not self.is_open():
                return d
            await self.handle(self.q.pop())
            if self.status not in ASKING:
                return None

    async def _hold(self, reason: str, step: str, outbox_id: UUID | None = None) -> None:
        assert self.inp is not None
        if self.status not in ("asking_homeowner", "asking_contractor"):
            return
        await self._db(
            outbox.stop_sending,
            outbox.StopIn(job_id=self.inp.job_id, reason=f"on_hold:{reason}", review_kind="on_hold"),
        )
        await self._record(
            "job.on_hold",
            self._tkey(step, "on_hold"),
            reason=reason,
            outbox_id=str(outbox_id) if outbox_id else None,
        )

    async def _note_delivery(self, step: str, d: Delivered) -> None:
        if d.state == "sent":
            await self._record(
                "job.message_delivered",
                f"outbox:{d.outbox_id}:sent",
                outbox_id=str(d.outbox_id),
                step=step,
                state="sent",
                sent_at=d.sent_at.isoformat() if d.sent_at else None,
            )
            if step == "first" and d.sent_at is not None:
                self.first_sent_at = d.sent_at
        elif d.state in ("failed", "unknown"):
            await self._record(
                "job.message_delivered",
                f"outbox:{d.outbox_id}:{d.state}",
                outbox_id=str(d.outbox_id),
                step=step,
                state=d.state,
            )
            await self._hold(d.state, step, d.outbox_id)
        elif d.state == "cancelled" and not self.unsubscribed and self.status in ASKING:
            await self._hold("cancelled", step, d.outbox_id)  # e.g. the grant was revoked before the claim

    async def _outreach(self) -> None:
        """Send the first email, then the reminders, while nobody has answered (R-OUT-4)."""
        code = await self._send("first")
        if code != "queued":
            await self._hold(code, "first")  # including suppressed: the homeowner can't be asked at all
            return
        d = await self._delivery("first")
        if d is None:
            return
        await self._note_delivery("first", d)
        if self.status not in ASKING or self.first_sent_at is None:
            return
        reminders = (("reminder_1", WAITS.reminder_1), ("reminder_2", WAITS.reminder_2))[
            : len(self.steps()) - 1
        ]
        for step, at in reminders:
            if not await self._idle_until(self.real_at(at, self.first_sent_at)):
                return
            if self.unsubscribed:
                continue
            code = await self._send(step)
            if code == "suppressed":
                self.unsubscribed = True
                continue
            if code != "queued":
                await self._hold(code, step)
                return
            rd = await self._delivery(step)
            if rd is not None:
                await self._note_delivery(step, rd)
            if step == "reminder_2":
                self.reminder2_at = rd.sent_at if rd is not None and rd.sent_at else workflow.now()
            if self.status not in ASKING:
                return
        # The last wait is 7 days after reminder 2 actually went (or was due, if it was skipped).
        anchor = self.reminder2_at or self.real_at(WAITS.reminder_2, self.first_sent_at)
        last = self.real_at(WAITS.last_wait, anchor)
        if await self._idle_until(last):
            await self._record("job.no_answer", self._tkey("last", "no_answer"))

    async def _idle_until(self, deadline: datetime) -> bool:
        """Handle commands until the deadline. True if the job is still waiting for an answer at the end."""
        while self.is_open() and self.status in ASKING:
            await wait_until(lambda: self.q.has_work() or not self.is_open(), deadline)
            if self.q.has_work():
                await self.handle(self.q.pop())
                continue
            return self.status in ASKING
        return False

    async def _undo_window(self) -> None:
        """confirmed -> recorded when the undo window ends (R-REC-3). Real-time floor: 60 s."""
        assert self.clock is not None and self.inp is not None and self.undo_deadline is not None
        while self.status == "confirmed":
            await wait_until(lambda: self.q.has_work(), self.undo_deadline)
            if self.q.has_work():
                await self.handle(self.q.pop())
                continue
            late: answers.LateUndo = await self._db(
                answers.saved_undo, answers.SavedUndoIn(job_id=self.inp.job_id, deadline=self.undo_deadline)
            )
            if late.command_id is not None:  # pressed in time, delivered late: it still counts (design 3.5)
                await self._undo(
                    CommandRef(command_id=late.command_id, kind="undo", saved_at=self.undo_deadline)
                )
                continue
            await self._record("job.recorded", self._tkey("undo", "recorded"))

    async def _undo(self, ref: CommandRef) -> None:
        await self._record("job.undone", f"cmd:{ref.command_id}:job.undone", command_id=str(ref.command_id))
        await self._new_round("undo")
        self.q.seen.add(ref.command_id)
        await self._finish(ref, True, "undone")

    async def _new_round(self, why: str) -> None:
        """A new round: new command IDs and links; the old round's unused approvals and links are voided."""
        assert self.inp is not None
        self.round += 1
        await self._db(
            answers.start_round, answers.RoundIn(job_id=self.inp.job_id, round=self.round, why=why)
        )
        self.approvals, self.outbox, self.delivered = {}, {}, {}
        self.first_sent_at = None
        self.reminder2_at = None
        self.correction_pending = False
        self.confirmed_at = None
        self.undo_deadline = None

    async def _confirmed(self, key: str, command_id: UUID, via: str) -> None:
        assert self.inp is not None
        await self._db(outbox.stop_sending, outbox.StopIn(job_id=self.inp.job_id, reason="answered"))
        # The undo window starts now, before `confirmed` is visible, so a quick undo is never early.
        assert self.clock is not None
        self.confirmed_at = workflow.now()
        self.undo_deadline = self.confirmed_at + self.clock.real_wait(WAITS.undo_window, WAITS.undo_floor)
        await self._record("job.homeowner_confirmed", key, command_id=str(command_id), via=via)

    # --- command handling ------------------------------------------------------------------------
    async def handle(self, ref: CommandRef) -> None:
        assert self.inp is not None
        ckey = f"cmd:{ref.command_id}"
        stale = self._allowed(ref)
        if (
            stale is not None
        ):  # checked again when its turn comes: an earlier command may have moved the job on
            await self._finish(ref, False, stale)
            return
        k = ref.kind
        if k in ("fix_fact", "approve_facts"):
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
        elif k == "pick_permit":
            pk = ref.codes["pk"]
            await self._record(
                "permit.matched",
                f"match:job-{self.inp.job_id}:{pk}",
                pk=pk,
                picked_by_contractor=True,
                command_id=str(ref.command_id),
            )
            self.permit_marked, self.permit_candidates = True, []
            await self._finish(ref, True, "picked")
        elif k == "drop":
            await self._record("job.dropped", f"{ckey}:job.dropped", command_id=str(ref.command_id))
            await self._finish(ref, True, "dropped")
        elif k == "approve":
            chk: outbox.ApprovalCheckOut = await self._db(
                outbox.check_approvals,
                outbox.ApprovalCheckIn(
                    job_id=self.inp.job_id,
                    approval_ids={s: ref.ids[s] for s in self.steps()},
                    round=self.round,
                ),
            )
            if not chk.ok:
                await self._finish(ref, False, chk.code)
                return
            self.approvals = {s: ref.ids[s] for s in self.steps()}
            await self._record(
                "job.approved",
                f"{ckey}:job.approved",
                approval_ids=[str(ref.ids[s]) for s in self.steps()],
                command_id=str(ref.command_id),
            )
            if self.origin == "contractor":  # the contractor's approval states the job happened
                await self._record(
                    "job.contractor_confirmed",
                    f"{ckey}:job.contractor_confirmed",
                    approval_id=str(ref.ids["first"]),
                    command_id=str(ref.command_id),
                )
            await self._finish(ref, True, "approved")
            await self._outreach()
        elif k == "answer":
            await self._answer(ref)
        elif k == "accept_correction":
            self.correction_pending = False
            await self._record(
                "job.correction_accepted", f"{ckey}:job.correction_accepted", command_id=str(ref.command_id)
            )
            await self._confirmed(f"{ckey}:job.homeowner_confirmed", ref.command_id, "correction_accepted")
            await self._finish(ref, True, "accepted")
        elif k == "reject_correction":
            self.correction_pending = False
            await self._db(
                answers.correction_rejected, acts.CommandIn(job_id=self.inp.job_id, command_id=ref.command_id)
            )
            await self._record(
                "job.correction_rejected", f"{ckey}:job.correction_rejected", command_id=str(ref.command_id)
            )
            await self._finish(ref, True, "rejected")
        elif k == "undo":
            await self._undo(ref)
        elif k == "unsubscribed":
            self.unsubscribed = True
            await self._finish(ref, True, "unsubscribed")
        elif k == "ops_stop":
            await self._db(outbox.stop_sending, outbox.StopIn(job_id=self.inp.job_id, reason="ops_stop"))
            await self._record(
                "job.stopped", f"{ckey}:job.stopped", reason="ops_stop", command_id=str(ref.command_id)
            )
            await self._finish(ref, True, "stopped")
        else:
            await self._finish(ref, False, "unknown_command")

    async def _answer(self, ref: CommandRef) -> None:
        """confirm / correct / decline / not my house (R-OUT-3). The typed values stay in the database."""
        assert self.inp is not None
        ckey = f"cmd:{ref.command_id}"
        kind = ref.codes["answer"]
        a: answers.AnswerOut = await self._db(
            answers.apply_answer, acts.CommandIn(job_id=self.inp.job_id, command_id=ref.command_id)
        )
        if not a.ok:
            await self._finish(ref, False, a.code)
            return
        await self._record(
            "job.answered",
            f"{ckey}:job.answered",
            command_id=str(ref.command_id),
            answer=kind,
            link_id=str(ref.link_id) if ref.link_id else None,
        )
        if a.price_visible is not None:
            await self._record(
                "job.price_visibility_set",
                f"{ckey}:job.price_visibility_set",
                command_id=str(ref.command_id),
                price_visible=a.price_visible,
            )
        if kind == "confirm" and self.origin == "homeowner":
            # S9: the contractor confirms through their link; the homeowner stated it when uploading.
            await self._record(
                "job.contractor_confirmed",
                f"{ckey}:job.contractor_confirmed",
                command_id=str(ref.command_id),
                via="link",
            )
            await self._confirmed(f"{ckey}:job.homeowner_confirmed", ref.command_id, "upload")
        elif kind == "confirm":
            self.correction_pending = False
            await self._confirmed(f"{ckey}:job.homeowner_confirmed", ref.command_id, "answer")
        elif kind == "correct":
            await self._db(outbox.stop_sending, outbox.StopIn(job_id=self.inp.job_id, reason="answered"))
            await self._record(
                "job.corrected",
                f"{ckey}:job.corrected",
                command_id=str(ref.command_id),
                finish_date=a.finish_date.isoformat() if a.finish_date else None,
                total=str(a.total) if a.total is not None else None,
            )
            self.correction_pending = True
        else:
            await self._db(
                outbox.stop_sending,
                outbox.StopIn(
                    job_id=self.inp.job_id,
                    reason=kind,
                    review_kind=("not_my_job" if self.origin == "homeowner" else "not_my_house")
                    if kind == "not_mine"
                    else None,
                ),
            )
            await self._record(
                "job.stopped",
                f"{ckey}:job.stopped",
                reason="declined" if kind == "decline" else "not_mine",
                command_id=str(ref.command_id),
            )
        await self._finish(ref, True, kind)

    # --- main ------------------------------------------------------------------------------------
    @workflow.run
    async def run(self, inp: JobIn) -> JobOut:
        self.inp = inp
        self.clock = to_clock(inp.clock)
        prep: acts.PrepOut = await self._db(acts.prepare_job, acts.JobRef(job_id=inp.job_id))
        self.origin = prep.origin
        self.document_id = prep.document_id
        await self._record("job.imported", f"wf:job-{inp.job_id}:imported", origin=self.origin)
        await self._read()
        await self._settle(f"wf:job-{inp.job_id}:1:first")
        while self.is_open():
            if self.status == "confirmed":
                await self._undo_window()
                continue
            if self.status == "recorded":
                break  # retraction runs as its own workflow
            if self.status == "no_answer" and self.first_sent_at is not None:
                end = self.real_at(WAITS.late_window, self.first_sent_at)
                await wait_until(lambda: self.q.has_work() or self.permits_dirty, end)
                if not self.q.has_work() and not self.permits_dirty and workflow.now() >= end:
                    self.closed_reason = "late_window_ended"
                    break
            else:
                await workflow.wait_condition(
                    lambda: self.q.has_work() or not self.is_open() or self.permits_dirty
                )
            if self.permits_dirty:
                self.permits_dirty = False
                if self.status not in ("recorded", "retracted"):
                    await self._match_permit()
            if self.q.has_work():
                await self.handle(self.q.pop())
        while self.q.has_work():  # commands queued behind the one that closed the job get an answer
            await self._finish(self.q.pop(), False, "job_closed")
        await self._db(answers.mark_closed, acts.JobRef(job_id=inp.job_id))
        await workflow.wait_condition(workflow.all_handlers_finished)
        return JobOut(job_id=inp.job_id, status=self.status or "imported", round=self.round)
