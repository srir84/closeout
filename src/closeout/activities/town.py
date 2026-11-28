"""Activities for asking the town (R-TOWN-1, R-TOWN-2). Results carry IDs, codes and dates only."""

from __future__ import annotations

import json
from datetime import date
from uuid import UUID

from pydantic import BaseModel
from temporalio import activity

from closeout import clockdb, db
from closeout.activities.jobs import open_review
from closeout.activities.safety import scrubbed
from closeout.core.town import decide
from closeout.events import append_next


class TownRef(BaseModel):
    request_id: UUID


class TownStatusIn(BaseModel):
    request_id: UUID
    status: str


class ReplyIn(BaseModel):
    request_id: UUID
    document_id: UUID
    command_id: UUID


class ReplyOut(BaseModel):
    result: str
    reason: str
    result_date: date | None


class TownCommandIn(BaseModel):
    command_id: UUID


class DocRef(BaseModel):
    document_id: UUID | None


@activity.defn
@scrubbed
def town_status(inp: TownStatusIn) -> None:
    with db.tx() as c:
        db.run(
            c,
            """UPDATE town_requests SET status = :s, updated_at = now(),
                     closed_at = CASE WHEN :s IN ('no_reply', 'closed', 'replied') THEN coalesce(closed_at, now())
                                 ELSE closed_at END WHERE id = :id""",
            s=inp.status,
            id=inp.request_id,
        )
        if inp.status == "no_reply":
            db.run(c, "UPDATE town_requests SET result = NULL WHERE id = :id", id=inp.request_id)
        db.run(
            c,
            "INSERT INTO audit_log (action, target, details) VALUES ('town_status', :t, CAST(:d AS jsonb))",
            t=f"town-{inp.request_id}",
            d=json.dumps({"status": inp.status}),
        )


@activity.defn
@scrubbed
def reply_document(inp: TownCommandIn) -> DocRef:
    """The document the clerk uploaded with this command (only its ID reaches the workflow)."""
    with db.tx() as c:
        refs = db.scalar(c, "SELECT refs FROM commands WHERE command_id = :c", c=inp.command_id) or {}
    d = (refs.get("ids") or {}).get("document_id")
    return DocRef(document_id=UUID(d) if d else None)


@activity.defn
@scrubbed
def decide_reply(inp: ReplyIn) -> ReplyOut:
    """R-TOWN-2: a result is shown only if both reads agree on it and its date; otherwise unclear, for ops."""
    with db.tx() as c:
        t = db.one(
            c,
            """SELECT t.*, p.permit_no FROM town_requests t JOIN permits p ON p.pk = t.permit_pk
                         WHERE t.id = :id FOR UPDATE""",
            id=inp.request_id,
        )
        assert t is not None
        reads = {
            r["reader"]: r["output"] if r["valid"] else None
            for r in db.all_rows(
                c,
                "SELECT reader, output, valid FROM reads WHERE document_id = :d AND purpose = 'town_reply'",
                d=inp.document_id,
            )
        }
        today = clockdb.virtual_now(c).date()
        got = decide(reads.get("A"), reads.get("B"), t["permit_no"] or "", today)
        db.run(
            c,
            """UPDATE town_requests SET status = 'replied', result = :r, result_date = :d, updated_at = now(),
                     closed_at = coalesce(closed_at, now()) WHERE id = :id""",
            r=got.result,
            d=got.result_date,
            id=inp.request_id,
        )
        append_next(
            c,
            f"home-{t['home_pin']}",
            "town.reply_recorded",
            {
                "town_request_id": str(inp.request_id),
                "permit_pk": t["permit_pk"],
                "result": got.result,
                "result_date": got.result_date.isoformat() if got.result_date else None,
            },
            f"cmd:{inp.command_id}:town.reply_recorded",
        )
        if got.result == "unclear":
            # The homeowner never settles a disagreement about their own home: ops does.
            open_review(
                c,
                "town_reply_unclear",
                f"town-{inp.request_id}",
                got.reason,
                {"town_request_id": str(inp.request_id), "document_id": str(inp.document_id)},
                f"town_unclear:{inp.request_id}",
            )
    return ReplyOut(result=got.result, reason=got.reason, result_date=got.result_date)


@activity.defn
@scrubbed
def stop_town_sending(inp: TownRef) -> int:
    with db.tx() as c:
        db.scalar(c, "SELECT id FROM town_requests WHERE id = :id FOR UPDATE", id=inp.request_id)
        rows = db.all_rows(
            c,
            """UPDATE outbox SET state = 'cancelled', cancel_reason = 'replied', updated_at = now()
                                 WHERE town_request_id = :t AND state = 'pending' RETURNING id""",
            t=inp.request_id,
        )
    return len(rows)


@activity.defn
@scrubbed
def town_hold(inp: TownStatusIn) -> None:
    """A send was refused or failed: a person looks."""
    with db.tx() as c:
        open_review(
            c,
            "town_on_hold",
            f"town-{inp.request_id}",
            inp.status,
            {"town_request_id": str(inp.request_id)},
            f"town_hold:{inp.request_id}:{inp.status}",
        )
