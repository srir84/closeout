"""Homeowner pages: my home, the sale report, share links, the recipient view, claim with a letter code, files.

Every page and download checks the access table in core/report.py (design 3.12, R-REP-1..3, R-REC-4).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from closeout import db, files
from closeout.act import ShareIn, new_share_approval, perform_share
from closeout.app import deps
from closeout.app.deps import render
from closeout.core import report as rp
from closeout.core.record import marks
from closeout.seed import claim_code_hash

ROLE_NAMES = {"buyers_agent": "Buyer's agent", "buyer": "Buyer", "inspector": "Inspector"}
MARK_NAMES = {
    "homeowner_confirmed": "Homeowner confirmed",
    "contractor_confirmed": "Contractor confirmed",
    "permit_on_file": "Town permit on file",
}


def owned_homes(account_id: uuid.UUID) -> list[dict[str, Any]]:
    with db.tx() as c:
        return db.all_rows(
            c,
            """SELECT h.pin, p.address, p.year_built, p.town FROM homes h JOIN parcels p ON p.pin = h.pin
                                 WHERE h.owner_account_id = :a ORDER BY p.address""",
            a=account_id,
        )


def own_home(acct: dict[str, Any], pin: str | None = None) -> dict[str, Any]:
    homes = owned_homes(acct["id"])
    home = next((h for h in homes if pin is None or h["pin"] == pin), None)
    if home is None:
        raise HTTPException(
            status_code=404, detail="You don't own a home here yet. Claim it with the code from your letter."
        )
    return home


def report_data(pin: str) -> tuple[list[rp.ReportJob], list[rp.ReportPermit]]:
    from closeout.events import read_stream

    jobs: list[rp.ReportJob] = []
    with db.tx() as c:
        rows = db.all_rows(
            c,
            """SELECT j.id, j.work_type, j.price_visible, k.name, k.license FROM jobs j
                                 JOIN contractors k ON k.id = j.contractor_id
                                 WHERE j.pin = :p AND j.status = 'recorded' ORDER BY j.created_at""",
            p=pin,
        )
        for r in rows:
            f = {
                x["field"]: x["value"]
                for x in db.all_rows(
                    c,
                    """SELECT field, value FROM facts WHERE job_id = :j
                                                                   AND status IN ('kept', 'answered')""",
                    j=r["id"],
                )
            }
            jobs.append(
                rp.ReportJob(
                    job_id=str(r["id"]),
                    work_type=r["work_type"] or "other",
                    finished=date.fromisoformat(f["completion_date"]) if f.get("completion_date") else None,
                    contractor_name=r["name"],
                    contractor_license=r["license"],
                    marks=[m.kind for m in marks(read_stream(c, f"job-{r['id']}"))],
                    warranty_years=int(f["warranty_years"])
                    if f.get("warranty_years") not in (None, "", "0")
                    else None,
                    price=Decimal(f["total_amount"]) if f.get("total_amount") else None,
                    price_visible=r["price_visible"],
                )
            )
        permits = [
            rp.ReportPermit(
                **{k: x[k] for k in ("permit_no", "permit_type", "trades", "issued_on", "final_on")},
                town_result=x.get("town_result"),
                town_result_date=x.get("town_result_date"),
            )
            for x in db.all_rows(
                c,
                """SELECT p.permit_no, p.permit_type, p.trades, p.issued_on, p.final_on,
                       (SELECT t.result FROM town_requests t WHERE t.permit_pk = p.pk ORDER BY t.created_at DESC LIMIT 1)
                           AS town_result,
                       (SELECT t.result_date FROM town_requests t WHERE t.permit_pk = p.pk
                        ORDER BY t.created_at DESC LIMIT 1) AS town_result_date
                       FROM permit_parcel pp JOIN permits p ON p.pk = pp.permit_pk
                       WHERE pp.pin = :p AND pp.status = 'linked' ORDER BY p.issued_on DESC NULLS LAST""",
                p=pin,
            )
        ]
    return jobs, permits


def link_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register(app: FastAPI) -> None:
    deps.HOMES["homeowner"] = "/h/home"

    @app.get("/h/home", response_class=HTMLResponse)
    def home(request: Request, msg: str = "") -> Any:
        acct = deps.require_account(request, "homeowner")
        homes = owned_homes(acct["id"])
        if not homes:
            return render(
                request,
                "h_claim.html",
                msg=msg or "Claim your home with the code from your letter.",
                sample=True,
            )
        h = homes[0]
        jobs, permits = report_data(h["pin"])
        with db.tx() as c:
            shares = db.all_rows(
                c,
                """SELECT id, role, fields_allowed, expires_at, revoked_at, created_at,
                                       now() >= expires_at AS expired FROM links WHERE purpose = 'share'
                                       AND target = :t ORDER BY created_at DESC""",
                t=f"home-{h['pin']}",
            )
            pending = db.all_rows(
                c,
                """SELECT j.id, j.work_type, j.status, k.name FROM jobs j
                                        JOIN contractors k ON k.id = j.contractor_id
                                        WHERE j.pin = :p AND j.status NOT IN ('recorded', 'retracted', 'dropped')
                                        ORDER BY j.created_at""",
                p=h["pin"],
            )
        return render(
            request,
            "h_home.html",
            home=h,
            jobs=jobs,
            permits=permits,
            shares=shares,
            pending=pending,
            msg=msg,
            marks=MARK_NAMES,
            roles=ROLE_NAMES,
            sample=True,
        )

    @app.get("/h/report", response_class=HTMLResponse)
    def report(request: Request) -> Any:
        acct = deps.require_account(request, "homeowner")
        h = own_home(acct)
        jobs, permits = report_data(h["pin"])
        return render(
            request,
            "report.html",
            r=rp.build(h["address"], jobs, permits, "owner"),
            marks=MARK_NAMES,
            viewer="Owner",
            sample=True,
        )

    @app.get("/h/share", response_class=HTMLResponse)
    def share_form(request: Request) -> Any:
        acct = deps.require_account(request, "homeowner")
        h = own_home(acct)
        return render(request, "h_share.html", home=h, roles=ROLE_NAMES, created=None, sample=True)

    @app.post("/h/share", response_class=HTMLResponse)
    def share(
        request: Request,
        role: str = Form(...),
        show_prices: str = Form(""),
        days: int = Form(14),
        csrf: str = Form(""),
    ) -> Any:
        acct = deps.require_account(request, "homeowner")
        deps.check_csrf(request, csrf)
        h = own_home(acct)
        if role not in rp.SHARE_ROLES:
            raise HTTPException(status_code=400, detail="Pick who the report is for.")
        days = max(1, min(int(days), 14))
        fields = ["work", "contractor", "permit_history"] + (["price"] if show_prices == "yes" else [])
        token = secrets.token_urlsafe(32)
        with db.tx() as c:
            now = db.scalar(c, "SELECT date_trunc('second', now())")
            expires = (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            grant = db.one(c, "SELECT * FROM grants WHERE account_id = :a", a=acct["id"])
            if grant is None:
                raise HTTPException(status_code=403, detail="Your account can't share reports.")
            aid = new_share_approval(
                c, acct["id"], h["pin"], role, tuple(fields), link_hash(token), expires, grant
            )
            out = perform_share(
                c,
                ShareIn(
                    approval_id=aid,
                    pin=h["pin"],
                    role=role,
                    fields=tuple(fields),
                    link_hash=link_hash(token),
                    link_expires=expires,
                    actor_account_id=acct["id"],
                ),
            )
        if not out.ok:
            raise HTTPException(status_code=403, detail=f"Not shared ({out.code}).")
        base = str(request.base_url).rstrip("/")
        return render(
            request,
            "h_share.html",
            home=h,
            roles=ROLE_NAMES,
            sample=True,
            created={
                "url": f"{base}/r/{token}",
                "role": ROLE_NAMES[role],
                "days": days,
                "prices": "price" in fields,
            },
        )

    @app.post("/h/share/{link_id}/revoke")
    def revoke(request: Request, link_id: uuid.UUID, csrf: str = Form("")) -> Any:
        acct = deps.require_account(request, "homeowner")
        deps.check_csrf(request, csrf)
        h = own_home(acct)
        with db.tx() as c:
            n = db.run(
                c,
                """UPDATE links SET revoked_at = now() WHERE id = :id AND purpose = 'share'
                             AND target = :t AND revoked_at IS NULL""",
                id=link_id,
                t=f"home-{h['pin']}",
            )
            db.run(
                c,
                "INSERT INTO audit_log (actor_account_id, action, target, details) VALUES (:a, 'revoke_share', "
                ":t, CAST(:d AS jsonb))",
                a=acct["id"],
                t=f"home-{h['pin']}",
                d=f'{{"link_id": "{link_id}"}}',
            )
        return RedirectResponse(
            f"/h/home?msg={'Link revoked.' if n else 'Nothing to revoke.'}", status_code=303
        )

    @app.get("/r/{token}", response_class=HTMLResponse)
    def shared_report(request: Request, token: str) -> Any:
        """R-REP-2/3: only what the role may see; a revoked or expired link shows nothing."""
        if not token or len(token) > 100:
            return render(request, "link_closed.html", status_code=404, why="unknown", account=None)
        with db.tx() as c:
            link = db.one(
                c,
                """SELECT *, now() >= expires_at AS expired FROM links WHERE token_hash = :h
                                AND purpose = 'share'""",
                h=link_hash(token),
            )
            address = None
            if link is not None:
                address = db.scalar(c, "SELECT address FROM parcels WHERE pin = :p", p=link["target"][5:])
        if (
            link is None
            or link["revoked_at"] is not None
            or link["expired"]
            or link["role"] not in rp.SHARE_ROLES
        ):
            return render(request, "link_closed.html", status_code=404, why="unknown", account=None)
        jobs, permits = report_data(link["target"][5:])
        r = rp.build(
            address or "",
            jobs,
            permits,
            link["role"],
            owner_allows_price="price" in (link["fields_allowed"] or []),
        )
        return render(
            request,
            "report.html",
            r=r,
            marks=MARK_NAMES,
            viewer=ROLE_NAMES[link["role"]],
            account=None,
            sample=True,
        )

    @app.post("/h/claim")
    def claim(request: Request, code: str = Form(...), csrf: str = Form("")) -> Any:
        """R-REC-4: a home's owner comes only from the seed or a code from a letter (simulated)."""
        acct = deps.require_account(request, "homeowner")
        deps.check_csrf(request, csrf)
        with db.tx() as c:
            home = db.one(
                c,
                "SELECT pin, owner_account_id FROM homes WHERE claim_code_hash = :h FOR UPDATE",
                h=claim_code_hash(code[:40]),
            )
            if home is None:
                msg = "That code didn't match. Check the letter and try again."
            elif home["owner_account_id"] is None:
                db.run(
                    c, "UPDATE homes SET owner_account_id = :a WHERE pin = :p", a=acct["id"], p=home["pin"]
                )
                db.run(
                    c,
                    "INSERT INTO audit_log (actor_account_id, action, target) VALUES (:a, 'claim_home', :t)",
                    a=acct["id"],
                    t=f"home-{home['pin']}",
                )
                msg = "This home is now yours on Closeout."
            elif home["owner_account_id"] == acct["id"]:
                msg = "You already own this home."
            else:
                db.run(
                    c,
                    """INSERT INTO review_items (kind, ref, reason, evidence, dedupe_key)
                             VALUES ('second_claim', :r, 'home_already_claimed', CAST(:e AS jsonb), :d)
                             ON CONFLICT (dedupe_key) DO NOTHING""",
                    r=f"home-{home['pin']}",
                    e=f'{{"account_id": "{acct["id"]}"}}',
                    d=f"second_claim:{home['pin']}:{acct['id']}",
                )
                msg = "Someone already claimed this home. We've asked a person to look at it."
        return RedirectResponse(f"/h/home?msg={msg}", status_code=303)

    @app.get("/files/{document_id}")
    def download(request: Request, document_id: uuid.UUID) -> Any:
        """Invoice files: the home's owner and the job's contractor only. Never through a share link (R-REP-2)."""
        acct = deps.require_account(request)
        with db.tx() as c:
            d = db.one(
                c,
                """SELECT d.path, d.mime, d.removed_at, j.contractor_id, j.pin, d.kind FROM documents d
                             LEFT JOIN jobs j ON j.id = d.job_id WHERE d.id = :id""",
                id=document_id,
            )
            owner = (
                db.scalar(c, "SELECT owner_account_id FROM homes WHERE pin = :p", p=d["pin"]) if d else None
            )
        if d is None or d["removed_at"] is not None:
            raise HTTPException(status_code=404, detail="Not found")
        role = (
            "owner"
            if owner == acct["id"]
            else "contractor"
            if acct["kind"] == "contractor" and acct["contractor_id"] == d["contractor_id"]
            else None
        )
        field = "invoice_file" if d["kind"] == "invoice" else "town_reply_file"
        if role is None or not rp.allowed(field, role):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(files.path_of(d["path"]), media_type=d["mime"])
