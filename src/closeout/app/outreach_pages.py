"""Approval page, the homeowner's answer page and one-click unsubscribe (design 3.6, 3.11, R-OUT-1, R-OUT-3, R-OUT-5).

Opening a link changes nothing: GET shows a page, and changes need a POST. Link tokens are never stored or logged;
only their SHA-256 is kept. The unsubscribe link alone accepts the standard one-click POST with no CSRF token.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from closeout import commands, db
from closeout.act import new_approval
from closeout.app import deps
from closeout.app.deps import render
from closeout.app.security import csrf_ok, csrf_token
from closeout.core.emails import ANSWER_SLOT, UNSUB_SLOT
from closeout.messages import job_messages
from closeout.privacy_hmac import email_hmac

ANSWER_OPEN = ("asking_homeowner", "answered", "no_answer", "on_hold")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def find_link(token: str, purpose: str) -> dict[str, Any] | None:
    if not token or len(token) > 100:
        return None
    with db.tx() as c:
        return db.one(
            c,
            """SELECT l.*, now() > l.expires_at AS expired FROM links l
                            WHERE l.token_hash = :h AND l.purpose = :p""",
            h=token_hash(token),
            p=purpose,
        )


def link_job(link: dict[str, Any]) -> dict[str, Any] | None:
    if not str(link["target"]).startswith("job-"):
        return None
    jid = uuid.UUID(link["target"][4:])
    with db.tx() as c:
        return db.one(
            c,
            """SELECT j.id, j.status, j.round, j.work_type, j.price_visible, k.name AS contractor_name,
                                   pa.address AS parcel_address, j.address_raw
                            FROM jobs j JOIN contractors k ON k.id = j.contractor_id
                            LEFT JOIN parcels pa ON pa.pin = j.pin WHERE j.id = :id""",
            id=jid,
        )


def shown_slots(body: str) -> str:
    return body.replace(ANSWER_SLOT, "[your answer link]").replace(UNSUB_SLOT, "[one-click unsubscribe link]")


def register(app: FastAPI) -> None:
    # --- the contractor approves the whole sequence -----------------------------------------------
    @app.get("/c/approve/{job_id}", response_class=HTMLResponse)
    def approve_page(request: Request, job_id: uuid.UUID, msg: str = "") -> Any:
        acct = deps.require_account(request, "contractor")
        from closeout.app.contractor import own_job

        job = own_job(acct, job_id)
        with db.tx() as c:
            msgs = job_messages(c, job_id)
        return render(
            request,
            "c_approve.html",
            job=job,
            msgs=msgs,
            shown=shown_slots,
            msg=msg,
            sample=True,
            command_id=uuid.uuid4(),
        )

    @app.post("/c/approve/{job_id}")
    async def approve(
        request: Request, job_id: uuid.UUID, command_id: uuid.UUID = Form(...), csrf: str = Form("")
    ) -> Any:
        acct = deps.require_account(request, "contractor")
        deps.check_csrf(request, csrf)
        from closeout.app.contractor import own_job

        job = own_job(acct, job_id)
        if job["status"] != "ready":
            return RedirectResponse(f"/c/jobs/{job_id}?msg=This job isn't ready to send.", status_code=303)
        existing = commands.read_result(command_id)
        if existing is None:
            with db.tx() as c:
                grant = db.one(
                    c, "SELECT * FROM grants WHERE account_id = :a AND revoked_at IS NULL", a=acct["id"]
                )
                if grant is None:
                    raise HTTPException(status_code=403, detail="Your account can't send email.")
                ids = {m.step: new_approval(c, acct["id"], m, grant) for m in job_messages(c, job_id)}
        else:
            with db.tx() as c:
                ref_row = db.one(c, "SELECT refs FROM commands WHERE command_id = :id", id=command_id)
            ids = {k: uuid.UUID(v) for k, v in (ref_row or {}).get("refs", {}).get("ids", {}).items()}
        ref = deps.save_command(
            target_workflow=f"job-{job_id}",
            kind="approve",
            account_id=acct["id"],
            command_id=command_id,
            ids=ids,
        )
        out = await commands.send(await deps.temporal(), f"job-{job_id}", ref)
        text = {"done": "Approved. The first email is on its way."}.get(
            out.status,
            f"Not sent ({out.code})" if out.status == "rejected" else "Saved — we'll process it shortly",
        )
        return RedirectResponse(f"/c/jobs/{job_id}?msg={text}", status_code=303)

    @app.post("/c/jobs/{job_id}/correction")
    async def decide_correction(
        request: Request,
        job_id: uuid.UUID,
        decision: str = Form(...),
        command_id: uuid.UUID = Form(...),
        csrf: str = Form(""),
    ) -> Any:
        acct = deps.require_account(request, "contractor")
        deps.check_csrf(request, csrf)
        from closeout.app.contractor import own_job

        own_job(acct, job_id)
        kind = {"accept": "accept_correction", "reject": "reject_correction"}.get(decision)
        if kind is None:
            raise HTTPException(status_code=400, detail="Unknown choice")
        ref = deps.save_command(
            target_workflow=f"job-{job_id}", kind=kind, account_id=acct["id"], command_id=command_id
        )
        out = await commands.send(await deps.temporal(), f"job-{job_id}", ref)
        return RedirectResponse(
            f"/c/jobs/{job_id}?msg={'Done' if out.status == 'done' else out.code}", status_code=303
        )

    # --- the homeowner answers one job ------------------------------------------------------------
    @app.get("/a/{token}", response_class=HTMLResponse)
    def answer_page(request: Request, token: str) -> Any:
        link = find_link(token, "answer")
        if link is None:
            return render(request, "link_closed.html", status_code=404, why="unknown", account=None)
        job = link_job(link)
        if job is None:
            return render(request, "link_closed.html", status_code=404, why="unknown", account=None)
        if link["round"] != job["round"] or link["revoked_at"] is not None:
            return render(request, "link_closed.html", why="replaced", account=None)
        if link["expired"] or job["status"] not in ANSWER_OPEN:
            return render(request, "link_closed.html", why="closed", account=None)
        with db.tx() as c:
            facts = {
                r["field"]: r["value"]
                for r in db.all_rows(c, "SELECT field, value FROM facts WHERE job_id=:j", j=job["id"])
            }
        return render(
            request,
            "a_answer.html",
            job=job,
            facts=facts,
            token=token,
            account=None,
            sample=True,
            link_csrf=csrf_token("link:" + link["token_hash"]),
            command_id=uuid.uuid4(),
        )

    @app.post("/a/{token}")
    async def answer(
        request: Request,
        token: str,
        answer: str = Form(...),
        csrf: str = Form(""),
        command_id: uuid.UUID = Form(...),
        finish_date: str = Form(""),
        total: str = Form(""),
        price_visible: str = Form(""),
    ) -> Any:
        link = find_link(token, "answer")
        if link is None:
            return render(request, "link_closed.html", status_code=404, why="unknown", account=None)
        if not csrf_ok("link:" + link["token_hash"], csrf):
            raise HTTPException(status_code=403, detail="Form expired. Open the link again.")
        job = link_job(link)
        if job is None or link["round"] != job["round"] or link["revoked_at"] is not None:
            return render(request, "link_closed.html", why="replaced", account=None)
        if link["expired"]:
            return render(request, "link_closed.html", why="closed", account=None)
        if answer not in ("confirm", "correct", "decline", "not_mine"):
            raise HTTPException(status_code=400, detail="Unknown answer")
        ref = deps.save_command(
            target_workflow=f"job-{job['id']}",
            kind="answer",
            link_id=link["id"],
            command_id=command_id,
            codes={"answer": answer, "round": str(link["round"])},
            payload={
                "answer": answer,
                "finish_date": finish_date[:40],
                "total": total[:40],
                "price_visible": price_visible[:5],
            },
        )
        out = await commands.send(await deps.temporal(), f"job-{job['id']}", ref)
        if out.status == "rejected":
            why = (
                "closed"
                if out.code in ("workflow_closed", "job_closed")
                else "replaced"
                if out.code == "old_round"
                else "refused"
            )
            return render(request, "link_closed.html", why=why, code=out.code, account=None)
        return render(
            request, "a_thanks.html", answer=answer, saved=out.status != "done", account=None, sample=True
        )

    # --- one-click unsubscribe --------------------------------------------------------------------
    @app.get("/u/{token}", response_class=HTMLResponse)
    def unsubscribe_page(request: Request, token: str) -> Any:
        link = find_link(token, "unsubscribe")
        if link is None:
            return render(request, "link_closed.html", status_code=404, why="unknown", account=None)
        return render(request, "u_unsubscribe.html", token=token, done=False, account=None)

    @app.post("/u/{token}", response_class=HTMLResponse)
    async def unsubscribe(request: Request, token: str) -> Any:
        """One click, no sign-in, no CSRF token: the link token itself proves the purpose (RFC 8058)."""
        link = find_link(token, "unsubscribe")
        if link is None:
            return render(request, "link_closed.html", status_code=404, why="unknown", account=None)
        job_ids = unsubscribe_recipient(link["recipient_id"])
        client = await deps.temporal()
        for jid in job_ids:
            ref = commands.save(
                target_workflow=f"job-{jid}",
                kind="unsubscribed",
                link_id=link["id"],
                command_id=uuid.uuid5(link["id"], f"unsub:{jid}"),
            )
            await commands.send(client, f"job-{jid}", ref, wait_seconds=2)
        return render(request, "u_unsubscribe.html", token=token, done=True, account=None)


def unsubscribe_recipient(recipient_id: uuid.UUID) -> list[uuid.UUID]:
    """R-OUT-5: add to the do-not-contact list and cancel every queued message to that address, in one go."""
    with db.tx() as c:
        email = db.scalar(
            c,
            """SELECT p.email FROM recipients r JOIN people p ON p.id = r.person_id
                                WHERE r.id = :r""",
            r=recipient_id,
        )
        if not email:
            return []
        h = email_hmac(email)
        db.run(
            c,
            "INSERT INTO suppression (email_hmac, reason) VALUES (:h, 'unsubscribed') ON CONFLICT DO NOTHING",
            h=h,
        )
        rows = db.all_rows(c, "SELECT id, job_id, to_address FROM outbox WHERE state = 'pending' FOR UPDATE")
        hit = [r for r in rows if r["to_address"] and email_hmac(r["to_address"]) == h]
        if hit:
            db.run(
                c,
                """UPDATE outbox SET state = 'cancelled', cancel_reason = 'unsubscribed', updated_at = now()
                         WHERE id = ANY(:ids) AND state = 'pending'""",
                ids=[r["id"] for r in hit],
            )
        jobs = db.all_rows(
            c,
            """SELECT DISTINCT o.job_id FROM outbox o WHERE o.recipient_id IN
                                 (SELECT r2.id FROM recipients r2 JOIN people p2 ON p2.id = r2.person_id
                                  WHERE p2.email_hmac = :h) AND o.job_id IS NOT NULL""",
            h=h,
        )
        db.run(
            c,
            """INSERT INTO audit_log (action, target, details) VALUES ('unsubscribe', :t, CAST(:d AS jsonb))""",
            t=f"recipient-{recipient_id}",
            d=f'{{"cancelled": {len(hit)}}}',
        )
    return [r["job_id"] for r in jobs]
