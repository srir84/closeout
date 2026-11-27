"""S1: the permit-sync Schedule is safe to create twice, and paused in replay mode (design 3.8)."""

import pytest
from temporalio.client import ScheduleOverlapPolicy

from closeout import schedules

pytestmark = [pytest.mark.slice("S1"), pytest.mark.req("R-DATA-1")]


async def test_schedule_create_twice_then_update(dev_env) -> None:
    client = dev_env.client
    assert await schedules.ensure(client, town="1329", paused=True, task_queue="tq-sched") == "created"
    assert await schedules.ensure(client, town="1329", paused=True, task_queue="tq-sched") == "updated"
    desc = await client.get_schedule_handle("permit-sync-1329").describe()
    assert desc.schedule.state.paused is True
    assert desc.schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
    assert desc.schedule.policy.catchup_window.total_seconds() == 86400
    assert await schedules.ensure(client, town="1329", paused=False, task_queue="tq-sched") == "updated"
    assert (await client.get_schedule_handle("permit-sync-1329").describe()).schedule.state.paused is False
    await client.get_schedule_handle("permit-sync-1329").delete()
