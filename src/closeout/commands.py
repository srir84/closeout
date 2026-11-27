"""The click protocol, client side (design 3.5).

1. The caller has already checked the session or link.
2. save(): a `commands` row with the typed values in `payload` (cleanable).
3. send(): a Temporal update whose update ID is the command ID, carrying only IDs and codes.
4. The page waits up to 5 s; the worker's sweep re-sends anything still `saved`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from temporalio.client import (
    Client,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
    WorkflowUpdateStage,
)
from temporalio.service import RPCError, RPCStatusCode

from closeout import db
from closeout.workflows.payloads import CommandRef, CommandResult

log = logging.getLogger("closeout.commands")


@dataclass
class Outcome:
    status: str  # done | rejected | saved
    code: str
    result: CommandResult | None = None


def save(
    *,
    target_workflow: str,
    kind: str,
    account_id: uuid.UUID | None = None,
    link_id: uuid.UUID | None = None,
    ids: dict[str, uuid.UUID] | None = None,
    codes: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    command_id: uuid.UUID | None = None,
) -> CommandRef:
    cid = command_id or uuid.uuid4()
    refs = {"ids": {k: str(v) for k, v in (ids or {}).items()}, "codes": codes or {}}
    with db.tx() as c:
        row = db.one(
            c,
            """INSERT INTO commands (command_id, account_id, link_id, target_workflow, kind, refs, payload)
                           VALUES (:id, :acct, :link, :wf, :kind, CAST(:refs AS jsonb), CAST(:payload AS jsonb))
                           ON CONFLICT (command_id) DO NOTHING RETURNING saved_at""",
            id=cid,
            acct=account_id,
            link=link_id,
            wf=target_workflow,
            kind=kind,
            refs=json.dumps(refs),
            payload=json.dumps(payload or {}, default=str),
        )
        if row is None:
            row = db.one(c, "SELECT saved_at FROM commands WHERE command_id = :id", id=cid)
    assert row is not None
    return ref_from_row(cid, kind, row["saved_at"], account_id, link_id, refs)


def ref_from_row(
    cid: uuid.UUID,
    kind: str,
    saved_at: datetime,
    account_id: uuid.UUID | None,
    link_id: uuid.UUID | None,
    refs: dict[str, Any],
) -> CommandRef:
    return CommandRef(
        command_id=cid,
        kind=kind,
        saved_at=saved_at,
        account_id=account_id,
        link_id=link_id,
        ids={k: uuid.UUID(v) for k, v in refs.get("ids", {}).items()},
        codes=dict(refs.get("codes", {})),
    )


def _mark(cid: uuid.UUID, state: str, result: dict[str, Any] | None) -> None:
    with db.tx() as c:
        if state == "accepted":
            db.run(
                c,
                "UPDATE commands SET state='accepted', updated_at=now() WHERE command_id=:id AND state='saved'",
                id=cid,
            )
        else:
            db.run(
                c,
                """UPDATE commands SET state=:st, result=CAST(:res AS jsonb), updated_at=now()
                         WHERE command_id=:id AND state IN ('saved','accepted')""",
                id=cid,
                st=state,
                res=json.dumps(result),
            )


def _closed(e: RPCError) -> bool:
    return e.status in (RPCStatusCode.NOT_FOUND, RPCStatusCode.FAILED_PRECONDITION)


async def send(client: Client, workflow_id: str, ref: CommandRef, wait_seconds: float = 5.0) -> Outcome:
    """Send the update. Returns within about wait_seconds."""
    handle = client.get_workflow_handle(workflow_id)
    try:
        upd = await asyncio.wait_for(
            handle.start_update(
                "command",
                ref,
                id=str(ref.command_id),
                result_type=CommandResult,
                wait_for_stage=WorkflowUpdateStage.ACCEPTED,
            ),
            timeout=wait_seconds,
        )
    except (TimeoutError, WorkflowUpdateRPCTimeoutOrCancelledError):
        return Outcome("saved", "saved")
    except WorkflowUpdateFailedError as e:
        code = _reason(e)
        await asyncio.to_thread(_mark, ref.command_id, "rejected", {"ok": False, "code": code})
        return Outcome("rejected", code)
    except RPCError as e:
        if _closed(e):
            await asyncio.to_thread(
                _mark, ref.command_id, "rejected", {"ok": False, "code": "workflow_closed"}
            )
            return Outcome("rejected", "workflow_closed")
        log.warning("update send failed: %s", e.status)
        return Outcome("saved", "saved")
    await asyncio.to_thread(_mark, ref.command_id, "accepted", None)
    try:
        res = await asyncio.wait_for(upd.result(), timeout=wait_seconds)
    except (TimeoutError, WorkflowUpdateRPCTimeoutOrCancelledError):
        return Outcome("saved", "working")
    except WorkflowUpdateFailedError as e:
        code = _reason(e)
        await asyncio.to_thread(_mark, ref.command_id, "rejected", {"ok": False, "code": code})
        return Outcome("rejected", code)
    await asyncio.to_thread(
        _mark, ref.command_id, "done" if res.ok else "rejected", res.model_dump(mode="json")
    )
    return Outcome("done" if res.ok else "rejected", res.code, res)


def _reason(e: WorkflowUpdateFailedError) -> str:
    cause = e.cause
    msg = getattr(cause, "message", None) or str(cause or e)
    return str(msg).split(":")[0].strip() or "rejected"


def pending(older_than_seconds: float = 0) -> list[tuple[str, CommandRef]]:
    with db.tx() as c:
        rows = db.all_rows(
            c,
            """SELECT command_id, kind, saved_at, account_id, link_id, refs, target_workflow
                                 FROM commands WHERE state IN ('saved','accepted')
                                 AND saved_at < now() - make_interval(secs => :age)
                                 ORDER BY saved_at LIMIT 500""",
            age=older_than_seconds,
        )
    return [
        (
            r["target_workflow"],
            ref_from_row(r["command_id"], r["kind"], r["saved_at"], r["account_id"], r["link_id"], r["refs"]),
        )
        for r in rows
    ]


async def sweep(client: Client, older_than_seconds: float = 5.0) -> int:
    """Re-send every saved command. Same update ID, so Temporal returns the first outcome."""
    n = 0
    for wf, ref in await asyncio.to_thread(pending, older_than_seconds):
        out = await send(client, wf, ref, wait_seconds=10)
        n += out.status != "saved"
    return n


def read_result(cid: uuid.UUID) -> dict[str, Any] | None:
    with db.tx() as c:
        return db.one(c, "SELECT state, result FROM commands WHERE command_id = :id", id=cid)
