"""Things the worker does once when it starts."""

from __future__ import annotations

import logging

from temporalio.client import Client

log = logging.getLogger("closeout.startup")


async def on_worker_start(client: Client) -> None:
    """Later slices add: the permit-sync Schedule and the check for jobs with no workflow."""
    log.info("worker starting")
