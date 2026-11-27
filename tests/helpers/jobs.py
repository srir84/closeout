"""Make a job ready for approval directly in the database (for perform and sender tests)."""

from __future__ import annotations

import uuid
from typing import Any

from closeout import db
from closeout.act import new_approval
from closeout.messages import job_messages
from closeout.privacy_hmac import email_hmac
from closeout.seed import sid

FACTS = {
    "contractor_name": "Harbor & Pine Builders",
    "contractor_license": "13VH01234567",
    "contractor_email": "office@harborpine.example",
    "customer_name": "Test Owner",
    "job_address": "14 Osprey Ln",
    "work_type": "kitchen",
    "completion_date": "2025-03-28",
    "total_amount": "48250.00",
    "warranty_years": "5",
}


def make_job(
    email: str = "owner@sampleton.example",
    status: str = "asking_homeowner",
    contractor: str = "harbor",
    ref: str | None = None,
) -> uuid.UUID:
    with db.tx() as c:
        pid = db.scalar(
            c,
            "INSERT INTO people (display_name, email, email_hmac) VALUES ('Test Owner', :e, :h) RETURNING id",
            e=email,
            h=email_hmac(email),
        )
        rid = db.scalar(
            c, "INSERT INTO recipients (person_id, source) VALUES (:p, 'owner_entered') RETURNING id", p=pid
        )
        jid = db.scalar(
            c,
            """INSERT INTO jobs (contractor_id, job_ref, homeowner_recipient_id, address_raw, work_type,
                              status, town) VALUES (:k, :ref, :r, '14 Osprey Ln', 'kitchen', :st, '9999') RETURNING id""",
            k=sid("contractor:" + contractor),
            ref=ref or uuid.uuid4().hex[:8],
            r=rid,
            st=status,
        )
        for f, v in FACTS.items():
            db.run(
                c,
                "INSERT INTO facts (job_id, field, value, source, status) VALUES (:j, :f, :v, 'both_reads', 'kept')",
                j=jid,
                f=f,
                v=v,
            )
    return jid


def approve(
    job_id: uuid.UUID,
    account: str = "mike",
    steps: tuple[str, ...] = ("first", "reminder_1", "reminder_2"),
    days: int = 30,
) -> dict[str, uuid.UUID]:
    with db.tx() as c:
        acct = sid("account:" + account)
        grant = db.one(c, "SELECT * FROM grants WHERE account_id = :a", a=acct)
        assert grant is not None
        return {m.step: new_approval(c, acct, m, grant, days=days) for m in job_messages(c, job_id, steps)}


def row(sql: str, **kw: Any) -> dict[str, Any] | None:
    with db.tx() as c:
        return db.one(c, sql, **kw)
