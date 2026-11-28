"""S12: the PrivacyRequest workflow redacts only after sends in flight have ended (review finding 6)."""

from __future__ import annotations

import uuid

import pytest
from temporalio import activity
from temporalio.worker import Worker

from closeout.activities import privacy_acts as acts
from closeout.workflows.privacy_request import PrivacyRequest

pytestmark = [pytest.mark.slice("S12"), pytest.mark.req("R-OPS-2")]


async def run(env, busy_for: int, sending_for: int) -> list[str]:
    log: list[str] = []
    n = {"busy": 0, "sending": 0}

    @activity.defn(name="privacy_mark")
    async def mark(inp: acts.PrivacyIn) -> None:
        log.append("mark")

    @activity.defn(name="privacy_stop")
    async def stop(inp: acts.PrivacyIn) -> acts.Counts:
        log.append("stop")
        return acts.Counts(counts={})

    @activity.defn(name="privacy_busy")
    async def busy(inp: acts.PrivacyIn) -> int:
        n["busy"] += 1
        return 1 if n["busy"] <= busy_for else 0

    @activity.defn(name="privacy_sending")
    async def sending(inp: acts.PrivacyIn) -> int:
        n["sending"] += 1
        left = 1 if n["sending"] <= sending_for else 0
        log.append(f"sending:{left}")
        return left

    @activity.defn(name="privacy_waiting")
    async def waiting(inp: acts.PrivacyIn) -> None:
        log.append("waiting")

    @activity.defn(name="privacy_redact")
    async def redact(inp: acts.PrivacyIn) -> acts.Counts:
        log.append("redact")
        return acts.Counts(counts={"people": 1})

    tq = f"tq-{uuid.uuid4()}"
    async with Worker(
        env.client,
        task_queue=tq,
        workflows=[PrivacyRequest],
        activities=[mark, stop, busy, sending, waiting, redact],
    ):
        out = await env.client.execute_workflow(
            PrivacyRequest.run,
            acts.PrivacyIn(request_id=uuid.uuid4(), email_hmac="h"),
            id=f"p-{uuid.uuid4()}",
            task_queue=tq,
        )
    assert out == {"people": 1}
    return log


async def test_nothing_in_flight_redacts_at_once(ts_env) -> None:
    assert await run(ts_env, busy_for=0, sending_for=0) == ["mark", "stop", "sending:0", "redact"]


async def test_a_send_still_in_flight_after_the_normal_wait_is_waited_for(ts_env) -> None:
    log = await run(ts_env, busy_for=100, sending_for=50)
    assert log[:2] == ["mark", "stop"] and log[-1] == "redact" and "waiting" in log
    assert log[-2] == "sending:0" and log.count("sending:1") == 50
