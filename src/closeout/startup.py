"""Things the worker does once when it starts (design 3.8)."""

from __future__ import annotations

import asyncio
import json
import logging

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from closeout import db

log = logging.getLogger("closeout.startup")
OPEN_JOB_STATES = (
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
BROKEN = (
    WorkflowExecutionStatus.FAILED,
    WorkflowExecutionStatus.TERMINATED,
    WorkflowExecutionStatus.TIMED_OUT,
    WorkflowExecutionStatus.CANCELED,
)


async def jobs_without_workflow(client: Client) -> list[str]:
    """Open jobs whose Job workflow is missing or failed get a review item for ops."""

    def open_jobs() -> list[str]:
        with db.tx() as c:
            return [
                str(r["id"])
                for r in db.all_rows(c, "SELECT id FROM jobs WHERE status = ANY(:s)", s=list(OPEN_JOB_STATES))
            ]

    found = []
    for jid in await asyncio.to_thread(open_jobs):
        try:
            desc = await client.get_workflow_handle(f"job-{jid}").describe()
            if desc.status not in BROKEN:
                continue
            why = "workflow_" + desc.status.name.lower() if desc.status else "workflow_unknown"
        except RPCError as e:
            if e.status != RPCStatusCode.NOT_FOUND:
                raise
            why = "workflow_missing"
        found.append(jid)

        def open_item(jid: str = jid, why: str = why) -> None:
            with db.tx() as c:
                db.run(
                    c,
                    """INSERT INTO review_items (kind, ref, reason, evidence, dedupe_key)
                             VALUES ('job_without_workflow', :r, :why, CAST(:e AS jsonb), :d)
                             ON CONFLICT (dedupe_key) DO NOTHING""",
                    r=f"job-{jid}",
                    why=why,
                    e=json.dumps({"job_id": jid}),
                    d=f"no_workflow:{jid}",
                )

        await asyncio.to_thread(open_item)
    return found


async def on_worker_start(client: Client) -> None:
    from closeout import schedules

    log.info("worker starting; permit-sync schedule %s", await schedules.ensure(client))
    try:
        missing = await jobs_without_workflow(client)
        if missing:
            log.warning("%d open jobs have no running workflow; review items opened", len(missing))
    except Exception as e:  # never block the worker from starting
        log.warning("startup check failed: %s", type(e).__name__)
