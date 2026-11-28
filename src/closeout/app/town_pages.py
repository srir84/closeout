"""Asking the town (R-TOWN-1, R-TOWN-2): the owner's request pages and the clerk's upload link."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from closeout import clockdb, commands, db, files
from closeout.act import new_approval
from closeout.app import deps
from closeout.app.deps import render
from closeout.app.homeowner import own_home
from closeout.app.outreach_pages import find_link, preview_hash, shown_slots
from closeout.messages import town_messages
from closeout.settings import get_settings
from closeout.workflows.town_request import TownIn

RESULT_WORDS = {
    "final_on_file": "Final approval on file",
    "no_record": "No record of a final approval found",
    "unclear": "Unclear: a person is checking the town's letter",
}


def eligible_permits(pin: str) -> list[dict[str, Any]]:
    """R-TOWN-1: permits on the home with no final approval in state data and older than 12 months (virtual)."""
    with db.tx() as c:
        today = clockdb.virtual_now(c).date()
        return db.all_rows(
            c,
            """SELECT p.pk, p.permit_no, p.permit_type, p.issued_on FROM permit_parcel pp
                                 JOIN permits p ON p.pk = pp.permit_pk
                                 WHERE pp.pin = :pin AND pp.status = 'linked' AND p.final_on IS NULL
                                 AND p.issued_on IS NOT NULL AND p.issued_on <= :cut
                                 AND NOT EXISTS (SELECT 1 FROM town_requests t WHERE t.permit_pk = p.pk
                                                 AND t.status NOT IN ('no_reply', 'closed', 'replied'))
                                 ORDER BY p.issued_on""",
            pin=pin,
            cut=today - timedelta(days=365),
        )


def own_request(acct: dict[str, Any], request_id: uuid.UUID) -> dict[str, Any]:
    h = own_home(acct)
    with db.tx() as c:
        t = db.one(
            c,
            """SELECT t.*, p.permit_no, p.issued_on FROM town_requests t JOIN permits p ON p.pk = t.permit_pk
                         WHERE t.id = :id AND t.home_pin = :pin""",
            id=request_id,
            pin=h["pin"],
        )
    if t is None:
        raise HTTPException(status_code=404, detail="No such request")
    return t


def register(app: FastAPI) -> None:
    @app.get("/h/town", response_class=HTMLResponse)
    def town_home(request: Request, msg: str = "") -> Any:
        acct = deps.require_account(request, "homeowner")
        h = own_home(acct)
        with db.tx() as c:
            reqs = db.all_rows(
                c,
                """SELECT t.id, t.status, t.result, t.result_date, p.permit_no FROM town_requests t
                                     JOIN permits p ON p.pk = t.permit_pk WHERE t.home_pin = :p
                                     ORDER BY t.created_at DESC""",
                p=h["pin"],
            )
        return render(
            request,
            "h_town.html",
            home=h,
            eligible=eligible_permits(h["pin"]),
            reqs=reqs,
            words=RESULT_WORDS,
            msg=msg,
            sample=True,
        )

    @app.post("/h/town/ask/{pk}")
    async def ask(request: Request, pk: str, csrf: str = Form("")) -> Any:
        acct = deps.require_account(request, "homeowner")
        deps.check_csrf(request, csrf)
        h = own_home(acct)
        if pk not in {p["pk"] for p in eligible_permits(h["pin"])}:
            raise HTTPException(status_code=400, detail="The town can't be asked about that permit.")
        with db.tx() as c:
            rid = db.scalar(
                c,
                """INSERT INTO town_requests (home_pin, permit_pk, account_id) VALUES (:h, :p, :a)
                                  RETURNING id""",
                h=h["pin"],
                p=pk,
                a=acct["id"],
            )
            clk = clockdb.clock_in(clockdb.read(c))
        client = await deps.temporal()
        await client.start_workflow(
            "TownRequest",
            TownIn(request_id=rid, clock=clk),
            id=f"town-{rid}",
            task_queue=get_settings().task_queue,
        )
        return RedirectResponse(f"/h/town/{rid}", status_code=303)

    @app.get("/h/town/{request_id}", response_class=HTMLResponse)
    def request_page(request: Request, request_id: uuid.UUID, msg: str = "") -> Any:
        acct = deps.require_account(request, "homeowner")
        t = own_request(acct, request_id)
        with db.tx() as c:
            msgs = town_messages(c, request_id)
        return render(
            request,
            "h_town_request.html",
            t=t,
            msgs=msgs,
            shown=shown_slots,
            preview=preview_hash(msgs),
            words=RESULT_WORDS,
            msg=msg,
            command_id=uuid.uuid4(),
            sample=True,
        )

    @app.post("/h/town/{request_id}/approve")
    async def approve(
        request: Request,
        request_id: uuid.UUID,
        command_id: uuid.UUID = Form(...),
        preview: str = Form(""),
        csrf: str = Form(""),
    ) -> Any:
        acct = deps.require_account(request, "homeowner")
        deps.check_csrf(request, csrf)
        t = own_request(acct, request_id)
        if t["status"] != "drafted":
            return RedirectResponse(f"/h/town/{request_id}?msg=Already sent.", status_code=303)
        if commands.read_result(command_id) is None:
            with db.tx() as c:
                msgs = town_messages(c, request_id)
                if preview_hash(msgs) != preview:
                    return RedirectResponse(
                        f"/h/town/{request_id}?msg=The messages changed. Please review them again.",
                        status_code=303,
                    )
                grant = db.one(
                    c, "SELECT * FROM grants WHERE account_id = :a AND revoked_at IS NULL", a=acct["id"]
                )
                if grant is None:
                    raise HTTPException(status_code=403, detail="Your account can't send email.")
                ids = {m.step: new_approval(c, acct["id"], m, grant) for m in msgs}
        else:
            with db.tx() as c:
                refs = db.scalar(c, "SELECT refs FROM commands WHERE command_id = :id", id=command_id) or {}
            ids = {k: uuid.UUID(v) for k, v in refs.get("ids", {}).items()}
        ref = deps.save_command(
            target_workflow=f"town-{request_id}",
            kind="approve",
            account_id=acct["id"],
            command_id=command_id,
            ids=ids,
        )
        out = await commands.send(await deps.temporal(), f"town-{request_id}", ref)
        text = "Approved. The request is on its way to the town clerk." if out.status == "done" else out.code
        return RedirectResponse(f"/h/town/{request_id}?msg={text}", status_code=303)

    # --- the clerk ----------------------------------------------------------------------------------
    @app.get("/t/{token}", response_class=HTMLResponse)
    def clerk_page(request: Request, token: str) -> Any:
        link = find_link(token, "town_reply")
        if link is None or link["revoked_at"] is not None or link["expired"]:
            return render(request, "link_closed.html", status_code=404, why="unknown", account=None)
        with db.tx() as c:
            t = db.one(
                c,
                """SELECT t.status, p.permit_no, pa.address FROM town_requests t
                             JOIN permits p ON p.pk = t.permit_pk JOIN parcels pa ON pa.pin = t.home_pin
                             WHERE t.id = :id""",
                id=uuid.UUID(link["target"][5:]),
            )
        if t is None or t["status"] in ("replied", "closed"):
            return render(request, "link_closed.html", why="closed", account=None)
        from closeout.app.security import csrf_token

        return render(
            request,
            "t_upload.html",
            t=t,
            token=token,
            account=None,
            link_csrf=csrf_token("link:" + link["token_hash"]),
            command_id=uuid.uuid4(),
        )

    @app.post("/t/{token}", response_class=HTMLResponse)
    async def clerk_upload(
        request: Request,
        token: str,
        reply: UploadFile = File(...),
        csrf: str = Form(""),
        command_id: uuid.UUID = Form(...),
    ) -> Any:
        from closeout.app.security import csrf_ok

        link = find_link(token, "town_reply")
        if link is None or link["revoked_at"] is not None or link["expired"]:
            return render(request, "link_closed.html", status_code=404, why="unknown", account=None)
        if not csrf_ok("link:" + link["token_hash"], csrf):
            raise HTTPException(status_code=403, detail="Form expired. Open the link again.")
        data = await reply.read(files.MAX_BYTES + 1)
        chk = files.check(data)
        if not chk.ok or chk.mime is None:
            return render(
                request,
                "t_done.html",
                ok=False,
                why=files.MESSAGES.get(chk.reason or "", "Refused"),
                account=None,
            )
        rid = uuid.UUID(link["target"][5:])
        with db.tx() as c:
            doc = db.scalar(
                c,
                """INSERT INTO documents (town_request_id, kind, sha256, path, mime, pages)
                                  VALUES (:t, 'town_reply', :s, :p, :m, :n) RETURNING id""",
                t=rid,
                s=chk.sha256,
                p=files.store(data, chk.mime),
                m=chk.mime,
                n=chk.pages,
            )
        ref = deps.save_command(
            target_workflow=f"town-{rid}",
            kind="reply",
            link_id=link["id"],
            command_id=command_id,
            ids={"document_id": doc},
        )
        out = await commands.send(await deps.temporal(), f"town-{rid}", ref, wait_seconds=10)
        if out.status == "rejected":
            return render(request, "link_closed.html", why="closed", account=None)
        return render(request, "t_done.html", ok=True, account=None)


def link_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
