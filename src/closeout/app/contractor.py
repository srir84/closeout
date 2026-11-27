"""Contractor pages: import, jobs, the job page and the review screen (design 3.12, 3.16)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from closeout import clockdb, commands, db, files
from closeout.activities.jobs import BatchIn
from closeout.app import deps
from closeout.app.deps import render
from closeout.core.clean import invoice_fields
from closeout.core.imports import messages as csv_messages
from closeout.settings import get_settings

FIELD_LABELS = {
    "contractor_name": "Contractor",
    "contractor_license": "License",
    "contractor_email": "Contractor email",
    "customer_name": "Homeowner",
    "job_address": "Job address",
    "work_type": "Type of work",
    "completion_date": "Date finished",
    "total_amount": "Total",
    "warranty_years": "Warranty (years)",
}
REASON_TEXT = {
    "facts_asked": "Some facts need checking: the two reads didn't agree, or the invoice and your file differ.",
    "address_unmatched": "We couldn't match the job address to exactly one property.",
    "other_contractor": "The invoice looks like it's from a different contractor.",
    "light_review": "Light mode: please check every fact the model read.",
    "read_failed": "The invoice couldn't be read. Please type the facts.",
}


def own_job(acct: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any]:
    with db.tx() as c:
        job = db.one(
            c,
            """SELECT j.*, pa.address AS parcel_address, k.name AS contractor_name
                           FROM jobs j LEFT JOIN parcels pa ON pa.pin = j.pin
                           LEFT JOIN contractors k ON k.id = j.contractor_id WHERE j.id = :id""",
            id=job_id,
        )
    if job is None or job["contractor_id"] != acct["contractor_id"]:
        raise HTTPException(status_code=404, detail="No such job")
    return job


def job_facts(job_id: uuid.UUID) -> list[dict[str, Any]]:
    with db.tx() as c:
        rows = {r["field"]: r for r in db.all_rows(c, "SELECT * FROM facts WHERE job_id = :j", j=job_id)}
    return [
        {"field": f, "label": FIELD_LABELS[f], **rows.get(f, {"status": "missing", "value": None})}
        for f in invoice_fields()
    ]


def register(app: FastAPI) -> None:
    deps.HOMES["contractor"] = "/c/jobs"

    @app.get("/c/jobs", response_class=HTMLResponse)
    def jobs_list(request: Request) -> Any:
        acct = deps.require_account(request, "contractor")
        with db.tx() as c:
            jobs = db.all_rows(
                c,
                """SELECT j.id, j.job_ref, j.status, j.work_type, j.address_raw, j.created_at,
                                            j.needs_reason, j.permit_result
                                     FROM jobs j WHERE j.contractor_id = :k AND j.status <> 'retracted'
                                     ORDER BY j.created_at DESC, j.job_ref""",
                k=acct["contractor_id"],
            )
            batches = db.all_rows(
                c,
                """SELECT id, status, counts, created_at FROM import_batches
                                        WHERE account_id = :a ORDER BY created_at DESC LIMIT 5""",
                a=acct["id"],
            )
        return render(request, "c_jobs.html", jobs=jobs, batches=batches, sample=True)

    @app.get("/c/import", response_class=HTMLResponse)
    def import_form(request: Request, error: str = "") -> Any:
        deps.require_account(request, "contractor")
        return render(request, "c_import.html", error=error, mode=get_settings().closeout_mode, sample=True)

    @app.post("/c/import")
    async def import_upload(
        request: Request,
        csv_file: UploadFile = File(...),
        invoices: list[UploadFile] = File(default=[]),
        csrf: str = Form(""),
    ) -> Any:
        acct = deps.require_account(request, "contractor")
        deps.check_csrf(request, csrf)
        csv_data = await csv_file.read(files.MAX_BYTES + 1)
        if not csv_data:
            return RedirectResponse("/c/import?error=Choose+a+CSV+file.", status_code=303)
        if len(csv_data) > files.MAX_BYTES:
            return RedirectResponse("/c/import?error=The+CSV+file+is+over+10+MB.", status_code=303)
        if len(invoices) > 50:
            return RedirectResponse(
                "/c/import?error=A+batch+can+have+at+most+50+invoice+files.", status_code=303
            )
        batch_id = uuid.uuid4()
        file_map: dict[str, Any] = {}
        for up in invoices:
            data = await up.read(files.MAX_BYTES + 1)
            name = (up.filename or "").split("/")[-1][:120]
            chk = files.check(data)
            if chk.ok and chk.mime:
                file_map[name] = {
                    "ok": True,
                    "path": files.store(data, chk.mime),
                    "mime": chk.mime,
                    "pages": chk.pages,
                    "sha256": chk.sha256,
                }
            else:
                file_map[name] = {"ok": False, "reason": chk.reason}
        files.store_raw(csv_data, f"batches/{batch_id}/import.csv")
        with db.tx() as c:
            db.run(
                c,
                """INSERT INTO import_batches (id, account_id, files) VALUES (:id, :a, CAST(:f AS jsonb))""",
                id=batch_id,
                a=acct["id"],
                f=__import__("json").dumps(file_map),
            )
            clk = clockdb.read(c)
        client = await deps.temporal()
        await client.start_workflow(
            "ImportBatch",
            BatchIn(batch_id=batch_id, clock=clockdb.clock_in(clk)),
            id=f"import-{batch_id}",
            task_queue=get_settings().task_queue,
        )
        return RedirectResponse(f"/c/batches/{batch_id}", status_code=303)

    @app.get("/c/batches/{batch_id}", response_class=HTMLResponse)
    def batch_page(request: Request, batch_id: uuid.UUID) -> Any:
        acct = deps.require_account(request, "contractor")
        with db.tx() as c:
            b = db.one(
                c,
                "SELECT * FROM import_batches WHERE id = :id AND account_id = :a",
                id=batch_id,
                a=acct["id"],
            )
            if b is None:
                raise HTTPException(status_code=404, detail="No such import")
            jobs = db.all_rows(
                c,
                """SELECT id, job_ref, status, needs_reason FROM jobs WHERE batch_id = :b
                                     ORDER BY job_ref""",
                b=batch_id,
            )
        bad_files = {
            k: files.MESSAGES.get(v.get("reason", ""), "Refused")
            for k, v in b["files"].items()
            if not v.get("ok")
        }
        return render(
            request,
            "c_batch.html",
            batch=b,
            jobs=jobs,
            bad_files=bad_files,
            csv_messages=csv_messages(),
            reasons=REASON_TEXT,
            sample=True,
        )

    @app.get("/c/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: uuid.UUID, msg: str = "") -> Any:
        acct = deps.require_account(request, "contractor")
        job = own_job(acct, job_id)
        return render(
            request,
            "c_job.html",
            job=job,
            facts=job_facts(job_id),
            reasons=REASON_TEXT,
            msg=msg,
            sample=True,
            command_id=uuid.uuid4(),
        )

    @app.get("/c/review/{job_id}", response_class=HTMLResponse)
    def review_page(request: Request, job_id: uuid.UUID, msg: str = "") -> Any:
        acct = deps.require_account(request, "contractor")
        job = own_job(acct, job_id)
        return render(
            request,
            "c_review.html",
            job=job,
            facts=job_facts(job_id),
            reasons=REASON_TEXT,
            msg=msg,
            sample=True,
            command_ids={f: uuid.uuid4() for f in [*invoice_fields(), "approve", "drop"]},
        )

    async def send(
        acct: dict[str, Any],
        job_id: uuid.UUID,
        kind: str,
        command_id: uuid.UUID,
        payload: dict[str, Any] | None = None,
        codes: dict[str, str] | None = None,
    ) -> commands.Outcome:
        ref = deps.save_command(
            target_workflow=f"job-{job_id}",
            kind=kind,
            account_id=acct["id"],
            command_id=command_id,
            payload=payload,
            codes=codes,
        )
        return await commands.send(await deps.temporal(), f"job-{job_id}", ref)

    def outcome_msg(out: commands.Outcome) -> str:
        if out.status == "done":
            return "Done"
        if out.status == "rejected":
            return {
                "bad_value": "That value isn't valid. Please check it.",
                "workflow_closed": "This job is closed.",
            }.get(out.code, f"Not allowed now ({out.code})")
        return "Saved — we'll process it shortly"

    @app.post("/c/review/{job_id}/fix")
    async def fix_fact(
        request: Request,
        job_id: uuid.UUID,
        field: str = Form(...),
        value: str = Form(""),
        command_id: uuid.UUID = Form(...),
        csrf: str = Form(""),
    ) -> Any:
        acct = deps.require_account(request, "contractor")
        deps.check_csrf(request, csrf)
        own_job(acct, job_id)
        if field not in invoice_fields():
            raise HTTPException(status_code=400, detail="Unknown field")
        out = await send(
            acct,
            job_id,
            "fix_fact",
            command_id,
            payload={"field": field, "value": value[:200]},
            codes={"field": field},
        )
        return RedirectResponse(f"/c/review/{job_id}?msg={outcome_msg(out)}", status_code=303)

    @app.post("/c/review/{job_id}/approve")
    async def approve_facts(
        request: Request, job_id: uuid.UUID, command_id: uuid.UUID = Form(...), csrf: str = Form("")
    ) -> Any:
        acct = deps.require_account(request, "contractor")
        deps.check_csrf(request, csrf)
        own_job(acct, job_id)
        out = await send(acct, job_id, "approve_facts", command_id)
        return RedirectResponse(f"/c/jobs/{job_id}?msg={outcome_msg(out)}", status_code=303)

    @app.post("/c/review/{job_id}/drop")
    async def drop_job(
        request: Request, job_id: uuid.UUID, command_id: uuid.UUID = Form(...), csrf: str = Form("")
    ) -> Any:
        acct = deps.require_account(request, "contractor")
        deps.check_csrf(request, csrf)
        own_job(acct, job_id)
        out = await send(acct, job_id, "drop", command_id)
        return RedirectResponse(f"/c/jobs/{job_id}?msg={outcome_msg(out)}", status_code=303)
