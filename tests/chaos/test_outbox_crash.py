"""S6 chaos: every row of the crash table (design 3.7, R-OUT-2, E3).

A fake SMTP server (aiosmtpd) gives the refusals and stalls Mailpit can't. Fault points stop the sender at exact
protocol places, as if the process died there; a new sender then carries on, like a restart. Each row must end in
its stated state with the stated number of copies delivered. A sender that never sends fails the control row.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter

import pytest
from aiosmtpd.controller import Controller

from closeout import db
from closeout.act import PerformIn, perform
from closeout.sender.loop import Crash, Sender
from closeout.settings import Settings
from tests.helpers.jobs import approve, make_job, row

pytestmark = [pytest.mark.slice("S6"), pytest.mark.req("R-OUT-2")]
LEASE, TIMEOUT = 3.0, 1.0


class Handler:
    """Delivers by default. `rcpt_codes` and `final` script the replies; `stall` never answers the body."""

    def __init__(self) -> None:
        self.copies: Counter[str] = Counter()
        self.rcpt_codes: list[str] = []
        self.final: list[str] = []
        self.stall = False

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: N802
        if self.rcpt_codes:
            return self.rcpt_codes.pop(0)
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        msg_id = next(
            (
                ln.split(":", 1)[1].strip()
                for ln in envelope.content.decode().splitlines()
                if ln.lower().startswith("message-id:")
            ),
            "?",
        )
        self.copies[msg_id] += 1
        if self.stall:
            await asyncio.sleep(TIMEOUT * 4)
        if self.final:
            return self.final.pop(0)
        return "250 Message accepted"


@pytest.fixture
def smtp():
    import socket

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    h = Handler()
    c = Controller(h, hostname="127.0.0.1", port=port)
    c.start()
    yield h, port
    c.stop()


def settings(port: int) -> Settings:
    return Settings(
        smtp_host="127.0.0.1", smtp_port=port, sender_lease_seconds=LEASE, smtp_timeout_seconds=TIMEOUT
    )


def queued(email: str = "owner@sampleton.example"):
    job = make_job(email=email)
    a = approve(job)
    with db.tx() as c:
        out = perform(
            c,
            PerformIn(
                approval_id=a["first"], action="send_email", target=f"job-{job}", round=1, step="first"
            ),
        )
    assert out.ok
    return job, out.outbox_id, a


def faults_at(point: str):
    def f(p: str) -> None:
        if p == point:
            raise Crash(p)

    return f


async def run(s: Settings, faults=None) -> None:
    sender = Sender(s, faults=faults) if faults else Sender(s)
    try:
        await sender.run_once()
    except Crash:
        pass


def state(oid) -> str:
    return row("SELECT state FROM outbox WHERE id = :id", id=oid)["state"]


def copies(h: Handler, oid) -> int:
    mid = row("SELECT message_id FROM outbox WHERE id = :id", id=oid)["message_id"]
    return h.copies[mid]


async def after_lease(s: Settings) -> None:
    await asyncio.sleep(LEASE + 0.5)
    await run(s)


def due_now(oid) -> None:
    with db.tx() as c:
        db.run(c, "UPDATE outbox SET next_attempt_at = now() WHERE id = :id", id=oid)


async def test_control_no_fault(seeded, smtp) -> None:
    h, port = smtp
    _, oid, _ = queued()
    await run(settings(port))
    assert (state(oid), copies(h, oid)) == ("sent", 1)
    await run(settings(port))
    assert copies(h, oid) == 1


async def test_crash_before_the_claim(seeded, smtp) -> None:
    h, port = smtp
    _, oid, _ = queued()
    await run(settings(port), faults_at("before_claim"))
    assert state(oid) == "pending"
    await run(settings(port))
    assert (state(oid), copies(h, oid)) == ("sent", 1)


@pytest.mark.parametrize("point", ["after_claim", "after_rcpt"])
async def test_crash_after_claim_or_rcpt_ends_unknown_with_nothing_sent(seeded, smtp, point: str) -> None:
    h, port = smtp
    _, oid, _ = queued()
    await run(settings(port), faults_at(point))
    assert state(oid) == "sending"
    await after_lease(settings(port))
    assert (state(oid), copies(h, oid)) == ("unknown", 0)
    await run(settings(port))
    assert copies(h, oid) == 0  # unknown is never re-sent automatically


async def test_crash_after_the_body_before_the_final_reply(seeded, smtp) -> None:
    h, port = smtp
    _, oid, _ = queued()
    await run(settings(port), faults_at("after_body"))
    await after_lease(settings(port))
    assert state(oid) == "unknown" and copies(h, oid) in (0, 1)


async def test_no_final_reply_is_unknown(seeded, smtp) -> None:
    h, port = smtp
    h.stall = True
    _, oid, _ = queued()
    await run(settings(port))
    assert state(oid) == "unknown" and copies(h, oid) in (0, 1)


async def test_crash_after_250_before_transaction_2(seeded, smtp) -> None:
    h, port = smtp
    _, oid, _ = queued()
    await run(settings(port), faults_at("after_250"))
    await after_lease(settings(port))
    assert (state(oid), copies(h, oid)) == ("unknown", 1)


async def test_crash_after_transaction_2_before_the_signal(seeded, smtp) -> None:
    h, port = smtp
    _, oid, _ = queued()
    await run(settings(port), faults_at("after_tx2"))
    assert (state(oid), copies(h, oid)) == ("sent", 1)  # the workflow learns by its backup check


async def test_451_then_recovers(seeded, smtp) -> None:
    h, port = smtp
    h.rcpt_codes = ["451 4.7.1 Try again later"]
    _, oid, _ = queued()
    await run(settings(port))
    r = row(
        "SELECT state, attempts, last_error_code, next_attempt_at > now() AS later FROM outbox WHERE id=:i",
        i=oid,
    )
    assert (r["state"], r["attempts"], r["last_error_code"], r["later"]) == ("pending", 1, "rcpt_451", True)
    due_now(oid)
    await run(settings(port))
    assert (state(oid), copies(h, oid)) == ("sent", 1)


async def test_550_fails(seeded, smtp) -> None:
    h, port = smtp
    h.rcpt_codes = ["550 5.1.1 No such user"]
    _, oid, _ = queued()
    await run(settings(port))
    assert (state(oid), copies(h, oid)) == ("failed", 0)


async def test_mail_server_down_then_back(seeded, smtp) -> None:
    h, port = smtp
    _, oid, _ = queued()
    await run(settings(1))  # nothing listens on port 1: refused
    assert state(oid) == "pending"
    due_now(oid)
    await run(settings(port))
    assert (state(oid), copies(h, oid)) == ("sent", 1)


async def test_five_temporary_refusals_then_failed(seeded, smtp) -> None:
    h, port = smtp
    h.rcpt_codes = ["451 later"] * 5
    _, oid, _ = queued()
    for _ in range(5):
        due_now(oid)
        await run(settings(port))
    assert (state(oid), copies(h, oid)) == ("failed", 0)


async def test_two_senders_at_once(seeded, smtp) -> None:
    h, port = smtp
    _, oid, _ = queued()
    await asyncio.gather(run(settings(port)), run(settings(port)), run(settings(port)))
    assert (state(oid), copies(h, oid)) == ("sent", 1)


async def test_worker_killed_inside_perform_after_commit(seeded, smtp) -> None:
    h, port = smtp
    job, oid, a = queued()
    with db.tx() as c:  # the retry of the same command returns the same row
        again = perform(
            c,
            PerformIn(
                approval_id=a["first"], action="send_email", target=f"job-{job}", round=1, step="first"
            ),
        )
    assert again.outbox_id == oid and again.code == "already_done"
    await run(settings(port))
    assert (state(oid), copies(h, oid)) == ("sent", 1)


async def test_decline_while_a_reminder_is_pending(seeded, smtp) -> None:
    from closeout.activities.outbox import StopIn, stop_sending

    h, port = smtp
    job, oid, _ = queued()
    stop_sending(StopIn(job_id=job, reason="decline"))
    await run(settings(port))
    assert (state(oid), copies(h, oid)) == ("cancelled", 0)


@pytest.mark.req("R-OUT-7")
async def test_a_real_looking_address_is_refused_at_send(seeded, smtp) -> None:
    h, port = smtp
    _, oid, _ = queued(email="someone@example.com")  # publish-check: allow (a real domain, refused)
    await run(settings(port))
    r = row("SELECT state, last_error_code FROM outbox WHERE id=:i", i=oid)
    assert (r["state"], r["last_error_code"], sum(h.copies.values())) == ("failed", "bad_domain", 0)


async def test_the_claim_rechecks_the_job(seeded, smtp) -> None:
    h, port = smtp
    job, oid, _ = queued()
    with db.tx() as c:
        db.run(c, "UPDATE jobs SET status = 'stopped' WHERE id = :j", j=job)
    await run(settings(port))
    r = row("SELECT state, cancel_reason FROM outbox WHERE id=:i", i=oid)
    assert (r["state"], r["cancel_reason"], copies(h, oid)) == ("cancelled", "job_not_waiting", 0)


async def test_the_sent_body_is_the_approved_text_with_only_the_links_filled(seeded, smtp) -> None:
    """R-OPS-3 / evals 4.4: every delivered email matches its approval with the two link slots filled."""
    from closeout.core.emails import fill

    h, port = smtp
    job, oid, a = queued()
    got = []

    class Keep(Handler):
        pass

    orig = h.handle_DATA

    async def keep(server, session, envelope):
        got.append(envelope.content.decode())
        return await orig(server, session, envelope)

    h.handle_DATA = keep  # type: ignore[method-assign]
    await run(settings(port))
    approved = row("SELECT body_template FROM approvals WHERE id = :a", a=a["first"])["body_template"]
    import email

    msg = email.message_from_string(got[0])
    body = msg.get_payload(decode=True).decode().replace("\r\n", "\n").rstrip("\n")
    links = [ln for ln in body.split() if ln.startswith("http://127.0.0.1:8000/")]
    assert fill(approved, links[0], links[1]).rstrip("\n") == body
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert time.time() > 0
