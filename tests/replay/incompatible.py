"""A deliberately incompatible change to Ping, used to prove the replay check catches it."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from closeout.workflows.payloads import PingIn, PingOut


@workflow.defn(name="Ping", sandboxed=False)
class PingChanged:
    @workflow.run
    async def run(self, inp: PingIn) -> PingOut:
        await workflow.sleep(timedelta(seconds=1))
        await workflow.sleep(timedelta(seconds=1))
        return PingOut(ping_id=inp.ping_id, presses=0, finished_state="finished")
