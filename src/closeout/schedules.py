"""The permit-sync Schedule: created at worker start, or updated if it exists (design 3.8)."""

from __future__ import annotations

from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from closeout.activities.permits import SyncIn
from closeout.settings import get_settings


def schedule_id(town: str) -> str:
    return f"permit-sync-{town}"


def build(town: str, paused: bool, task_queue: str) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            "PermitSync", SyncIn(town=town), id=f"permit-sync-{town}-run", task_queue=task_queue
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(days=1))]),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(days=1)),
        state=ScheduleState(paused=paused, note="paused in replay mode" if paused else ""),
    )


async def ensure(
    client: Client, town: str = "1329", paused: bool | None = None, task_queue: str | None = None
) -> str:
    s = get_settings()
    paused = (s.closeout_mode == "replay") if paused is None else paused
    sched = build(town, paused, task_queue or s.task_queue)
    sid = schedule_id(town)
    try:
        await client.create_schedule(sid, sched)
        return "created"
    except ScheduleAlreadyRunningError:

        async def update(_: ScheduleUpdateInput) -> ScheduleUpdate:
            return ScheduleUpdate(schedule=sched)

        await client.get_schedule_handle(sid).update(update)
        return "updated"
