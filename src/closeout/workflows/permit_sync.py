"""PermitSync: one bounded run per day for one town (design 3.8). No continue-as-new."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from closeout.activities.permits import SyncIn, SyncOut, sync_permits

FEED_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=3.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
)


@workflow.defn(name="PermitSync")
class PermitSync:
    @workflow.run
    async def run(self, inp: SyncIn) -> SyncOut:
        out: SyncOut = await workflow.execute_activity(
            sync_permits, inp, start_to_close_timeout=timedelta(seconds=120), retry_policy=FEED_RETRY
        )
        for jid in out.job_ids:
            # Later permits (design 3.2): each unmarked job on a changed parcel checks again.
            try:
                await workflow.get_external_workflow_handle(f"job-{jid}").signal("permits_changed")
            except Exception:  # a closed or missing job workflow has nothing to re-check
                workflow.logger.info("job %s not running", jid)
        return out
