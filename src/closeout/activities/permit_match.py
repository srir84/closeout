"""Permit match for one job (design 3.2, R-COR-1). The rules are in core/permits.py."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel
from temporalio import activity

from closeout import db
from closeout.activities.safety import scrubbed
from closeout.core.permits import Permit, match


class MatchIn(BaseModel):
    job_id: UUID


class MatchPermitOut(BaseModel):
    result: str
    pks: list[str]
    already_marked: bool


def finish_date(conn, job_id: UUID) -> date | None:  # type: ignore[no-untyped-def]
    f = db.one(
        conn, "SELECT value, status FROM facts WHERE job_id = :j AND field = 'completion_date'", j=job_id
    )
    if f and f["value"] and f["status"] in ("kept", "answered"):
        return date.fromisoformat(f["value"])
    return db.scalar(conn, "SELECT csv_finish_date FROM jobs WHERE id = :j", j=job_id)


def parcel_permits(conn, pin: str) -> list[Permit]:  # type: ignore[no-untyped-def]
    rows = db.all_rows(
        conn,
        """SELECT p.pk, p.permit_type, p.trades, p.issued_on, p.final_on, p.permit_no
                                FROM permit_parcel pp JOIN permits p ON p.pk = pp.permit_pk
                                WHERE pp.pin = :pin AND pp.status = 'linked'""",
        pin=pin,
    )
    return [Permit(**r) for r in rows]


@activity.defn
@scrubbed
def match_permit(inp: MatchIn) -> MatchPermitOut:
    with db.tx() as c:
        job = db.one(c, "SELECT * FROM jobs WHERE id = :id", id=inp.job_id)
        assert job is not None
        marked = bool(
            db.scalar(
                c,
                "SELECT count(*) FROM events WHERE stream = :s AND kind = 'permit.matched'",
                s=f"job-{inp.job_id}",
            )
        )
        if job["pin"] is None:
            m_result, m_pks = "none", []
        else:
            m = match(
                job["work_type"] or "other",
                finish_date(c, inp.job_id),
                parcel_permits(c, job["pin"]),
                job["stated_like_for_like"],
                job["stated_detached_1_2_family"],
            )
            m_result, m_pks = m.result, m.pks
        db.run(
            c,
            "UPDATE jobs SET permit_result = :r, permit_pks = :p WHERE id = :id",
            r=m_result,
            p=m_pks,
            id=inp.job_id,
        )
    return MatchPermitOut(result=m_result, pks=m_pks, already_marked=marked)
