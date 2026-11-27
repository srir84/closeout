"""S0 tool proof: the time-skipping kit finishes a 7-day timer quickly, and updates work in it."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest

from closeout.workflows.payloads import ClockIn, CommandRef, CommandResult, PingIn
from closeout.workflows.ping import Ping
from tests.workflow.conftest import running_worker

pytestmark = [pytest.mark.slice("S0")]


async def _noop_finish(result: CommandResult) -> None:  # fake activity
    return None


def _fake_finish():
    from temporalio import activity

    @activity.defn(name="finish_command")
    async def finish_command(result: CommandResult) -> None:
        return None

    return finish_command


async def test_seven_day_timer_under_five_seconds(ts_env) -> None:
    now = datetime.now(UTC)
    inp = PingIn(
        ping_id=uuid.uuid4(),
        clock=ClockIn(epoch_real=now, epoch_virtual=now, scale=1),
        wait_virtual_seconds=7 * 86400,
    )
    async with running_worker(ts_env.client, [Ping], [_fake_finish()]) as tq:
        t0 = time.monotonic()
        out = await ts_env.client.execute_workflow(Ping.run, inp, id=f"ping-{inp.ping_id}", task_queue=tq)
        assert out.finished_state == "finished"
        assert time.monotonic() - t0 < 5


async def test_updates_work_in_the_time_skipping_kit(ts_env) -> None:
    now = datetime.now(UTC)
    inp = PingIn(
        ping_id=uuid.uuid4(),
        clock=ClockIn(epoch_real=now, epoch_virtual=now, scale=1),
        wait_virtual_seconds=3 * 86400,
    )
    async with running_worker(ts_env.client, [Ping], [_fake_finish()]) as tq:
        h = await ts_env.client.start_workflow(Ping.run, inp, id=f"ping-{inp.ping_id}", task_queue=tq)
        ref = CommandRef(command_id=uuid.uuid4(), kind="press", saved_at=now)
        r1 = await h.execute_update(Ping.command, ref, id=str(ref.command_id))
        r2 = await h.execute_update(Ping.command, ref, id=str(ref.command_id))
        assert r1 == r2 and r1.ok
        assert (await h.result()).presses == 1
