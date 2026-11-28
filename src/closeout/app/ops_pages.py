"""Ops pages: the needs-a-look list, the outbox, privacy requests and data health (R-OPS-1, R-OPS-2, design 3.12)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from closeout import commands, db, privacy
from closeout.activities.privacy_acts import PrivacyIn
from closeout.app import deps
from closeout.app.deps import render
from closeout.settings import get_settings

REASONS = {
    "address_unmatched": "The job address didn't match exactly one property.",
    "facts_asked": "The two reads disagreed, or the invoice and the contractor's file differ.",
    "other_contractor": "The invoice looks like it's from a different contractor.",
    "light_review": "Light mode: every fact needs a person's check.",
    "read_failed": "The invoice couldn't be read.",
    "not_mine": "The homeowner said it isn't their home.",
    "correction_rejected": "The contractor rejected the homeowner's correction.",
    "home_already_claimed": "Someone claimed a home that already has an owner.",
    "workflow_missing": "A job has no running workflow.",
}


def register(app: FastAPI) -> None:
    deps.HOMES["ops"] = "/ops"

    def ops(request: Request) -> dict[str, Any]:
        return deps.require_account(request, "ops")

    @app.get("/ops", response_class=HTMLResponse)
    def overview(request: Request, msg: str = "") -> Any:
        ops(request)
        with db.tx() as c:
            n = db.one(
                c,
                """SELECT (SELECT count(*) FROM review_items WHERE resolved_at IS NULL) AS review,
                                    (SELECT count(*) FROM outbox WHERE state = 'unknown') AS unknown,
                                    (SELECT count(*) FROM jobs WHERE status = 'on_hold') AS on_hold,
                                    (SELECT count(*) FROM privacy_requests WHERE status = 'running') AS privacy""",
            )
            items = db.all_rows(
                c,
                """SELECT id, kind, ref, reason, evidence, created_at FROM review_items
                                      WHERE resolved_at IS NULL ORDER BY created_at""",
            )
        return render(request, "ops.html", n=n, items=items, reasons=REASONS, msg=msg)

    @app.post("/ops/review/{item_id}/resolve")
    def resolve(
        request: Request, item_id: uuid.UUID, resolution: str = Form("handled"), csrf: str = Form("")
    ) -> Any:
        acct = ops(request)
        deps.check_csrf(request, csrf)
        with db.tx() as c:
            db.run(
                c,
                """UPDATE review_items SET resolved_at = now(), resolved_by = :a, resolution = :r
                         WHERE id = :id AND resolved_at IS NULL""",
                a=acct["id"],
                r=resolution[:60],
                id=item_id,
            )
        return RedirectResponse("/ops?msg=Resolved.", status_code=303)

    @app.get("/ops/outbox", response_class=HTMLResponse)
    def outbox_page(request: Request, msg: str = "") -> Any:
        ops(request)
        with db.tx() as c:
            unknown = db.all_rows(
                c,
                """SELECT o.id, o.step, o.round, o.attempts, o.last_error_code, o.updated_at,
                                       coalesce('job ' || j.job_ref, 'town request') AS what FROM outbox o
                                       LEFT JOIN jobs j ON j.id = o.job_id WHERE o.state = 'unknown'
                                       ORDER BY o.updated_at""",
            )
            held = db.all_rows(
                c,
                """SELECT id, job_ref, needs_reason, updated_at FROM jobs WHERE status = 'on_hold'
                                    ORDER BY updated_at""",
            )
        return render(
            request, "ops_outbox.html", unknown=unknown, held=held, msg=msg, command_id=uuid.uuid4()
        )

    @app.post("/ops/outbox/{outbox_id}")
    def settle_unknown(
        request: Request, outbox_id: uuid.UUID, outcome: str = Form(...), csrf: str = Form("")
    ) -> Any:
        """Ops confirms an unknown message arrived, or closes it. Unknown is never re-sent automatically."""
        acct = ops(request)
        deps.check_csrf(request, csrf)
        if outcome not in ("sent", "cancelled"):
            raise HTTPException(status_code=400, detail="Choose sent or closed.")
        with db.tx() as c:
            n = db.run(
                c,
                """UPDATE outbox SET state = :s, cancel_reason = CASE WHEN :s = 'cancelled' THEN 'ops_closed'
                             END, sent_at = CASE WHEN :s = 'sent' THEN coalesce(sent_at, now()) END, updated_at = now()
                             WHERE id = :id AND state = 'unknown'""",
                s=outcome,
                id=outbox_id,
            )
            db.run(
                c,
                """INSERT INTO audit_log (actor_account_id, action, target, details)
                         VALUES (:a, 'ops_outbox', :t, CAST(:d AS jsonb))""",
                a=acct["id"],
                t=f"outbox-{outbox_id}",
                d=f'{{"outcome": "{outcome}"}}',
            )
        return RedirectResponse(f"/ops/outbox?msg={'Updated.' if n else 'Nothing changed.'}", status_code=303)

    @app.post("/ops/jobs/{job_id}/stop")
    async def stop_job(
        request: Request, job_id: uuid.UUID, command_id: uuid.UUID = Form(...), csrf: str = Form("")
    ) -> Any:
        acct = ops(request)
        deps.check_csrf(request, csrf)
        ref = deps.save_command(
            target_workflow=f"job-{job_id}", kind="ops_stop", account_id=acct["id"], command_id=command_id
        )
        out = await commands.send(await deps.temporal(), f"job-{job_id}", ref)
        return RedirectResponse(
            f"/ops/outbox?msg={'Stopped.' if out.status == 'done' else out.code}", status_code=303
        )

    @app.get("/ops/privacy", response_class=HTMLResponse)
    def privacy_page(request: Request, msg: str = "") -> Any:
        ops(request)
        with db.tx() as c:
            reqs = db.all_rows(
                c,
                "SELECT id, status, created_at, finished_at FROM privacy_requests "
                "ORDER BY created_at DESC LIMIT 50",
            )
        return render(request, "ops_privacy.html", reqs=reqs, msg=msg)

    @app.post("/ops/privacy")
    async def start_privacy(request: Request, email: str = Form(...), csrf: str = Form("")) -> Any:
        """R-OPS-2: ops records a request to erase a person. The address is used to find them, then not stored."""
        acct = ops(request)
        deps.check_csrf(request, csrf)
        with db.tx() as c:
            pid = privacy.person_by_email(c, email[:200])
            if pid is None:
                return RedirectResponse("/ops/privacy?msg=No one with that address.", status_code=303)
            rid = db.scalar(
                c,
                "INSERT INTO privacy_requests (person_id, requested_by) VALUES (:p, :a) RETURNING id",
                p=pid,
                a=acct["id"],
            )
        client = await deps.temporal()
        await client.start_workflow(
            "PrivacyRequest",
            PrivacyIn(request_id=rid, person_id=pid),
            id=f"privacy-{rid}",
            task_queue=get_settings().task_queue,
        )
        return RedirectResponse(
            "/ops/privacy?msg=Started. Their details are being removed everywhere.", status_code=303
        )

    @app.get("/ops/data", response_class=HTMLResponse)
    def data_health(request: Request) -> Any:
        ops(request)
        from closeout.app.data_pages import data_numbers

        with db.tx() as c:
            runs = db.all_rows(c, "SELECT * FROM sync_runs ORDER BY finished_at DESC NULLS LAST LIMIT 10")
            bad = db.all_rows(
                c,
                """SELECT unnest(bad_fields) AS field, count(*) AS n FROM permits
                                    GROUP BY 1 ORDER BY 2 DESC""",
            )
            calls = db.one(
                c,
                """SELECT count(*) AS n, count(*) FILTER (WHERE error IS NOT NULL) AS failed
                                 FROM model_calls""",
            )
        return render(request, "ops_data.html", n=data_numbers(), runs=runs, bad=bad, calls=calls)
