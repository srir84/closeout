"""The web app (FastAPI + Jinja). Pages save what a person did and pass it to workflows (rule 1)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from closeout import clockdb, commands, db
from closeout.app import deps
from closeout.app.deps import render
from closeout.app.security import COOKIE, HostAndHeaders, session_value
from closeout.settings import get_settings
from closeout.workflows.payloads import PingIn


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Closeout (demo)", docs_url=None, redoc_url=None)
    app.add_middleware(HostAndHeaders, allowed=s.allowed_host_list)
    app.mount("/static", StaticFiles(directory=str(deps.HERE / "static")), name="static")

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> Any:
        if request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return render(request, "error.html", status_code=exc.status_code, message=exc.detail)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Any:
        with db.tx() as c:
            people = db.all_rows(
                c,
                """SELECT a.id, a.kind, p.display_name FROM accounts a
                                      JOIN people p ON p.id = a.person_id WHERE p.erased_at IS NULL
                                      ORDER BY a.demo_order, p.display_name""",
            )
        return render(request, "signin.html", people=people)

    @app.post("/signin")
    def signin(account_id: uuid.UUID = Form(...)) -> Any:
        acct = deps.load_account(account_id)
        if acct is None:
            raise HTTPException(status_code=404, detail="No such demo person")
        resp = RedirectResponse(home_for(acct), status_code=303)
        resp.set_cookie(COOKIE, session_value(account_id), httponly=True, samesite="lax")
        return resp

    @app.post("/signout")
    def signout(request: Request, csrf: str = Form("")) -> Any:
        deps.check_csrf(request, csrf)
        resp = RedirectResponse("/", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/clock")
    def clock() -> dict[str, Any]:
        info = deps.clock_info()
        return {
            "virtual_now": info["virtual_now"].isoformat() if info["virtual_now"] else None,
            "scale": info["scale"],
        }

    @app.get("/commands/{command_id}")
    def command_status(request: Request, command_id: uuid.UUID) -> Any:
        acct = deps.current_account(request)
        with db.tx() as c:
            row = db.one(
                c, "SELECT account_id, state, result FROM commands WHERE command_id=:id", id=command_id
            )
        if row is None or (
            row["account_id"] is not None and (acct is None or acct["id"] != row["account_id"])
        ):
            raise HTTPException(status_code=404, detail="Not found")
        return {"state": row["state"], "result": row["result"]}

    # --- walking skeleton (S0) -------------------------------------------------
    @app.get("/skeleton", response_class=HTMLResponse)
    async def skeleton(request: Request, ping: uuid.UUID | None = None, msg: str = "") -> Any:
        deps.require_account(request)
        status = None
        if ping is not None:
            client = await deps.temporal()
            try:
                desc = await client.get_workflow_handle(f"ping-{ping}").describe()
                status = desc.status.name if desc.status else "UNKNOWN"
            except Exception:
                status = "NOT_FOUND"
        scale = deps.clock_info()["scale"]
        return render(
            request,
            "skeleton.html",
            ping=ping,
            status=status,
            msg=msg,
            default_wait=int(120 * scale),
            command_id=uuid.uuid4(),
        )

    @app.post("/skeleton/start")
    async def skeleton_start(request: Request, csrf: str = Form(""), wait_seconds: float = Form(60)) -> Any:
        deps.require_account(request)
        deps.check_csrf(request, csrf)
        with db.tx() as c:
            clk = clockdb.read(c)
        ping_id = uuid.uuid4()
        client = await deps.temporal()
        await client.start_workflow(
            "Ping",
            PingIn(ping_id=ping_id, clock=clockdb.clock_in(clk), wait_virtual_seconds=wait_seconds),
            id=f"ping-{ping_id}",
            task_queue=get_settings().task_queue,
        )
        return RedirectResponse(f"/skeleton?ping={ping_id}", status_code=303)

    @app.post("/skeleton/press")
    async def skeleton_press(
        request: Request, ping: uuid.UUID = Form(...), csrf: str = Form(""), command_id: uuid.UUID = Form(...)
    ) -> Any:
        acct = deps.require_account(request)
        deps.check_csrf(request, csrf)
        ref = deps.save_command(
            target_workflow=f"ping-{ping}", kind="press", account_id=acct["id"], command_id=command_id
        )
        out = await commands.send(await deps.temporal(), f"ping-{ping}", ref)
        msg = {"done": "Done", "rejected": f"Refused: {out.code}"}.get(
            out.status, "Saved — we'll process it shortly"
        )
        return RedirectResponse(f"/skeleton?ping={ping}&msg={msg}", status_code=303)

    from closeout.app import routes

    routes.register(app)
    return app


def home_for(acct: dict[str, Any]) -> str:
    return deps.HOMES.get(acct["kind"], "/skeleton")


app = create_app()
