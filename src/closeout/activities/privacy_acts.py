"""Activities for a privacy request (design 3.17). Only IDs and counts cross into the workflow."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel
from temporalio import activity

from closeout import db, privacy
from closeout.activities.safety import scrubbed
from closeout.events import append_next
from closeout.settings import get_settings


class PrivacyIn(BaseModel):
    request_id: UUID
    person_id: UUID


class Counts(BaseModel):
    counts: dict[str, int]


@activity.defn
@scrubbed
def privacy_mark(inp: PrivacyIn) -> None:
    with db.tx() as c:
        privacy.mark(c, inp.person_id)
        privacy.audit(c, inp.request_id, "marked", {})


@activity.defn
@scrubbed
def privacy_stop(inp: PrivacyIn) -> Counts:
    with db.tx() as c:
        n = privacy.stop(c, inp.person_id)
        privacy.audit(c, inp.request_id, "stopped", n)
    return Counts(counts=n)


@activity.defn
@scrubbed
def privacy_busy(inp: PrivacyIn) -> int:
    with db.tx() as c:
        return privacy.busy(c, inp.person_id)


@activity.defn
@scrubbed
def privacy_redact(inp: PrivacyIn) -> Counts:
    """Mailpit first (it needs the address), then every database store and the uploaded files."""
    with db.tx() as c:
        email = db.scalar(c, "SELECT email FROM people WHERE id = :p", p=inp.person_id)
    mailpit = privacy.mailpit_delete(get_settings().mailpit_api_url, email)
    with db.tx() as c:
        n = privacy.redact(c, inp.person_id)
        n["mailpit"] = mailpit
        privacy.audit(c, inp.request_id, "redacted", n)
        append_next(
            c,
            f"privacy-{inp.request_id}",
            "privacy.erased",
            {"request_id": str(inp.request_id)},
            f"privacy:{inp.request_id}:privacy.erased",
        )
        db.run(
            c,
            "UPDATE privacy_requests SET status = 'done', finished_at = now() WHERE id = :id",
            id=inp.request_id,
        )
    return Counts(counts=n)
