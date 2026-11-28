"""Temporal fixtures live in the root conftest.py."""

from collections.abc import AsyncIterator  # noqa: E402

import pytest_asyncio  # noqa: E402
from conftest import running_worker  # noqa: F401
from temporalio.testing import WorkflowEnvironment  # noqa: E402

from closeout.temporal import DATA_CONVERTER  # noqa: E402


@pytest_asyncio.fixture
async def ts_env() -> AsyncIterator[WorkflowEnvironment]:
    """A fresh time-skipping server per test: workflows left by one test must not hold time still for the next."""
    env = await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER)
    yield env
    await env.shutdown()
