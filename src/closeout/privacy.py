"""Removing a person's details everywhere they are stored (design 3.17, R-OPS-2).

Order: 1. erase marker + do-not-contact; 2. revoke links, cancel queued messages; 3. wait until nothing for them is
running (sends in flight always finish or turn unknown); 4. redact every store. A person is found by the HMAC of
their address, so every identity row with that address is covered, including ones made after the request.

After step 1, every writer of a personal value checks `erased_job(..., lock=True)` in the same transaction as its
write. The check takes a share lock on the person's row and the marker takes an update lock on it, so a write either
finishes before the marker (and step 4 removes it) or sees the marker (and stores a blank). Nothing can slip between.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from closeout import db, files
from closeout.privacy_hmac import email_hmac

PERSONAL_FIELDS = ("customer_name", "job_address")


def people_by_hmac(conn: Connection, h: str) -> list[UUID]:
    """Every identity row with this address (imports make one per job)."""
    return [
        r["id"]
        for r in db.all_rows(conn, "SELECT id FROM people WHERE email_hmac = :h ORDER BY created_at", h=h)
    ]


def person_by_email(conn: Connection, email: str) -> UUID | None:
    ids = people_by_hmac(conn, email_hmac(email))
    return ids[0] if ids else None


def their_jobs(conn: Connection, person_ids: list[UUID]) -> list[UUID]:
    """Jobs about their home: they are the homeowner on it (contractor-started or homeowner-started)."""
    return [
        r["id"]
        for r in db.all_rows(
            conn,
            """SELECT j.id FROM jobs j JOIN recipients r ON r.id = j.homeowner_recipient_id
               WHERE r.person_id = ANY(:p)""",
            p=person_ids,
        )
    ]


def erased_job(conn: Connection, job_id: UUID | None, lock: bool = False) -> bool:
    """True when the job's homeowner has asked to be erased: writers store blanks.

    With lock=True the person's row is share-locked until the caller's transaction ends (see the module note).
    """
    if job_id is None:
        return False
    return bool(
        db.scalar(
            conn,
            """SELECT p.erased_at IS NOT NULL FROM jobs j JOIN recipients r ON r.id = j.homeowner_recipient_id
               JOIN people p ON p.id = r.person_id WHERE j.id = :j"""
            + (" FOR SHARE OF p" if lock else ""),
            j=job_id,
        )
    )


def erased_address(conn: Connection, email: str) -> bool:
    """An import must not bring an erased person back: their address is on the do-not-contact list for privacy."""
    return bool(
        db.scalar(
            conn,
            "SELECT 1 FROM suppression WHERE email_hmac = :h AND reason = 'privacy_request'",
            h=email_hmac(email),
        )
    )


def mark(conn: Connection, person_ids: list[UUID], h: str) -> None:
    """Step 1: the erase marker on every identity, and the address onto the do-not-contact list."""
    db.run(
        conn,
        "SELECT id FROM people WHERE id = ANY(:p) ORDER BY id FOR UPDATE",
        p=person_ids,
    )
    db.run(conn, "UPDATE people SET erased_at = coalesce(erased_at, now()) WHERE id = ANY(:p)", p=person_ids)
    db.run(
        conn,
        """INSERT INTO suppression (email_hmac, reason) VALUES (:h, 'privacy_request')
           ON CONFLICT (email_hmac) DO UPDATE SET reason = 'privacy_request'""",
        h=h,
    )


def _recipients(conn: Connection, person_ids: list[UUID]) -> list[UUID]:
    return [
        r["id"]
        for r in db.all_rows(conn, "SELECT id FROM recipients WHERE person_id = ANY(:p)", p=person_ids)
    ]


def stop(conn: Connection, person_ids: list[UUID]) -> dict[str, int]:
    """Step 2: revoke their links and cancel their queued messages."""
    recips = _recipients(conn, person_ids)
    links = db.run(
        conn,
        "UPDATE links SET revoked_at = coalesce(revoked_at, now()) WHERE recipient_id = ANY(:r)",
        r=recips,
    )
    cancelled = db.run(
        conn,
        """UPDATE outbox SET state = 'cancelled', cancel_reason = 'privacy_request', updated_at = now()
           WHERE recipient_id = ANY(:r) AND state = 'pending'""",
        r=recips,
    )
    return {"links": links, "cancelled": cancelled}


def sending(conn: Connection, person_ids: list[UUID]) -> int:
    """Messages to them a sender has claimed. These always end (sent, failed or, via the reaper, unknown)."""
    return int(
        db.scalar(
            conn,
            """SELECT count(*) FROM outbox o JOIN recipients r ON r.id = o.recipient_id
               WHERE r.person_id = ANY(:p) AND o.state = 'sending'""",
            p=person_ids,
        )
        or 0
    )


def busy(conn: Connection, person_ids: list[UUID]) -> int:
    """Step 3: things still in flight for them: claimed sends and clicks not yet processed."""
    jobs = their_jobs(conn, person_ids)
    saved = db.scalar(
        conn,
        "SELECT count(*) FROM commands WHERE state IN ('saved', 'accepted') AND target_workflow = ANY(:w)",
        w=[f"job-{j}" for j in jobs],
    )
    return sending(conn, person_ids) + int(saved or 0)


def _remove_files(conn: Connection, docs: list[dict[str, Any]]) -> None:
    """Delete each stored file unless a document that stays still uses it (files are stored by content hash)."""
    ids = [d["id"] for d in docs]
    for d in docs:
        still_used = db.scalar(
            conn,
            "SELECT count(*) FROM documents WHERE path = :p AND removed_at IS NULL AND id <> ALL(:ids)",
            p=d["path"],
            ids=ids,
        )
        p = files.path_of(d["path"])
        if p.exists() and not still_used:
            p.unlink()


def redact(conn: Connection, person_ids: list[UUID]) -> dict[str, int]:
    """Step 4: blank every store that held their details (the one redaction function)."""
    emails = [
        r["email"]
        for r in db.all_rows(
            conn, "SELECT email FROM people WHERE id = ANY(:p) AND email IS NOT NULL", p=person_ids
        )
    ]
    jobs = their_jobs(conn, person_ids)
    recips = _recipients(conn, person_ids)
    accounts = [
        r["id"] for r in db.all_rows(conn, "SELECT id FROM accounts WHERE person_id = ANY(:p)", p=person_ids)
    ]
    docs = db.all_rows(
        conn,
        """SELECT id, path FROM documents WHERE removed_at IS NULL AND (job_id = ANY(:j)
           OR town_request_id IN (SELECT id FROM town_requests WHERE account_id = ANY(:a)))""",
        j=jobs,
        a=accounts,
    )
    doc_ids = [d["id"] for d in docs]
    n: dict[str, int] = {}
    n["people"] = db.run(
        conn,
        "UPDATE people SET display_name = NULL, email = NULL, phone = NULL WHERE id = ANY(:p)",
        p=person_ids,
    )
    n["facts"] = db.run(
        conn,
        """UPDATE facts SET value = NULL, value_a = NULL, value_b = NULL, updated_at = now()
           WHERE job_id = ANY(:j) AND field = ANY(:f)""",
        j=jobs,
        f=list(PERSONAL_FIELDS),
    )
    # A contractor's own address, where an invoice printed it on someone else's job.
    n["contractor_email_facts"] = db.run(
        conn,
        """UPDATE facts SET value = NULL, value_a = NULL, value_b = NULL, updated_at = now()
           WHERE field = 'contractor_email' AND (lower(value) = ANY(:e) OR lower(value_a) = ANY(:e)
                                                 OR lower(value_b) = ANY(:e))""",
        e=[e.lower() for e in emails],
    )
    n["jobs"] = db.run(conn, "UPDATE jobs SET address_raw = NULL WHERE id = ANY(:j)", j=jobs)
    n["reads"] = db.run(
        conn,
        "UPDATE reads SET output = NULL, errors = '{}', redacted_at = now() WHERE document_id = ANY(:d)",
        d=doc_ids,
    )
    n["model_calls"] = db.run(
        conn,
        "UPDATE model_calls SET response = NULL, error = NULL, redacted_at = now() WHERE document_id = ANY(:d)",
        d=doc_ids,
    )
    n["commands"] = db.run(
        conn,
        """UPDATE commands SET payload = '{}'::jsonb, redacted_at = now()
           WHERE target_workflow = ANY(:w) OR link_id IN (SELECT id FROM links WHERE recipient_id = ANY(:r))""",
        w=[f"job-{j}" for j in jobs],
        r=recips,
    )
    n["approvals"] = db.run(
        conn,
        """UPDATE approvals SET to_address = NULL, subject = NULL, body_template = NULL, redacted_at = now()
           WHERE recipient_id = ANY(:r) OR target = ANY(:w)""",
        r=recips,
        w=[f"job-{j}" for j in jobs],
    )
    n["outbox"] = db.run(
        conn,
        """UPDATE outbox SET to_address = NULL, subject = NULL, body_template = NULL, redacted_at = now()
           WHERE recipient_id = ANY(:r) OR job_id = ANY(:j)""",
        r=recips,
        j=jobs,
    )
    n["documents"] = db.run(conn, "UPDATE documents SET removed_at = now() WHERE id = ANY(:d)", d=doc_ids)
    _remove_files(conn, docs)
    return n


def mailpit_delete(api_url: str, emails: list[str]) -> int:
    """Delivered emails are deleted from Mailpit by search, repeated until none is left (no 500-message cap)."""
    from closeout.mailpit import Mailpit

    mp = Mailpit(api_url)
    gone = 0
    for e in emails:
        for _ in range(100):
            found = mp.search(f"to:{e}")
            if not found:
                break
            gone += len(found)
            mp.delete_search(f"to:{e}")
    return gone


def audit(conn: Connection, request_id: UUID, step: str, counts: dict[str, Any]) -> None:
    db.run(
        conn,
        "INSERT INTO audit_log (action, target, details) VALUES ('privacy', :t, CAST(:d AS jsonb))",
        t=f"privacy-{request_id}",
        d=json.dumps({"step": step, **counts}),
    )
