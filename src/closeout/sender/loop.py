"""The outbox sender: each approved message is delivered at most once (design 3.7, R-OUT-2).

1. Claim (transaction 1, committed before any network call): the job still waits on this round, the grant
   is active, the address isn't suppressed. The row becomes `sending` with a new attempt ID and a lease.
   Fresh answer and unsubscribe links are made; only their hashes are stored.
2. Send through SMTP, stage by stage. The outcome follows the stage table.
3. Finish (transaction 2): only if this attempt still owns the row. Then tell the workflow.
A reaper moves `sending` rows whose lease has run out to `unknown`. Unknown is never re-sent automatically.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import secrets
import smtplib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any

from closeout import db
from closeout.core.emails import fill
from closeout.core.wording import check_text
from closeout.privacy_hmac import email_hmac
from closeout.sender.checks import allowed_recipient
from closeout.settings import Settings

log = logging.getLogger("closeout.sender")
RETRY_SPACING = (5, 30, 120, 600, 1800)  # seconds before tries 2..6; after the 5th try: failed
MAX_TRIES = 5
WAITING_STATES = ("asking_homeowner", "asking_contractor")
FROM = "Closeout demo <no-reply@closeout.test>"


class Crash(Exception):
    """Raised by a test fault point to stop the sender exactly there, as if the process died."""


Faults = Callable[[str], None]


def no_faults(point: str) -> None:
    return None


@dataclass
class Claimed:
    id: uuid.UUID
    attempt_id: uuid.UUID
    job_id: uuid.UUID | None
    town_request_id: uuid.UUID | None
    round: int
    to_address: str
    subject: str
    body: str
    message_id: str
    answer_url: str
    unsub_url: str
    attempts: int


@dataclass
class Outcome:
    state: str  # sent | pending | failed | unknown
    code: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Sender:
    def __init__(
        self,
        s: Settings,
        signal: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        faults: Faults = no_faults,
        smtp: Callable[..., smtplib.SMTP] = smtplib.SMTP,
    ) -> None:
        self.s = s
        self.signal = signal
        self.fault = faults
        self.smtp = smtp

    # --- database steps ---------------------------------------------------------------------------
    def reap(self) -> list[dict[str, Any]]:
        with db.tx() as c:
            return db.all_rows(
                c,
                """UPDATE outbox SET state = 'unknown', last_error_code = 'lease_expired',
                                     updated_at = now() WHERE state = 'sending' AND lease_until < now()
                                     RETURNING id, job_id, town_request_id, round, state, sent_at""",
            )

    def due(self, limit: int = 20) -> list[uuid.UUID]:
        with db.tx() as c:
            return [
                r["id"]
                for r in db.all_rows(
                    c,
                    """SELECT id FROM outbox WHERE state = 'pending'
                    AND next_attempt_at <= now() ORDER BY next_attempt_at, created_at LIMIT :n""",
                    n=limit,
                )
            ]

    def claim(self, oid: uuid.UUID) -> Claimed | None:
        """Transaction 1. The permission boundary: re-check the job, grant and do-not-contact list."""
        self.fault("before_claim")
        with db.tx() as c:
            row = db.one(c, "SELECT * FROM outbox WHERE id = :id", id=oid)
            if row is None or row["state"] != "pending":
                return None
            if row["job_id"] is not None:
                job = db.one(c, "SELECT status, round FROM jobs WHERE id = :j FOR SHARE", j=row["job_id"])
                if job is None or job["round"] != row["round"] or job["status"] not in WAITING_STATES:
                    self._cancel(c, oid, "job_not_waiting")
                    return None
            if row["town_request_id"] is not None:
                tr = db.one(
                    c,
                    "SELECT status, round FROM town_requests WHERE id = :t FOR SHARE",
                    t=row["town_request_id"],
                )
                if tr is None or tr["round"] != row["round"] or tr["status"] not in ("approved", "sent"):
                    self._cancel(c, oid, "request_not_waiting")
                    return None
            if row["grant_id"] is not None:
                g = db.one(
                    c,
                    "SELECT revoked_at, expires_at < now() AS expired FROM grants WHERE id = :g",
                    g=row["grant_id"],
                )
                if g is None or g["revoked_at"] is not None or g["expired"]:
                    self._cancel(c, oid, "grant_revoked")
                    return None
            # The same lock as unsubscribe: either the unsubscribe committed first and we see it, or this claim
            # commits first and is the one message that may still arrive (R-OUT-5).
            addr = email_hmac(row["to_address"] or "")
            db.scalar(c, "SELECT pg_advisory_xact_lock(hashtextextended(:h, 7))", h=addr)
            if db.scalar(c, "SELECT count(*) FROM suppression WHERE email_hmac = :h", h=addr):
                self._cancel(c, oid, "suppressed")
                return None
            attempt = uuid.uuid4()
            got = db.one(
                c,
                """UPDATE outbox SET state = 'sending', attempt_id = :a,
                               lease_until = now() + make_interval(secs => :lease), attempts = attempts + 1,
                               updated_at = now()
                               WHERE id = :id AND state = 'pending' AND next_attempt_at <= now() RETURNING *""",
                a=attempt,
                lease=self.s.sender_lease_seconds,
                id=oid,
            )
            if got is None:
                return None
            answer, unsub = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            target = f"job-{got['job_id']}" if got["job_id"] else f"town-{got['town_request_id']}"
            origin = (
                db.scalar(c, "SELECT origin FROM jobs WHERE id = :j", j=got["job_id"])
                if got["job_id"]
                else None
            )
            purpose = {"contractor": "answer", "homeowner": "contractor_confirm"}.get(
                origin or "", "town_reply"
            )
            db.run(
                c,
                """INSERT INTO links (token_hash, purpose, target, round, outbox_id, recipient_id, expires_at)
                         VALUES (:h, :p, :t, :r, :o, :rec, now() + make_interval(days => :days))""",
                h=token_hash(answer),
                p=purpose,
                t=target,
                r=got["round"],
                o=oid,
                rec=got["recipient_id"],
                # A town letter may come up to 60 days after the request (late window), so its link lasts longer.
                days=75 if purpose == "town_reply" else 30,
            )
            db.run(
                c,
                """INSERT INTO links (token_hash, purpose, target, round, outbox_id, recipient_id, expires_at)
                         VALUES (:h, 'unsubscribe', :t, :r, :o, :rec, now() + interval '365 days')""",
                h=token_hash(unsub),
                t=target,
                r=got["round"],
                o=oid,
                rec=got["recipient_id"],
            )
        base = self.s.app_public_url.rstrip("/")
        path = {"answer": "a", "contractor_confirm": "k", "town_reply": "t"}[purpose]
        return Claimed(
            id=oid,
            attempt_id=attempt,
            job_id=got["job_id"],
            town_request_id=got["town_request_id"],
            round=got["round"],
            to_address=got["to_address"] or "",
            subject=got["subject"] or "",
            body=got["body_template"] or "",
            message_id=got["message_id"],
            answer_url=f"{base}/{path}/{answer}",
            unsub_url=f"{base}/u/{unsub}",
            attempts=got["attempts"],
        )

    def _cancel(self, c: Any, oid: uuid.UUID, why: str) -> None:
        db.run(
            c,
            """UPDATE outbox SET state = 'cancelled', cancel_reason = :w, updated_at = now()
                     WHERE id = :id AND state = 'pending'""",
            w=why,
            id=oid,
        )

    def finish(self, cl: Claimed, out: Outcome) -> dict[str, Any] | None:
        """Transaction 2: only if this attempt still owns the row; otherwise a newer state won."""
        state = out.state
        if state == "pending" and cl.attempts >= MAX_TRIES:
            state = "failed"
        with db.tx() as c:
            delay = RETRY_SPACING[min(cl.attempts, len(RETRY_SPACING)) - 1]
            row = db.one(
                c,
                """UPDATE outbox SET state = :st, last_error_code = :code,
                               sent_at = CASE WHEN :st = 'sent' THEN now() ELSE sent_at END,
                               next_attempt_at = CASE WHEN :st = 'pending'
                                   THEN now() + make_interval(secs => :delay) ELSE next_attempt_at END,
                               lease_until = NULL, updated_at = now()
                               WHERE id = :id AND attempt_id = :a AND state = 'sending'
                               RETURNING id, job_id, town_request_id, round, state, sent_at""",
                st=state,
                code=None if state == "sent" else out.code,
                delay=delay,
                id=cl.id,
                a=cl.attempt_id,
            )
        if row is None:
            log.info("late result for %s: a newer state won", cl.id)
        return row

    # --- SMTP ---------------------------------------------------------------------------------------
    def message(self, cl: Claimed) -> EmailMessage:
        m = EmailMessage()
        m["From"] = FROM
        m["To"] = cl.to_address
        m["Subject"] = cl.subject
        m["Message-ID"] = cl.message_id
        m["Date"] = formatdate(localtime=False)
        m["List-Unsubscribe"] = f"<{cl.unsub_url}>"
        m["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        m.set_content(fill(cl.body, cl.answer_url, cl.unsub_url))
        return m

    def deliver(self, cl: Claimed) -> Outcome:
        if not allowed_recipient(cl.to_address):
            return Outcome("failed", "bad_domain")
        msg = self.message(cl)
        text = msg.get_content() + "\n" + str(msg["Subject"])
        if check_text(text):
            return Outcome("failed", "banned_words")
        self.fault("after_claim")
        t = self.s.smtp_timeout_seconds
        try:
            conn = self.smtp(self.s.smtp_host, self.s.smtp_port, timeout=t)
        except (OSError, smtplib.SMTPException):
            return Outcome("pending", "connect")
        try:
            try:
                code, _ = conn.ehlo()
                if code >= 400:
                    return Outcome("failed" if code >= 500 else "pending", f"ehlo_{code}")
                code, _ = conn.mail(FROM.split("<")[1].rstrip(">"))
                if code != 250:
                    return Outcome("failed" if code >= 500 else "pending", f"mail_{code}")
                code, _ = conn.rcpt(cl.to_address)
                if code not in (250, 251):
                    return Outcome("failed" if code >= 500 else "pending", f"rcpt_{code}")
                self.fault("after_rcpt")
                code, _ = conn.docmd("data")
                if code != 354:
                    return Outcome("failed" if code >= 500 else "pending", f"data_{code}")
            except (OSError, smtplib.SMTPException):
                return Outcome("pending", "dropped_before_body")
            body = smtplib._quote_periods(smtplib._fix_eols(msg.as_string()).encode("utf-8"))  # type: ignore[attr-defined]
            if not body.endswith(b"\r\n"):
                body += b"\r\n"
            try:
                conn.send(body + b".\r\n")
            except (OSError, smtplib.SMTPException):
                return Outcome("unknown", "dropped_during_body")
            # The body may have gone. From here, anything but a clear reply is `unknown`.
            self.fault("after_body")
            try:
                code, _ = conn.getreply()
            except (TimeoutError, OSError, smtplib.SMTPException):
                return Outcome("unknown", "no_final_reply")
            if code == 250:
                self.fault("after_250")
                return Outcome("sent", "250")
            # Only a clear refusal is trusted. Anything else after the body may mean it went: unknown, never re-sent.
            if 400 <= code < 500:
                return Outcome("pending", f"final_{code}")
            if 500 <= code < 600:
                return Outcome("failed", f"final_{code}")
            return Outcome("unknown", f"final_unclear_{code}")
        finally:
            with contextlib.suppress(Exception):
                conn.close()

    # --- the loop -------------------------------------------------------------------------------------
    async def tell(self, row: dict[str, Any]) -> None:
        if self.signal is None or row is None:
            return
        try:
            await self.signal(row)
        except Exception as e:  # the workflow's backup check covers a lost signal
            log.info("signal for %s not delivered: %s", row["id"], type(e).__name__)

    async def run_once(self) -> int:
        n = 0
        for row in await asyncio.to_thread(self.reap):
            await self.tell(row)
        for oid in await asyncio.to_thread(self.due):
            cl = await asyncio.to_thread(self.claim, oid)
            if cl is None:
                continue
            out = await asyncio.to_thread(self.deliver, cl)
            row = await asyncio.to_thread(self.finish, cl, out)
            self.fault("after_tx2")
            if row is not None and row["state"] in ("sent", "failed", "unknown"):
                await self.tell(row)
            n += 1
        return n


async def temporal_signaller(s: Settings) -> Callable[[dict[str, Any]], Awaitable[None]]:
    from closeout.temporal import connect
    from closeout.workflows.payloads import Delivered

    client = await connect()

    async def signal(row: dict[str, Any]) -> None:
        wf = f"job-{row['job_id']}" if row["job_id"] else f"town-{row['town_request_id']}"
        sent_at: datetime | None = row["sent_at"]
        await client.get_workflow_handle(wf).signal(
            "delivered",
            Delivered(outbox_id=row["id"], state=row["state"], sent_at=sent_at, round=row["round"]),
        )

    return signal


async def run_forever(s: Settings) -> None:
    sender = Sender(s, signal=await temporal_signaller(s))
    log.info("sender ready")
    while True:
        try:
            await sender.run_once()
        except Exception as e:  # keep going through outages
            log.warning("sender loop: %s", type(e).__name__)
        await asyncio.sleep(s.sender_poll_seconds)
