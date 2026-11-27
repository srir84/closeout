"""S0 tool proof: updates on the real dev server, with the click protocol and the sweep."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from temporalio.client import WorkflowUpdateFailedError

from closeout import commands
from closeout.activities import common
from closeout.workflows.payloads import ClockIn, CommandRef, PingIn, PingOut
from closeout.workflows.ping import Ping
from tests.workflow.conftest import running_worker

pytestmark = [pytest.mark.slice("S0")]


def ping_in(wait: float = 3600) -> PingIn:
    now = datetime.now(UTC)
    return PingIn(
        ping_id=uuid.uuid4(),
        clock=ClockIn(epoch_real=now, epoch_virtual=now, scale=1),
        wait_virtual_seconds=wait,
    )


async def test_same_update_id_twice_returns_the_first_result(dev_env, seeded) -> None:
    client = dev_env.client
    async with running_worker(client, [Ping], [common.finish_command]) as tq:
        inp = ping_in(wait=3)
        h = await client.start_workflow(Ping.run, inp, id=f"ping-{inp.ping_id}", task_queue=tq)
        ref = commands.save(target_workflow=h.id, kind="press")
        first = await commands.send(client, h.id, ref)
        second = await commands.send(client, h.id, ref)
        assert first.status == "done" and second.status == "done"
        assert first.result == second.result
        out: PingOut = await h.result()
        assert out.presses == 1
        row = commands.read_result(ref.command_id)
        assert row is not None and row["state"] == "done"


async def test_validator_rejects_a_bad_request(dev_env, seeded) -> None:
    client = dev_env.client
    async with running_worker(client, [Ping], [common.finish_command]) as tq:
        inp = ping_in(wait=2)
        h = await client.start_workflow(Ping.run, inp, id=f"ping-{inp.ping_id}", task_queue=tq)
        bad = CommandRef(command_id=uuid.uuid4(), kind="launch_rockets", saved_at=datetime.now(UTC))
        with pytest.raises(WorkflowUpdateFailedError):
            await h.execute_update(Ping.command, bad, id=str(bad.command_id))
        ref = commands.save(target_workflow=h.id, kind="launch_rockets")
        out = await commands.send(client, h.id, ref)
        assert (out.status, out.code) == ("rejected", "unknown_command")
        assert (await h.result()).presses == 0


async def test_worker_down_then_sweep_delivers_once(dev_env, seeded) -> None:
    client = dev_env.client
    tq = f"tq-{uuid.uuid4()}"
    inp = ping_in(wait=30)
    h = await client.start_workflow(Ping.run, inp, id=f"ping-{inp.ping_id}", task_queue=tq)
    ref = commands.save(target_workflow=h.id, kind="press")
    out = await commands.send(client, h.id, ref, wait_seconds=1.5)  # no worker: times out
    assert out.status == "saved"
    assert commands.read_result(ref.command_id)["state"] == "saved"
    async with running_worker(client, [Ping], [common.finish_command], task_queue=tq):
        n1 = await commands.sweep(client, older_than_seconds=0)
        n2 = await commands.sweep(client, older_than_seconds=0)
        assert n1 == 1 and n2 == 0
        assert commands.read_result(ref.command_id)["state"] == "done"
        await h.terminate("test over")
    hist = await h.fetch_history()
    accepted = [e for e in hist.events if e.HasField("workflow_execution_update_accepted_event_attributes")]
    assert len(accepted) == 1


async def test_sweep_marks_commands_for_closed_workflows(dev_env, seeded) -> None:
    client = dev_env.client
    async with running_worker(client, [Ping], [common.finish_command]) as tq:
        inp = ping_in(wait=0.5)
        h = await client.start_workflow(Ping.run, inp, id=f"ping-{inp.ping_id}", task_queue=tq)
        await h.result()
        ref = commands.save(target_workflow=h.id, kind="press")
        await commands.sweep(client, older_than_seconds=0)
        row = commands.read_result(ref.command_id)
        assert row["state"] == "rejected" and row["result"]["code"] == "workflow_closed"


async def test_a_reused_command_id_for_another_request_is_refused(dev_env, seeded) -> None:
    ref = commands.save(target_workflow="ping-x", kind="press")
    again = commands.save(target_workflow="ping-x", kind="press", command_id=ref.command_id)
    assert again == ref
    with pytest.raises(commands.CommandConflict):
        commands.save(target_workflow="ping-x", kind="other", command_id=ref.command_id)
    with pytest.raises(commands.CommandConflict):
        commands.save(target_workflow="ping-y", kind="press", command_id=ref.command_id)
