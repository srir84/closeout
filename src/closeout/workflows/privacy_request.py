"""PrivacyRequest: remove a person's details everywhere (design 3.17, R-OPS-2)."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from closeout.activities import privacy_acts as acts
    from closeout.workflows.util import DB_RETRY, DB_TIMEOUT


@workflow.defn(name="PrivacyRequest")
class PrivacyRequest:
    @workflow.run
    async def run(self, inp: acts.PrivacyIn) -> dict[str, int]:
        async def step(fn):  # type: ignore[no-untyped-def]
            return await workflow.execute_activity(
                fn, inp, start_to_close_timeout=DB_TIMEOUT, retry_policy=DB_RETRY
            )

        await step(acts.privacy_mark)  # 1. from here on, late writes store blanks
        await step(acts.privacy_stop)  # 2. links revoked, queued messages cancelled
        for _ in range(30):  # 3. let clicks and sends in flight settle (a click for a closed job may never)
            if await step(acts.privacy_busy) == 0:
                break
            await workflow.sleep(timedelta(seconds=2))
        # A send a sender has claimed must finish first, or it could deliver after Mailpit is cleaned. Claimed
        # sends always end: sent, failed or, through the reaper, unknown. So this wait has no cap.
        if await step(acts.privacy_sending):
            await step(acts.privacy_waiting)
            while await step(acts.privacy_sending):
                await workflow.sleep(timedelta(seconds=5))
        out: acts.Counts = await step(acts.privacy_redact)  # 4. every store
        return out.counts
