"""Every workflow and activity the worker runs, in one place (also used by tests and the replayer)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def workflows() -> list[type]:
    from closeout.workflows.permit_sync import PermitSync
    from closeout.workflows.ping import Ping

    return [Ping, PermitSync]


def activities() -> list[Callable[..., Any]]:
    from closeout.activities import common, permits, reading

    return [common.finish_command, permits.sync_permits, reading.decide_facts]


def model_activities() -> list[Callable[..., Any]]:
    """Run on the per-reader queues `model-reads-A` and `model-reads-B`, one at a time each."""
    from closeout.activities import reading

    return [reading.read_document]
