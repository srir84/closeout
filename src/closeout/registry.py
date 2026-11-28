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
    from closeout.workflows.privacy_request import PrivacyRequest
    from closeout.workflows.retract import Retract
    from closeout.workflows.town_request import TownRequest

    return [Ping, PermitSync, ImportBatch, Job, Retract, TownRequest, PrivacyRequest]


def activities(client: Client | None = None) -> list[Callable[..., Any]]:
    from closeout.activities import (
        answers,
        common,
        jobs,
        outbox,
        permit_match,
        permits,
        privacy_acts,
        reading,
        town,
    )

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
        permit_match.match_permit,
        outbox.perform_send,
        outbox.outbox_state,
        outbox.check_approvals,
        outbox.stop_sending,
        answers.apply_answer,
        answers.correction_rejected,
        answers.saved_undo,
        answers.start_round,
        answers.mark_closed,
        town.town_status,
        town.reply_document,
        town.decide_reply,
        town.stop_town_sending,
        town.town_hold,
        privacy_acts.privacy_mark,
        privacy_acts.privacy_stop,
        privacy_acts.privacy_busy,
        privacy_acts.privacy_redact,
    ]
    if client is not None:
        out.append(jobs.Starter(client).start_jobs)
    return out


def model_activities() -> list[Callable[..., Any]]:
    """Run on the per-reader queues `model-reads-A` and `model-reads-B`, one at a time each."""
    from closeout.activities import reading

    return [reading.read_document]
