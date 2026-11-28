"""Retract: after the undo window, the contractor can only retract a recorded job (R-REC-3). It hides the job."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from closeout.activities import jobs as acts
    from closeout.workflows.util import DB_RETRY, DB_TIMEOUT


class RetractIn(BaseModel):
    job_id: UUID
    command_id: UUID
    round: int


@workflow.defn(name="Retract")
class Retract:
    @workflow.run
    async def run(self, inp: RetractIn) -> str:
        out: acts.RecordOut = await workflow.execute_activity(
            acts.record,
            acts.RecordIn(
                job_id=inp.job_id,
                kind="job.retracted",
                payload={"round": inp.round, "command_id": str(inp.command_id)},
                event_key=f"cmd:{inp.command_id}:job.retracted",
            ),
            start_to_close_timeout=DB_TIMEOUT,
            retry_policy=DB_RETRY,
        )
        return out.status
