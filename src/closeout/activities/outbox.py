"""Activities that touch approvals and the outbox (design 3.6, 3.7)."""

from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel
from temporalio import activity

from closeout import db
from closeout.act import PerformIn, PerformOut, audit, perform
from closeout.activities.safety import scrubbed
from closeout.workflows.payloads import Delivered


class OutboxRef(BaseModel):
    outbox_id: UUID


class ApprovalCheckIn(BaseModel):
    job_id: UUID
    approval_ids: dict[str, UUID]
    round: int


class ApprovalCheckOut(BaseModel):
    ok: bool
    code: str


class StopIn(BaseModel):
    job_id: UUID
    reason: str
    review_kind: str | None = None


@activity.defn
@scrubbed
def perform_send(inp: PerformIn) -> PerformOut:
    """The one permission check, then one outbox row. Safe to retry: the same command returns the first result."""
    with db.tx() as c:
        return perform(c, inp)


@activity.defn
@scrubbed
def outbox_state(inp: OutboxRef) -> Delivered:
    """The workflow's backup check for a delivery result (design 3.7)."""
    with db.tx() as c:
        r = db.one(c, "SELECT id, state, sent_at, round FROM outbox WHERE id = :id", id=inp.outbox_id)
    assert r is not None
    return Delivered(outbox_id=r["id"], state=r["state"], sent_at=r["sent_at"], round=r["round"])


@activity.defn
@scrubbed
def check_approvals(inp: ApprovalCheckIn) -> ApprovalCheckOut:
    """Before moving on: the approvals exist, are for this job's current round and steps, and aren't voided."""
    with db.tx() as c:
        for step, aid in inp.approval_ids.items():
            a = db.one(c, "SELECT target, round, step, voided_at FROM approvals WHERE id = :id", id=aid)
            if a is None:
                return ApprovalCheckOut(ok=False, code="unknown_nonce")
            if a["voided_at"] is not None:
                return ApprovalCheckOut(ok=False, code="voided")
            if (a["target"], a["round"], a["step"]) != (f"job-{inp.job_id}", inp.round, step):
                return ApprovalCheckOut(ok=False, code="wrong_target")
    return ApprovalCheckOut(ok=True, code="ok")


@activity.defn
@scrubbed
def stop_sending(inp: StopIn) -> int:
    """Anything that stops a job cancels its pending messages, holding the job row, so a claim can't overlap."""
    with db.tx() as c:
        db.scalar(c, "SELECT id FROM jobs WHERE id = :j FOR UPDATE", j=inp.job_id)
        rows = db.all_rows(
            c,
            """UPDATE outbox SET state = 'cancelled', cancel_reason = :r, updated_at = now()
                                 WHERE job_id = :j AND state = 'pending' RETURNING id""",
            r=inp.reason,
            j=inp.job_id,
        )
        if inp.review_kind:
            db.run(
                c,
                """INSERT INTO review_items (kind, ref, reason, evidence, dedupe_key)
                         VALUES (:k, :ref, :r, CAST(:e AS jsonb), :d) ON CONFLICT (dedupe_key)
                         DO UPDATE SET resolved_at = NULL""",
                k=inp.review_kind,
                ref=f"job-{inp.job_id}",
                r=inp.reason,
                e=json.dumps({"job_id": str(inp.job_id)}),
                d=f"{inp.review_kind}:{inp.reason}:{inp.job_id}",
            )
        audit(
            c,
            "stop_sending",
            f"job-{inp.job_id}",
            None,
            None,
            None,
            {"reason": inp.reason, "cancelled": len(rows)},
        )
        return len(rows)
