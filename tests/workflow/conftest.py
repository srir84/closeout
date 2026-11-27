from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import pytest_asyncio
from conftest import temporal_cli_path
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from closeout.temporal import DATA_CONVERTER


@pytest_asyncio.fixture(scope="session")
async def dev_env() -> AsyncIterator[WorkflowEnvironment]:
    """A real Temporal dev server (for updates, Schedules, restarts)."""
    kwargs = {}
    if temporal_cli_path():
        kwargs["dev_server_existing_path"] = temporal_cli_path()
    env = await WorkflowEnvironment.start_local(
        data_converter=DATA_CONVERTER, dev_server_extra_args=["--log-level", "error"], **kwargs
    )
    yield env
    await env.shutdown()


@pytest_asyncio.fixture(scope="module")
async def ts_env() -> AsyncIterator[WorkflowEnvironment]:
    """Temporal's time-skipping test server."""
    env = await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER)
    yield env
    await env.shutdown()


@contextlib.asynccontextmanager
async def running_worker(client: Client, workflows: list, activities: list, task_queue: str | None = None):
    tq = task_queue or f"tq-{uuid.uuid4()}"
    with ThreadPoolExecutor(max_workers=8) as pool:
        async with Worker(
            client, task_queue=tq, workflows=workflows, activities=activities, activity_executor=pool
        ):
            yield tq
