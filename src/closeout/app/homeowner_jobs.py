"""S9: a homeowner uploads an invoice and asks the contractor to confirm it (design 3.20, R-HOME-3).

The contractor's address comes only from our contractor list (recipients source 'registry'), never from the
invoice. The homeowner approves the two messages (the request and one reminder) like a contractor does.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from closeout import clockdb, commands, db, files
from closeout.act import new_approval
from closeout.app import deps
from closeout.app.deps import render
from closeout.app.homeowner import own_home
from closeout.app.outreach_pages import preview_hash, shown_slots
from closeout.messages import job_messages
from closeout.settings import get_settings

WORK_TYPES = (
    "roof",
    "water_heater",
    "hvac",
    "electrical",
    "plumbing",
    "windows",
    "kitchen",
    "bathroom",
    "other",
)


def own_upload(acct: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any]:
    """A homeowner-started job on a home this account owns, or 404."""
    with db.tx() as c:
        job = db.one(
            c,
            """SELECT j.*, k.name AS contractor_name FROM jobs j JOIN contractors k ON k.id = j.contractor_id
                           JOIN homes h ON h.pin = j.pin WHERE j.id = :j AND j.origin = 'homeowner'
                           AND h.owner_account_id = :a""",
            j=job_id,
            a=acct["id"],
        )
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    return job


def register(app: FastAPI) -> None:
    @app.get("/h/upload", response_class=HTMLResponse)
    def upload_page(request: Request, msg: str = "") -> Any:
        acct = deps.require_account(request, "homeowner")
        h = own_home(acct)
        with db.tx() as c:
            ks = db.all_rows(
                c, "SELECT id, name, license FROM contractors WHERE recipient_id IS NOT NULL ORDER BY name"
            )
            mine = db.all_rows(
                c,
                """SELECT j.id, j.work_type, j.status, k.name FROM jobs j
                                     JOIN contractors k ON k.id = j.contractor_id
                                     WHERE j.pin = :p AND j.origin = 'homeowner' ORDER BY j.created_at DESC""",
                p=h["pin"],
            )
        return render(
            request,
            "h_upload.html",
            home=h,
            contractors=ks,
            work_types=WORK_TYPES,
            mine=mine,
            msg=msg,
            sample=True,
        )

    @app.post("/h/upload")
    async def upload(
        request: Request,
        contractor_id: uuid.UUID = Form(...),
        work_type: str = Form(...),
        finish_date: str = Form(""),
        total: str = Form(""),
        invoice: UploadFile = File(...),
        csrf: str = Form(""),
    ) -> Any:
        acct = deps.require_account(request, "homeowner")
        deps.check_csrf(request, csrf)
        h = own_home(acct)
        if work_type not in WORK_TYPES:
            raise HTTPException(status_code=400, detail="Unknown kind of work")
        try:
            fd = date.fromisoformat(finish_date) if finish_date else None
            tot = Decimal(total.replace(",", "").replace("$", "")) if total else None
        except (ValueError, InvalidOperation):
            return RedirectResponse(
                "/h/upload?msg=Please check the date (YYYY-MM-DD) and the total.", status_code=303
            )
        if tot is not None and not (Decimal(0) < tot < Decimal(10_000_000)):
            return RedirectResponse("/h/upload?msg=Please check the total.", status_code=303)
        data = await invoice.read(files.MAX_BYTES + 1)
        chk = files.check(data)
        if not chk.ok or chk.mime is None:
            return RedirectResponse(f"/h/upload?msg=We can't use that file ({chk.reason}).", status_code=303)
        with db.tx() as c:
            k = db.one(
                c, "SELECT id FROM contractors WHERE id = :k AND recipient_id IS NOT NULL", k=contractor_id
            )
            if k is None:
                raise HTTPException(status_code=400, detail="Pick a contractor from the list")
            person = db.scalar(c, "SELECT person_id FROM accounts WHERE id = :a", a=acct["id"])
            rid = db.scalar(
                c,
                """INSERT INTO recipients (person_id, source, entered_by)
                                  VALUES (:p, 'owner_entered', :a) RETURNING id""",
                p=person,
                a=acct["id"],
            )
            jid = db.scalar(
                c,
                """INSERT INTO jobs (origin, contractor_id, job_ref, homeowner_recipient_id, address_raw,
                                  work_type, csv_finish_date, csv_total, town, pin)
                                  VALUES ('homeowner', :k, :ref, :r, :addr, :wt, :fd, :tot, :town, :pin) RETURNING id""",
                k=contractor_id,
                ref=f"H-{uuid.uuid4().hex[:8].upper()}",  # unique per contractor, whichever home it's for
                r=rid,
                addr=h["address"],
                wt=work_type,
                fd=fd,
                tot=tot,
                town=h["town"],
                pin=h["pin"],
            )
            db.run(
                c,
                """INSERT INTO documents (job_id, kind, sha256, path, mime, pages)
                         VALUES (:j, 'invoice', :s, :p, :m, :n)""",
                j=jid,
                s=chk.sha256,
                p=files.store(data, chk.mime),
                m=chk.mime,
                n=chk.pages,
            )
            clk = clockdb.clock_in(clockdb.read(c))
        from closeout.workflows.job import JobIn

        client = await deps.temporal()
        await client.start_workflow(
            "Job",
            JobIn(job_id=jid, clock=clk, light=get_settings().closeout_mode == "light"),
            id=f"job-{jid}",
            task_queue=get_settings().task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        return RedirectResponse(f"/h/jobs/{jid}?msg=Uploaded. We're reading the invoice.", status_code=303)

    @app.get("/h/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: uuid.UUID, msg: str = "") -> Any:
        acct = deps.require_account(request, "homeowner")
        job = own_upload(acct, job_id)
        with db.tx() as c:
            msgs = job_messages(c, job_id) if job["status"] == "ready" else []
            facts = {
                r["field"]: r["value"]
                for r in db.all_rows(c, "SELECT field, value FROM facts WHERE job_id = :j", j=job_id)
            }
        return render(
            request,
            "h_job.html",
            job=job,
            msgs=msgs,
            facts=facts,
            preview=preview_hash(msgs),
            shown=shown_slots,
            msg=msg,
            command_id=uuid.uuid4(),
            sample=True,
        )

    @app.post("/h/jobs/{job_id}/approve")
    async def approve(
        request: Request,
        job_id: uuid.UUID,
        command_id: uuid.UUID = Form(...),
        csrf: str = Form(""),
        preview: str = Form(""),
    ) -> Any:
        """The homeowner approves the exact messages to the contractor; perform checks they own the home."""
        acct = deps.require_account(request, "homeowner")
        deps.check_csrf(request, csrf)
        job = own_upload(acct, job_id)
        existing = commands.read_result(command_id)
        if existing is None:
            if job["status"] != "ready":
                return RedirectResponse(
                    f"/h/jobs/{job_id}?msg=This job isn't ready to send.", status_code=303
                )
            with db.tx() as c:
                msgs = job_messages(c, job_id)
                if not msgs or preview_hash(msgs) != preview:
                    return RedirectResponse(
                        f"/h/jobs/{job_id}?msg=The messages changed. Please look again.", status_code=303
                    )
                grant = db.one(
                    c, "SELECT * FROM grants WHERE account_id = :a AND revoked_at IS NULL", a=acct["id"]
                )
                if grant is None:
                    raise HTTPException(status_code=403, detail="Your account can't send email.")
                ids = {m.step: new_approval(c, acct["id"], m, grant) for m in msgs}
        else:
            with db.tx() as c:
                row = db.one(c, "SELECT refs FROM commands WHERE command_id = :id", id=command_id)
            ids = {k: uuid.UUID(v) for k, v in (row or {}).get("refs", {}).get("ids", {}).items()}
        ref = deps.save_command(
            target_workflow=f"job-{job_id}",
            kind="approve",
            account_id=acct["id"],
            command_id=command_id,
            ids=ids,
        )
        out = await commands.send(await deps.temporal(), f"job-{job_id}", ref)
        text = {"done": "Approved. We've asked the contractor to confirm."}.get(
            out.status,
            f"Not sent ({out.code})" if out.status == "rejected" else "Saved. We'll process it shortly.",
        )
        return RedirectResponse(f"/h/jobs/{job_id}?msg={text}", status_code=303)
