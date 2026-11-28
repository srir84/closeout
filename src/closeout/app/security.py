"""Demo sign-in cookies, CSRF tokens, host allow-list and page headers."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp

from closeout import keys

COOKIE = "closeout_session"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def _mac(msg: str) -> str:
    return hmac.new(keys.session_key(), msg.encode(), hashlib.sha256).hexdigest()


def session_value(account_id: uuid.UUID) -> str:
    return f"{account_id}.{_mac('session:' + str(account_id))}"


def account_from_cookie(value: str | None) -> uuid.UUID | None:
    if not value or "." not in value:
        return None
    aid, mac = value.rsplit(".", 1)
    try:
        parsed = uuid.UUID(aid)
    except ValueError:
        return None
    if not hmac.compare_digest(mac, _mac("session:" + str(parsed))):
        return None
    return parsed


def csrf_token(scope: str) -> str:
    """A CSRF token bound to a session or a link (scope is the session value or the link hash)."""
    return _mac("csrf:" + scope)


def csrf_ok(scope: str, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(csrf_token(scope), token or "")


class HostAndHeaders(BaseHTTPMiddleware):
    """Answer only the hosts in ALLOWED_HOSTS (exact Host header), and set safety headers."""

    def __init__(self, app: ASGIApp, allowed: list[str]) -> None:
        super().__init__(app)
        self.allowed = {h.lower() for h in allowed}

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        host = request.headers.get("host", "").lower()
        if host not in self.allowed:
            return PlainTextResponse("Unknown host", status_code=400)
        if request.method == "POST":
            # Another website can't make the browser post here (sign-in included). Browsers say where a request
            # comes from in Sec-Fetch-Site. Origin is checked only when it names a site: with no-referrer,
            # browsers send "null" even for our own forms. Mail clients' one-click unsubscribe sends neither.
            origin = request.headers.get("origin", "")
            if request.headers.get("sec-fetch-site", "").lower() == "cross-site" or (
                origin not in ("", "null") and urlsplit(origin).netloc.lower() not in self.allowed
            ):
                return PlainTextResponse("Forms can only be sent from this site.", status_code=403)
            # Every body is refused by size before anything is read or spooled to disk, whatever its type says.
            length = request.headers.get("content-length")
            if length is None and request.headers.get("transfer-encoding"):
                return PlainTextResponse("Uploads need a Content-Length.", status_code=411)
            if length is not None and (not length.isdigit() or int(length) > MAX_UPLOAD_BYTES):
                return PlainTextResponse("The upload is too big (at most 100 MB in one go).", status_code=413)
        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers.setdefault("Cache-Control", "no-store")
        return response
