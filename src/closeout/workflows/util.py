"""Small helpers for workflow code (deterministic, no I/O)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from closeout.core.clock import Clock
    from closeout.workflows.payloads import ClockIn

# Database steps, perform, starting workflows: unlimited retries, backing off to 1 minute (design 3.8).
DB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=0,
    non_retryable_error_types=["BadInput", "ApprovalRefused", "ReplayMiss"],
)
DB_TIMEOUT = timedelta(seconds=30)


def to_clock(c: ClockIn) -> Clock:
    return Clock(epoch_real=c.epoch_real, epoch_virtual=c.epoch_virtual, scale=c.scale)


def vnow(c: ClockIn) -> datetime:
    return to_clock(c).virtual(workflow.now())


async def wait_until(cond: Callable[[], bool], deadline: datetime | None) -> bool:
    """Wait until cond() is true (returns True) or the real deadline passes (returns False)."""
    if cond():
        return True
    if deadline is None:
        await workflow.wait_condition(cond)
        return True
    left = (deadline - workflow.now()).total_seconds()
    if left <= 0:
        return cond()
    try:
        await workflow.wait_condition(cond, timeout=timedelta(seconds=left))
        return True
    except TimeoutError:
        return cond()
