"""Every workflow and activity the worker runs, in one place (also used by tests and the replayer)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from temporalio.client import Client


def workflows() -> list[type]:
    from closeout.workflows.import_batch import ImportBatch
    from closeout.workflows.job import Job
    from closeout.workflows.permit_sync import PermitSync
    from closeout.workflows.ping import Ping

    return [Ping, PermitSync, ImportBatch, Job]


def activities(client: Client | None = None) -> list[Callable[..., Any]]:
    from closeout.activities import common, jobs, permits, reading

    out: list[Callable[..., Any]] = [
        common.finish_command,
        permits.sync_permits,
        reading.decide_facts,
        jobs.record,
        jobs.prepare_job,
        jobs.match_job_address,
        jobs.check_job,
        jobs.apply_fix,
        jobs.prepare_batch,
        jobs.finish_batch,
    ]
    if client is not None:
        out.append(jobs.Starter(client).start_jobs)
    return out


def model_activities() -> list[Callable[..., Any]]:
    """Run on the per-reader queues `model-reads-A` and `model-reads-B`, one at a time each."""
    from closeout.activities import reading

    return [reading.read_document]
