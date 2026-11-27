"""Event kinds and their payloads (design 3.9). Pure.

Payloads hold IDs, codes, dates and amounts only, never names, emails, addresses or typed text
(rule 7). Each kind has a model with a version `v`. tests/arch checks every model here.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    v: int = 1


class JobPayload(Payload):
    round: int = 1


class Imported(JobPayload):
    batch_id: UUID | None = None
    origin: Literal["contractor", "homeowner"] = "contractor"


class NeedsALook(JobPayload):
    reason: str  # a code, e.g. address_several, facts_asked, other_contractor, correction_rejected
    review_item_id: UUID | None = None


class FactFixed(JobPayload):
    fact_id: UUID
    field: str


class Ready(JobPayload):
    pin: str | None = None


class Approved(JobPayload):
    approval_ids: tuple[UUID, ...]
    command_id: UUID


class ContractorConfirmed(JobPayload):
    approval_id: UUID | None = None
    command_id: UUID | None = None
    via: Literal["approval", "link"] = "approval"


class MessageDelivered(JobPayload):
    outbox_id: UUID
    step: str
    state: Literal["sent", "failed", "unknown"]
    sent_at: datetime | None = None


class Answered(JobPayload):
    command_id: UUID
    answer: Literal["confirm", "correct", "decline", "not_mine"]
    link_id: UUID | None = None


class HomeownerConfirmed(JobPayload):
    command_id: UUID
    via: Literal["answer", "correction_accepted", "upload"] = "answer"


class Corrected(JobPayload):
    command_id: UUID
    finish_date: date | None = None
    total: Decimal | None = None
    fact_ids: tuple[UUID, ...] = ()


class CorrectionDecided(JobPayload):
    command_id: UUID


class Stopped(JobPayload):
    reason: Literal["declined", "not_mine", "ops_stop"]
    command_id: UUID | None = None


class NoAnswer(JobPayload):
    pass


class OnHold(JobPayload):
    reason: str  # stale_job, over_limit, grant_revoked, expired, failed, unknown, refused
    outbox_id: UUID | None = None
    review_item_id: UUID | None = None


class Resent(JobPayload):
    command_id: UUID


class Undone(JobPayload):
    command_id: UUID


class Recorded(JobPayload):
    pass


class Retracted(JobPayload):
    command_id: UUID | None = None


class Dropped(JobPayload):
    command_id: UUID | None = None


class PriceVisibilitySet(JobPayload):
    command_id: UUID
    price_visible: bool


class PermitIssued(Payload):
    pk: str
    issued_on: date | None = None


class PermitChanged(Payload):
    pk: str


class PermitFinalRecorded(Payload):
    pk: str
    final_on: date


class PermitMatched(JobPayload):
    pk: str
    picked_by_contractor: bool = False
    command_id: UUID | None = None


class TownReplyRecorded(Payload):
    town_request_id: UUID
    permit_pk: str
    result: Literal["final_on_file", "no_record", "unclear"]
    result_date: date | None = None


class PrivacyErased(Payload):
    request_id: UUID


def kinds() -> dict[str, type[Payload]]:
    return {
        "job.imported": Imported,
        "job.needs_a_look": NeedsALook,
        "job.fact_fixed": FactFixed,
        "job.ready": Ready,
        "job.approved": Approved,
        "job.contractor_confirmed": ContractorConfirmed,
        "job.message_delivered": MessageDelivered,
        "job.answered": Answered,
        "job.homeowner_confirmed": HomeownerConfirmed,
        "job.corrected": Corrected,
        "job.correction_accepted": CorrectionDecided,
        "job.correction_rejected": CorrectionDecided,
        "job.stopped": Stopped,
        "job.no_answer": NoAnswer,
        "job.on_hold": OnHold,
        "job.resent": Resent,
        "job.undone": Undone,
        "job.recorded": Recorded,
        "job.retracted": Retracted,
        "job.dropped": Dropped,
        "job.price_visibility_set": PriceVisibilitySet,
        "permit.issued": PermitIssued,
        "permit.changed": PermitChanged,
        "permit.final_recorded": PermitFinalRecorded,
        "permit.matched": PermitMatched,
        "town.reply_recorded": TownReplyRecorded,
        "privacy.erased": PrivacyErased,
    }


def parse(kind: str, payload: dict[str, object]) -> Payload:
    model = kinds().get(kind)
    if model is None:
        raise ValueError(f"unknown event kind {kind}")
    return model.model_validate(payload)


class Event(BaseModel):
    """An event as read back from the record."""

    model_config = ConfigDict(frozen=True)
    event_id: UUID
    stream: str
    seq: int
    kind: str
    payload: dict[str, object]

    @property
    def round(self) -> int:
        r = self.payload.get("round", 1)
        return r if isinstance(r, int) else 1
