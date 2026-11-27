"""Activities for homeowner answers, corrections, undo and new rounds (design 3.4, 3.5, R-OUT-3, R-REC-3)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel
from temporalio import activity

from closeout import db
from closeout.activities.jobs import CommandIn, open_review
from closeout.activities.safety import scrubbed
from closeout.core.clean import clean_date, clean_money


class AnswerOut(BaseModel):
    price_visible: bool | None
    finish_date: date | None = None
    total: Decimal | None = None


class SavedUndoIn(BaseModel):
    job_id: UUID
    deadline: datetime


class LateUndo(BaseModel):
    command_id: UUID | None


class RoundIn(BaseModel):
    job_id: UUID
    round: int
    why: str


@activity.defn
@scrubbed
def apply_answer(inp: CommandIn) -> AnswerOut:
    """Store what the homeowner said. Corrected dates and amounts come back (they may go in events); text stays here."""
    with db.tx() as c:
        cmd = db.one(c, "SELECT payload FROM commands WHERE command_id = :id", id=inp.command_id)
        p = (cmd or {}).get("payload") or {}
        today = db.scalar(c, "SELECT (now() AT TIME ZONE 'UTC')::date")
        pv = p.get("price_visible")
        price_visible = None if pv in (None, "") else str(pv).lower() in ("yes", "true", "1")
        fd = tot = None
        if p.get("answer") == "correct":
            if p.get("finish_date"):
                c1 = clean_date(p["finish_date"], today)
                if c1.ok:
                    assert isinstance(c1.value, date)
                    fd = c1.value
                    db.run(
                        c,
                        """UPDATE facts SET value = :v, status = 'answered', source = 'person',
                                 reason = 'homeowner_correction', updated_at = now()
                                 WHERE job_id = :j AND field = 'completion_date'""",
                        v=fd.isoformat(),
                        j=inp.job_id,
                    )
            if p.get("total"):
                c2 = clean_money(p["total"])
                if c2.ok:
                    tot = c2.value
                    db.run(
                        c,
                        """UPDATE facts SET value = :v, status = 'answered', source = 'person',
                                 reason = 'homeowner_correction', updated_at = now()
                                 WHERE job_id = :j AND field = 'total_amount'""",
                        v=str(tot),
                        j=inp.job_id,
                    )
        if price_visible is not None:
            db.run(c, "UPDATE jobs SET price_visible = :v WHERE id = :id", v=price_visible, id=inp.job_id)
    return AnswerOut(price_visible=price_visible, finish_date=fd, total=tot)  # type: ignore[arg-type]


@activity.defn
@scrubbed
def correction_rejected(inp: CommandIn) -> None:
    """The contractor disagrees with the homeowner's correction: a person settles it (R-OPS-1)."""
    with db.tx() as c:
        open_review(
            c,
            "correction_rejected",
            f"job-{inp.job_id}",
            "correction_rejected",
            {"job_id": str(inp.job_id), "command_id": str(inp.command_id)},
            f"correction:{inp.command_id}",
        )
        db.run(c, "UPDATE jobs SET needs_reason = 'correction_rejected' WHERE id = :id", id=inp.job_id)


@activity.defn
@scrubbed
def saved_undo(inp: SavedUndoIn) -> LateUndo:
    """An undo saved before the window ended counts even if it arrives late (windows by saved_at, design 3.5)."""
    with db.tx() as c:
        cid = db.scalar(
            c,
            """SELECT command_id FROM commands WHERE target_workflow = :wf AND kind = 'undo'
                              AND state IN ('saved', 'accepted') AND saved_at <= :d
                              ORDER BY saved_at LIMIT 1""",
            wf=f"job-{inp.job_id}",
            d=inp.deadline,
        )
    return LateUndo(command_id=cid)


@activity.defn
@scrubbed
def start_round(inp: RoundIn) -> int:
    """A new round: the old round's unused approvals are voided, its links revoked, its pending sends cancelled."""
    target = f"job-{inp.job_id}"
    with db.tx() as c:
        db.scalar(c, "SELECT id FROM jobs WHERE id = :id FOR UPDATE", id=inp.job_id)
        db.run(
            c,
            "UPDATE jobs SET round = GREATEST(round, :r), updated_at = now() WHERE id = :id",
            r=inp.round,
            id=inp.job_id,
        )
        n = db.run(
            c,
            """UPDATE approvals SET voided_at = now() WHERE target = :t AND round < :r
                         AND used_at IS NULL AND voided_at IS NULL""",
            t=target,
            r=inp.round,
        )
        db.run(
            c,
            """UPDATE links SET revoked_at = now() WHERE target = :t AND round < :r AND revoked_at IS NULL
                     AND purpose <> 'unsubscribe'""",
            t=target,
            r=inp.round,
        )
        db.run(
            c,
            """UPDATE outbox SET state = 'cancelled', cancel_reason = 'new_round', updated_at = now()
                     WHERE job_id = :j AND round < :r AND state = 'pending'""",
            j=inp.job_id,
            r=inp.round,
        )
        db.run(
            c,
            """INSERT INTO audit_log (action, target, details) VALUES ('new_round', :t, CAST(:d AS jsonb))""",
            t=target,
            d=json.dumps({"round": inp.round, "why": inp.why}),
        )
    return n
