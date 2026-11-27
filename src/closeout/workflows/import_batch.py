"""ImportBatch: check the files, make the jobs, start one Job workflow each (design 3.8)."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from closeout.activities import jobs as acts
    from closeout.workflows.util import DB_RETRY, DB_TIMEOUT


@workflow.defn(name="ImportBatch")
class ImportBatch:
    @workflow.run
    async def run(self, inp: acts.BatchIn) -> acts.BatchOut:
        out: acts.BatchOut = await workflow.execute_activity(
            acts.prepare_batch, inp, start_to_close_timeout=timedelta(seconds=120), retry_policy=DB_RETRY
        )
        if out.job_ids:
            await workflow.execute_activity(
                "start_jobs",
                acts.StartJobsIn(job_ids=out.job_ids, clock=inp.clock),
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=DB_RETRY,
            )
        await workflow.execute_activity(
            acts.finish_batch, inp, start_to_close_timeout=DB_TIMEOUT, retry_policy=DB_RETRY
        )
        return out
