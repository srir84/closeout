"""Every workflow and activity the worker runs, in one place (also used by tests and the replayer)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def workflows() -> list[type]:
    from closeout.workflows.ping import Ping

    return [Ping]


def activities() -> list[Callable[..., Any]]:
    from closeout.activities import common

    return [common.finish_command]


def model_activities() -> list[Callable[..., Any]]:
    return []
