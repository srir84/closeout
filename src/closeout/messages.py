"""What a job's messages say right now (design 3.6). Used by the approval page and by perform."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Connection

from closeout import db
from closeout.core.emails import JOB_STEPS, Draft, JobMessageFacts, job_message


class Message(BaseModel):
    target: str
    round: int
    step: str
    content_rev: int
    recipient_id: UUID
    to_address: str
    subject: str
    body_template: str


def job_facts(conn: Connection, job_id: UUID) -> dict[str, Any]:
    return {
        r["field"]: r["value"]
        for r in db.all_rows(
            conn,
            """SELECT field, value FROM facts WHERE job_id = :j
                                                               AND status IN ('kept', 'answered')""",
            j=job_id,
        )
    }


def job_messages(conn: Connection, job_id: UUID, steps: tuple[str, ...] = JOB_STEPS) -> list[Message]:
    job = db.one(
        conn,
        """SELECT j.*, k.name AS contractor_name, pa.address AS parcel_address, p.email AS to_address
                          FROM jobs j JOIN contractors k ON k.id = j.contractor_id
                          LEFT JOIN parcels pa ON pa.pin = j.pin
                          JOIN recipients r ON r.id = j.homeowner_recipient_id JOIN people p ON p.id = r.person_id
                          WHERE j.id = :id""",
        id=job_id,
    )
    if job is None or not job["to_address"]:
        return []
    f = job_facts(conn, job_id)
    facts = JobMessageFacts(
        contractor_name=job["contractor_name"],
        work_type=job["work_type"] or "other",
        address=f.get("job_address") or job["address_raw"] or "",
        finish_date=date.fromisoformat(f["completion_date"])
        if f.get("completion_date")
        else job["csv_finish_date"],
        total=Decimal(f["total_amount"]) if f.get("total_amount") else job["csv_total"],
    )
    out = []
    for step in steps:
        d: Draft = job_message(step, facts)
        out.append(
            Message(
                target=f"job-{job_id}",
                round=job["round"],
                step=step,
                content_rev=job["content_rev"],
                recipient_id=job["homeowner_recipient_id"],
                to_address=job["to_address"],
                subject=d.subject,
                body_template=d.body_template,
            )
        )
    return out
