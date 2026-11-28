"""Fixes from the independent S6/S7 review: signed text, signed expiry, the unsubscribe lock, snapshot addresses."""

from __future__ import annotations

import threading
import time

import pytest

from closeout import db
from closeout.act import PerformIn, perform
from closeout.app.outreach_pages import unsubscribe_address
from closeout.sender.loop import Sender
from closeout.settings import Settings
from tests.helpers.jobs import approve, make_job, row

pytestmark = [pytest.mark.slice("S6")]


def queue(job, a, step="first"):
    with db.tx() as c:
        return perform(
            c, PerformIn(approval_id=a[step], action="send_email", target=f"job-{job}", round=1, step=step)
        )


@pytest.mark.req("R-OUT-1")
def test_a_tampered_approval_row_cannot_change_the_sent_text(seeded) -> None:
    job = make_job()
    a = approve(job)
    with db.tx() as c:
        db.run(
            c,
            "UPDATE approvals SET subject = 'Pay now', body_template = 'evil {{ANSWER_LINK}} {{UNSUB_LINK}}' "
            "WHERE id = :a",
            a=a["first"],
        )
    out = queue(job, a)
    o = row("SELECT subject, body_template FROM outbox WHERE id = :i", i=out.outbox_id)
    assert o["subject"].startswith("Please confirm") and "evil" not in o["body_template"]


@pytest.mark.req("R-OUT-1")
def test_expiry_is_the_signed_one_not_the_column(seeded) -> None:
    job = make_job()
    a = approve(job, days=0)  # signed to expire now
    with db.tx() as c:
        db.run(c, "UPDATE approvals SET expires_at = now() + interval '10 years' WHERE id = :a", a=a["first"])
    assert queue(job, a).code == "expired"


@pytest.mark.req("R-OUT-5")
def test_unsubscribe_and_claim_are_serialised(seeded) -> None:
    """An unsubscribe that is mid-transaction makes a concurrent claim wait, then see it: nothing is sent."""
    job = make_job(email="race@sampleton.example")
    out = queue(job, approve(job))
    from closeout.privacy_hmac import email_hmac

    started, release = threading.Event(), threading.Event()

    def slow_unsubscribe() -> None:
        with db.tx() as c:
            db.scalar(
                c,
                "SELECT pg_advisory_xact_lock(hashtextextended(:h, 7))",
                h=email_hmac("race@sampleton.example"),
            )
            started.set()
            release.wait(5)
            db.run(
                c,
                "INSERT INTO suppression (email_hmac, reason) VALUES (:h, 'unsubscribed')",
                h=email_hmac("race@sampleton.example"),
            )

    t = threading.Thread(target=slow_unsubscribe)
    t.start()
    started.wait(5)
    result: list = []
    claimer = threading.Thread(target=lambda: result.append(Sender(Settings()).claim(out.outbox_id)))
    claimer.start()
    time.sleep(0.5)
    assert claimer.is_alive(), "the claim must wait for the unsubscribe's lock"
    release.set()
    t.join()
    claimer.join(5)
    assert result == [None]
    assert row("SELECT state, cancel_reason FROM outbox WHERE id=:i", i=out.outbox_id) == {
        "state": "cancelled",
        "cancel_reason": "suppressed",
    }


@pytest.mark.req("R-OUT-5")
def test_unsubscribe_uses_the_address_the_email_went_to(seeded) -> None:
    job = make_job(email="old@sampleton.example")
    out = queue(job, approve(job))
    with db.tx() as c:  # the contact is edited after the email went out
        db.run(
            c,
            """UPDATE people SET email = 'new@sampleton.example' WHERE id = (SELECT person_id FROM recipients
                     WHERE id = (SELECT homeowner_recipient_id FROM jobs WHERE id = :j))""",
            j=job,
        )
    from closeout.app.outreach_pages import unsubscribe_link

    link = {"id": None, "outbox_id": out.outbox_id}
    unsubscribe_link(link)
    from closeout.privacy_hmac import email_hmac

    hs = {r["email_hmac"] for r in [row("SELECT email_hmac FROM suppression")]}
    assert hs == {email_hmac("old@sampleton.example")}
    assert row("SELECT state FROM outbox WHERE id=:i", i=out.outbox_id)["state"] == "cancelled"
    assert unsubscribe_address("old@sampleton.example") == [job]


@pytest.mark.req("R-OUT-1")
@pytest.mark.slice("S7")
def test_only_the_previewed_messages_can_be_approved(seeded) -> None:
    import re

    from fastapi.testclient import TestClient

    from closeout.app.main import create_app
    from closeout.seed import sid

    job = make_job(status="ready")
    c = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    c.post("/signin", data={"account_id": str(sid("account:mike"))})
    page = c.get(f"/c/approve/{job}").text
    assert page.count('class="card message"') == 3
    form = {
        k: re.search(rf'name="{k}" value="([^"]+)"', page).group(1) for k in ("csrf", "command_id", "preview")
    }
    with db.tx() as cn:  # the facts change after the page was shown
        cn.execute(
            __import__("sqlalchemy").text(
                "UPDATE facts SET value = '50000.00' WHERE job_id = :j AND field = 'total_amount'"
            ),
            {"j": job},
        )
    r = c.post(f"/c/approve/{job}", data=form)
    assert "The messages changed" in r.text
    assert row("SELECT count(*) AS n FROM approvals")["n"] == 0
