"""Shared web helpers: templates, the current account, the Temporal client."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from temporalio.client import Client

from closeout import clockdb, db
from closeout.app.security import COOKIE, account_from_cookie, csrf_ok, csrf_token, msg_sig

HERE = Path(__file__).parent
HOMES: dict[str, str] = {}  # role -> start page, filled in by each slice
templates = Jinja2Templates(directory=str(HERE / "templates"))  # autoescape is on for .html

_client: Client | None = None
_client_lock = asyncio.Lock()


async def temporal() -> Client:
    global _client
    async with _client_lock:
        if _client is None:
            from closeout.temporal import connect

            _client = await connect()
    return _client


def load_account(account_id: uuid.UUID) -> dict[str, Any] | None:
    with db.tx() as c:
        return db.one(
            c,
            """SELECT a.id, a.kind, a.contractor_id, a.person_id, p.display_name
                            FROM accounts a JOIN people p ON p.id = a.person_id WHERE a.id = :id""",
            id=account_id,
        )


def current_account(request: Request) -> dict[str, Any] | None:
    aid = account_from_cookie(request.cookies.get(COOKIE))
    return load_account(aid) if aid else None


def require_account(request: Request, kind: str | None = None) -> dict[str, Any]:
    acct = current_account(request)
    if acct is None:
        raise HTTPException(status_code=401, detail="Sign in first")
    if kind is not None and acct["kind"] != kind:
        raise HTTPException(status_code=403, detail="Not allowed")
    return acct


def save_command(**kw: Any) -> Any:
    """Save a click. A form re-sent with the same command ID gets the first command back."""
    from closeout import commands

    try:
        return commands.save(**kw)
    except commands.CommandConflict:
        raise HTTPException(
            status_code=409, detail="This form was already used for something else."
        ) from None


def check_csrf(request: Request, token: str | None) -> None:
    scope = request.cookies.get(COOKIE) or ""
    if not csrf_ok(scope, token):
        raise HTTPException(status_code=403, detail="Form expired. Go back and try again.")


def clock_info() -> dict[str, Any]:
    with db.tx() as c:
        try:
            clk = clockdb.read(c)
        except RuntimeError:
            return {"scale": 1.0, "virtual_now": None}
        now = db.scalar(c, "SELECT now()")
    return {"scale": clk.scale, "virtual_now": clk.virtual(now)}


def render(request: Request, name: str, status_code: int = 200, **ctx: Any) -> Any:
    acct = ctx.pop("account", None) or current_account(request)
    scope = request.cookies.get(COOKIE) or ""
    base = {
        "request": request,
        "account": acct,
        "csrf": csrf_token(scope) if scope else "",
        "clock": clock_info(),
    }
    base.update(ctx)
    # A notice that came from the URL is shown only if this site signed it (see security.sign_location).
    url_msg = request.query_params.get("msg") or ""
    if base.get("msg") and base["msg"] == url_msg and request.query_params.get("ms") != msg_sig(url_msg):
        base["msg"] = ""
    return templates.TemplateResponse(request, name, base, status_code=status_code)
