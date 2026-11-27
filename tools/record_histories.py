"""Record workflow histories for the replay tests (tests/replay/histories/).

Run against a dev server started by the time-skipping kit; each scenario saves one history JSON.
Usage: uv run python tools/record_histories.py [scenario ...]
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from closeout.temporal import DATA_CONVERTER  # noqa: E402
from closeout.workflows.payloads import ClockIn, CommandRef, CommandResult, PingIn  # noqa: E402
from closeout.workflows.ping import Ping  # noqa: E402

OUT = ROOT / "tests" / "replay" / "histories"
IDENTITY = "closeout-recorder"


def scrub(obj: object) -> object:
    """Replace worker and client identities (they hold the machine's hostname) before saving."""
    if isinstance(obj, dict):
        return {k: (IDENTITY if k == "identity" else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


@activity.defn(name="finish_command")
def fake_finish(result: CommandResult) -> None:
    return None


async def ping_pressed(env: WorkflowEnvironment) -> tuple[str, str]:
    now = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    inp = PingIn(
        ping_id=uuid.UUID(int=1),
        clock=ClockIn(epoch_real=now, epoch_virtual=now, scale=1),
        wait_virtual_seconds=3600,
    )
    with ThreadPoolExecutor() as pool:
        async with Worker(
            env.client,
            task_queue="rec",
            workflows=[Ping],
            activities=[fake_finish],
            activity_executor=pool,
            identity=IDENTITY,
        ):
            h = await env.client.start_workflow(Ping.run, inp, id="ping-recorded-1", task_queue="rec")
            ref = CommandRef(command_id=uuid.UUID(int=2), kind="press", saved_at=now)
            await h.execute_update(Ping.command, ref, id=str(ref.command_id))
            await h.result()
            hist = await h.fetch_history()
    return "ping_pressed", hist.to_json()


async def job_scenario(env: WorkflowEnvironment, name: str) -> tuple[str, str]:
    """Job workflow histories at its waiting points and unusual paths (design 3.8)."""
    sys.path.insert(0, str(ROOT))
    from datetime import timedelta

    from tests.workflow import test_outreach as t

    w = t.World(refuse={"reminder_1": "over_limit"} if name == "job_on_hold" else None)
    async with t.job(env, w) as h:
        await t.approved(h, w, env)
        if name == "job_recorded":
            await t.send(h, "answer", answer="confirm", round="1")
            await h.result()
        elif name == "job_no_answer":
            await h.result()
        elif name == "job_undo_round_2":
            await t.send(h, "answer", answer="confirm", round="1")
            await t.send(h, "undo")
            await t.send(h, "approve")
            await t.until_state(h, w, "asking_homeowner", env)
            await t.send(h, "answer", answer="confirm", round="2")
            await h.result()
        elif name == "job_corrected_accepted":
            await t.send(h, "answer", answer="correct", round="1")
            await t.send(h, "accept_correction")
            await h.result()
        elif name == "job_on_hold":
            await t.until_state(h, w, "on_hold", env, step=timedelta(hours=6))
            await t.send(h, "ops_stop")
            await h.result()
        elif name == "job_between_reminders":
            await env.sleep(timedelta(days=4))
            await h.terminate("saved while waiting between reminders")
        hist = await h.fetch_history()
    return name, hist.to_json()


JOB_SCENARIOS = (
    "job_recorded",
    "job_no_answer",
    "job_undo_round_2",
    "job_corrected_accepted",
    "job_on_hold",
    "job_between_reminders",
)
SCENARIOS = {
    "ping_pressed": ping_pressed,
    **{n: (lambda env, n=n: job_scenario(env, n)) for n in JOB_SCENARIOS},
}


async def main(names: list[str]) -> None:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER)
    try:
        for name in names or list(SCENARIOS):
            stem, js = await SCENARIOS[name](env)
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / f"{stem}.json").write_text(json.dumps(scrub(json.loads(js)), indent=1))
            print("saved", stem)
    finally:
        await env.shutdown()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
