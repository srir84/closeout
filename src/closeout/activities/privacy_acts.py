"""Activities for a privacy request (design 3.17). Only IDs, an HMAC and counts cross into the workflow."""

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
    email_hmac: str


class Counts(BaseModel):
    counts: dict[str, int]


def _people(c, inp: PrivacyIn) -> list[UUID]:  # type: ignore[no-untyped-def]
    return privacy.people_by_hmac(c, inp.email_hmac)  # found again each step: new identities are included


@activity.defn
@scrubbed
def privacy_mark(inp: PrivacyIn) -> None:
    with db.tx() as c:
        privacy.mark(c, _people(c, inp), inp.email_hmac)
        privacy.audit(c, inp.request_id, "marked", {})


@activity.defn
@scrubbed
def privacy_stop(inp: PrivacyIn) -> Counts:
    with db.tx() as c:
        n = privacy.stop(c, _people(c, inp))
        privacy.audit(c, inp.request_id, "stopped", n)
    return Counts(counts=n)


@activity.defn
@scrubbed
def privacy_busy(inp: PrivacyIn) -> int:
    with db.tx() as c:
        return privacy.busy(c, _people(c, inp))


@activity.defn
@scrubbed
def privacy_sending(inp: PrivacyIn) -> int:
    with db.tx() as c:
        return privacy.sending(c, _people(c, inp))


@activity.defn
@scrubbed
def privacy_redact(inp: PrivacyIn) -> Counts:
    """Mailpit first (it needs the addresses), then every database store and the uploaded files."""
    with db.tx() as c:
        people = _people(c, inp)
        emails = [
            r["email"]
            for r in db.all_rows(
                c, "SELECT email FROM people WHERE id = ANY(:p) AND email IS NOT NULL", p=people
            )
        ]
    mailpit = privacy.mailpit_delete(get_settings().mailpit_api_url, sorted(set(emails)))
    with db.tx() as c:
        n = privacy.redact(c, _people(c, inp))
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


@activity.defn
@scrubbed
def privacy_waiting(inp: PrivacyIn) -> None:
    """A send to them is still in flight after the normal wait: ops can see the request is waiting for it."""
    with db.tx() as c:
        db.run(
            c, "UPDATE privacy_requests SET status = 'waiting_for_sender' WHERE id = :id", id=inp.request_id
        )
