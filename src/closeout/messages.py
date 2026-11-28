"""What a job's messages say right now (design 3.6). Used by the approval page and by perform."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Connection

from closeout import db
from closeout.core.emails import (
    JOB_STEPS,
    TOWN_STEPS,
    Draft,
    JobMessageFacts,
    TownMessageFacts,
    job_message,
    town_message,
)


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


def town_messages(conn: Connection, request_id: UUID, steps: tuple[str, ...] = TOWN_STEPS) -> list[Message]:
    """The request and its reminder to the town's clerk. The clerk's address comes only from the town settings."""
    t = db.one(
        conn,
        """SELECT t.*, p.permit_no, p.issued_on, pa.address, tw.name AS town_name, tw.clerk_recipient_id,
                               pe.email AS clerk_email
                        FROM town_requests t JOIN permits p ON p.pk = t.permit_pk JOIN parcels pa ON pa.pin = t.home_pin
                        JOIN towns tw ON tw.code = pa.town JOIN recipients r ON r.id = tw.clerk_recipient_id
                        JOIN people pe ON pe.id = r.person_id WHERE t.id = :id""",
        id=request_id,
    )
    if t is None or not t["clerk_email"]:
        return []
    facts = TownMessageFacts(
        town_name=t["town_name"],
        permit_no=t["permit_no"] or t["permit_pk"],
        address=t["address"],
        issued_on=t["issued_on"],
    )
    return [
        Message(
            target=f"town-{request_id}",
            round=t["round"],
            step=s,
            content_rev=t["content_rev"],
            recipient_id=t["clerk_recipient_id"],
            to_address=t["clerk_email"],
            subject=(d := town_message(s, facts)).subject,
            body_template=d.body_template,
        )
        for s in steps
    ]
