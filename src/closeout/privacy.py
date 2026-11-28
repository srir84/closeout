"""Removing a person's details everywhere they are stored (design 3.17, R-OPS-2).

Order: 1. erase marker + do-not-contact; 2. revoke links, cancel queued messages; 3. wait until nothing for them is
running; 4. redact every store. After step 1, any write that would store a personal value for an erased person
stores a blank instead, so a late model answer or a late click can't bring the data back.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from closeout import db, files
from closeout.privacy_hmac import email_hmac


def person_by_email(conn: Connection, email: str) -> UUID | None:
    return db.scalar(
        conn, "SELECT id FROM people WHERE email_hmac = :h ORDER BY created_at LIMIT 1", h=email_hmac(email)
    )


def their_jobs(conn: Connection, person_id: UUID) -> list[UUID]:
    return [
        r["id"]
        for r in db.all_rows(
            conn,
            """SELECT j.id FROM jobs j JOIN recipients r
                                                 ON r.id = j.homeowner_recipient_id WHERE r.person_id = :p""",
            p=person_id,
        )
    ]


def erased_job(conn: Connection, job_id: UUID | None) -> bool:
    """True when the job's homeowner has asked to be erased: late writes store blanks."""
    if job_id is None:
        return False
    return bool(
        db.scalar(
            conn,
            """SELECT p.erased_at IS NOT NULL FROM jobs j JOIN recipients r
                                   ON r.id = j.homeowner_recipient_id JOIN people p ON p.id = r.person_id
                                   WHERE j.id = :j""",
            j=job_id,
        )
    )


def mark(conn: Connection, person_id: UUID) -> None:
    """Step 1: the erase marker, and the address onto the do-not-contact list."""
    p = db.one(conn, "SELECT email FROM people WHERE id = :p FOR UPDATE", p=person_id)
    if p is None:
        return
    db.run(conn, "UPDATE people SET erased_at = coalesce(erased_at, now()) WHERE id = :p", p=person_id)
    if p["email"]:
        db.run(
            conn,
            """INSERT INTO suppression (email_hmac, reason) VALUES (:h, 'privacy_request')
                        ON CONFLICT (email_hmac) DO NOTHING""",
            h=email_hmac(p["email"]),
        )


def stop(conn: Connection, person_id: UUID) -> dict[str, int]:
    """Step 2: revoke their links and cancel their queued messages."""
    recips = [
        r["id"] for r in db.all_rows(conn, "SELECT id FROM recipients WHERE person_id = :p", p=person_id)
    ]
    links = db.run(
        conn,
        "UPDATE links SET revoked_at = coalesce(revoked_at, now()) WHERE recipient_id = ANY(:r)",
        r=recips,
    )
    cancelled = db.run(
        conn,
        """UPDATE outbox SET state = 'cancelled', cancel_reason = 'privacy_request',
                                updated_at = now() WHERE recipient_id = ANY(:r) AND state = 'pending'""",
        r=recips,
    )
    return {"links": links, "cancelled": cancelled}


def busy(conn: Connection, person_id: UUID) -> int:
    """Step 3: things still in flight for them (claimed sends, unprocessed clicks)."""
    jobs = their_jobs(conn, person_id)
    sending = db.scalar(
        conn,
        """SELECT count(*) FROM outbox o JOIN recipients r ON r.id = o.recipient_id
                                 WHERE r.person_id = :p AND o.state = 'sending'""",
        p=person_id,
    )
    saved = db.scalar(
        conn,
        """SELECT count(*) FROM commands WHERE state IN ('saved', 'accepted')
                               AND target_workflow = ANY(:w)""",
        w=[f"job-{j}" for j in jobs],
    )
    return int(sending or 0) + int(saved or 0)


def redact(conn: Connection, person_id: UUID) -> dict[str, int]:
    """Step 4: blank every store that held their details (the one redaction function)."""
    jobs = their_jobs(conn, person_id)
    recips = [
        r["id"] for r in db.all_rows(conn, "SELECT id FROM recipients WHERE person_id = :p", p=person_id)
    ]
    docs = db.all_rows(
        conn, "SELECT id, path FROM documents WHERE job_id = ANY(:j) AND removed_at IS NULL", j=jobs
    )
    n: dict[str, int] = {}
    n["people"] = db.run(
        conn,
        """UPDATE people SET display_name = NULL, email = NULL, phone = NULL
                                  WHERE id = :p""",
        p=person_id,
    )
    n["facts"] = db.run(
        conn,
        """UPDATE facts SET value = NULL, value_a = NULL, value_b = NULL, updated_at = now()
                                 WHERE job_id = ANY(:j) AND field IN ('customer_name', 'job_address')""",
        j=jobs,
    )
    n["jobs"] = db.run(conn, "UPDATE jobs SET address_raw = NULL WHERE id = ANY(:j)", j=jobs)
    n["reads"] = db.run(
        conn,
        """UPDATE reads SET output = NULL, redacted_at = now()
                                 WHERE document_id = ANY(:d)""",
        d=[d["id"] for d in docs] or [None],
    )
    n["model_calls"] = db.run(
        conn,
        """UPDATE model_calls SET response = NULL, redacted_at = now()
                                       WHERE document_id IN (SELECT id FROM documents WHERE job_id = ANY(:j))""",
        j=jobs,
    )
    n["commands"] = db.run(
        conn,
        """UPDATE commands SET payload = '{}'::jsonb, redacted_at = now()
                                    WHERE target_workflow = ANY(:w) OR link_id IN (SELECT id FROM links
                                    WHERE recipient_id = ANY(:r))""",
        w=[f"job-{j}" for j in jobs],
        r=recips,
    )
    n["approvals"] = db.run(
        conn,
        """UPDATE approvals SET to_address = NULL, subject = NULL, body_template = NULL,
                                     redacted_at = now() WHERE recipient_id = ANY(:r)""",
        r=recips,
    )
    n["outbox"] = db.run(
        conn,
        """UPDATE outbox SET to_address = NULL, subject = NULL, body_template = NULL,
                                  redacted_at = now() WHERE recipient_id = ANY(:r)""",
        r=recips,
    )
    for d in docs:
        p = files.path_of(d["path"])
        shared = db.scalar(
            conn,
            """SELECT count(*) FROM documents WHERE path = :p AND removed_at IS NULL
                                    AND id <> :id AND job_id <> ALL(:j)""",
            p=d["path"],
            id=d["id"],
            j=jobs,
        )
        if p.exists() and not shared:
            p.unlink()
    n["documents"] = db.run(
        conn,
        "UPDATE documents SET removed_at = now() WHERE id = ANY(:d)",
        d=[d["id"] for d in docs] or [None],
    )
    return n


def mailpit_delete(api_url: str, email: str | None) -> int:
    """Delivered emails are deleted from Mailpit through its API (search by address)."""
    if not email:
        return 0
    from closeout.mailpit import Mailpit

    mp = Mailpit(api_url)
    found = mp.search(f"to:{email}")
    if found:
        mp.delete([m["ID"] for m in found])
    return len(found)


def audit(conn: Connection, request_id: UUID, step: str, counts: dict[str, Any]) -> None:
    db.run(
        conn,
        "INSERT INTO audit_log (action, target, details) VALUES ('privacy', :t, CAST(:d AS jsonb))",
        t=f"privacy-{request_id}",
        d=json.dumps({"step": step, **counts}),
    )
