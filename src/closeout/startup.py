"""Things the worker does once when it starts."""

from __future__ import annotations

import logging

from temporalio.client import Client

log = logging.getLogger("closeout.startup")


async def on_worker_start(client: Client) -> None:
    """Create or update the permit-sync Schedule. Later slices add the check for jobs with no workflow."""
    from closeout import schedules

    log.info("worker starting; permit-sync schedule %s", await schedules.ensure(client))
