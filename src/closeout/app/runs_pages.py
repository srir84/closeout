"""The runs view and the proof behind each mark (design 3.9, 3.12, R-OBS-2, R-REC-2).

/runs reads from the database (events, outbox, commands), not from Temporal, so it works for closed workflows
and with the worker down. It shows step names, times, waits and outcomes: never email text, addresses or prices.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from closeout import db
from closeout.app import deps
from closeout.app.deps import render
from closeout.core.record import marks

STEP_WORDS = {
    "job.imported": "Imported",
    "job.needs_a_look": "Needs a look",
    "job.fact_fixed": "Fact fixed",
    "job.ready": "Ready to send",
    "job.approved": "Contractor approved the emails",
    "job.contractor_confirmed": "Contractor confirmed the job",
    "job.message_delivered": "Email delivery result",
    "job.answered": "Homeowner answered",
    "job.homeowner_confirmed": "Homeowner confirmed",
    "job.corrected": "Homeowner sent a correction",
    "job.correction_accepted": "Contractor accepted the correction",
    "job.correction_rejected": "Contractor rejected the correction",
    "job.stopped": "Stopped",
    "job.no_answer": "No answer",
    "job.on_hold": "On hold for ops",
    "job.resent": "Resent",
    "job.undone": "Undone (new round)",
    "job.recorded": "Recorded",
    "job.retracted": "Retracted",
    "job.dropped": "Dropped",
    "job.price_visibility_set": "Price visibility chosen",
    "permit.matched": "Permit matched",
}
# Payload values safe to show: codes only. Dates, amounts and IDs are left out of the view.
SHOWN_CODES = ("reason", "answer", "step", "state", "via", "origin", "field")


def visible_jobs(acct: dict[str, Any]) -> list[dict[str, Any]]:
    """ops: all jobs. Contractor: own jobs. Homeowner: jobs on their own home (design 3.12)."""
    with db.tx() as c:
        if acct["kind"] == "ops":
            where, kw = "TRUE", {}
        elif acct["kind"] == "contractor":
            where, kw = "j.contractor_id = :k", {"k": acct["contractor_id"]}
        else:
            where, kw = "j.pin IN (SELECT pin FROM homes WHERE owner_account_id = :a)", {"a": acct["id"]}
        return db.all_rows(
            c,
            f"""SELECT j.id, j.job_ref, j.status, j.round, j.work_type, j.updated_at
                                  FROM jobs j WHERE {where} ORDER BY j.updated_at DESC LIMIT 200""",
            **kw,
        )


def can_see_job(acct: dict[str, Any], job_id: uuid.UUID) -> bool:
    return any(j["id"] == job_id for j in visible_jobs(acct))


def run_view(job_id: uuid.UUID) -> dict[str, Any]:
    from closeout.events import read_stream

    with db.tx() as c:
        job = db.one(
            c, "SELECT id, job_ref, status, round, work_type, closed_at FROM jobs WHERE id = :j", j=job_id
        )
        evs = read_stream(c, f"job-{job_id}")
        times = {
            r["event_id"]: r["created_at"]
            for r in db.all_rows(
                c,
                """SELECT event_id, created_at FROM events
                                                                         WHERE stream = :s""",
                s=f"job-{job_id}",
            )
        }
        sends = db.all_rows(
            c,
            """SELECT step, round, state, attempts, created_at, sent_at, last_error_code, cancel_reason
                                  FROM outbox WHERE job_id = :j ORDER BY created_at""",
            j=job_id,
        )
        cmds = db.all_rows(
            c,
            """SELECT kind, state, saved_at, result->>'code' AS code FROM commands
                                 WHERE target_workflow = :w ORDER BY saved_at""",
            w=f"job-{job_id}",
        )
        reads = db.all_rows(
            c,
            """SELECT m.reader, m.provider, m.model, m.ms, m.error FROM model_calls m
                                  JOIN documents d ON d.id = m.document_id WHERE d.job_id = :j ORDER BY m.created_at""",
            j=job_id,
        )
    steps = [
        {
            "seq": e.seq,
            "at": times[e.event_id],
            "round": e.round,
            "what": STEP_WORDS.get(e.kind, e.kind),
            "codes": {k: v for k, v in e.payload.items() if k in SHOWN_CODES and isinstance(v, str)},
        }
        for e in evs
    ]
    return {"job": job, "steps": steps, "sends": sends, "commands": cmds, "reads": reads}


def register(app: FastAPI) -> None:
    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request) -> Any:
        acct = deps.require_account(request)
        return render(request, "runs.html", jobs=visible_jobs(acct), sample=True)

    @app.get("/runs/{job_id}", response_class=HTMLResponse)
    def run(request: Request, job_id: uuid.UUID) -> Any:
        acct = deps.require_account(request)
        if not can_see_job(acct, job_id):
            raise HTTPException(status_code=404, detail="No such job")
        return render(request, "run.html", **run_view(job_id), sample=True)

    @app.get("/proof/{job_id}/{kind}", response_class=HTMLResponse)
    def proof(request: Request, job_id: uuid.UUID, kind: str) -> Any:
        """The record behind one mark (R-REC-2). Same access as the job itself."""
        from closeout.events import read_stream

        acct = deps.require_account(request)
        if not can_see_job(acct, job_id):
            raise HTTPException(status_code=404, detail="No such job")
        with db.tx() as c:
            evs = read_stream(c, f"job-{job_id}")
            m = next((x for x in marks(evs) if x.kind == kind), None)
            if m is None:
                raise HTTPException(status_code=404, detail="This job doesn't show that mark")
            detail: dict[str, Any] = {}
            if m.proof_kind == "permit":
                detail = (
                    db.one(
                        c,
                        """SELECT permit_no, permit_type, trades, issued_on, final_on, final_type
                                      FROM permits WHERE pk = :p""",
                        p=m.proof_id,
                    )
                    or {}
                )
            elif m.proof_kind == "approval":
                detail = (
                    db.one(
                        c,
                        """SELECT a.created_at AS approved_at, a.round, a.step, a.used_at,
                                      k.name AS contractor FROM approvals a JOIN accounts ac ON ac.id = a.account_id
                                      JOIN contractors k ON k.id = ac.contractor_id WHERE a.id = :a""",
                        a=uuid.UUID(m.proof_id),
                    )
                    or {}
                )
            else:
                detail = (
                    db.one(
                        c,
                        """SELECT kind, saved_at AS answered_at, refs->'codes'->>'round' AS round,
                                      result->>'code' AS outcome FROM commands WHERE command_id = :c""",
                        c=uuid.UUID(m.proof_id),
                    )
                    or {}
                )
        return render(request, "proof.html", job_id=job_id, mark=m, detail=detail, sample=True)
