"""S7: links in emails (design 3.11, R-SAFE-3, R-OUT-5, R-OUT-3)."""

from __future__ import annotations

import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient

from closeout import db
from closeout.app.outreach_pages import unsubscribe_recipient
from tests.helpers.jobs import approve, make_job, row

pytestmark = [pytest.mark.slice("S7")]


@pytest.fixture
def client(seeded) -> TestClient:
    from closeout.app.main import create_app

    return TestClient(create_app(), base_url="http://127.0.0.1:8000")


def link(job, purpose: str, round_: int = 1, expires: str = "now() + interval '30 days'") -> str:
    tok = secrets.token_urlsafe(32)
    with db.tx() as c:
        rec = db.scalar(c, "SELECT homeowner_recipient_id FROM jobs WHERE id = :j", j=job)
        db.run(
            c,
            f"""INSERT INTO links (token_hash, purpose, target, round, recipient_id, expires_at)
                      VALUES (:h, :p, :t, :r, :rec, {expires})""",
            h=hashlib.sha256(tok.encode()).hexdigest(),
            p=purpose,
            t=f"job-{job}",
            r=round_,
            rec=rec,
        )
    return tok


def snapshot() -> tuple:
    with db.tx() as c:
        return tuple(
            db.scalar(c, f"SELECT count(*) FROM {t}")
            for t in ("commands", "events", "outbox", "suppression", "audit_log", "links")
        ) + (db.scalar(c, "SELECT string_agg(status || round, ',' ORDER BY id) FROM jobs"),)


@pytest.mark.req("R-SAFE-3")
def test_opening_a_link_changes_nothing(client: TestClient) -> None:
    job = make_job()
    a, u = link(job, "answer"), link(job, "unsubscribe")
    before = snapshot()
    assert "Was this your job?" in client.get(f"/a/{a}").text
    assert "Unsubscribe" in client.get(f"/u/{u}").text
    client.head(f"/a/{a}")
    assert snapshot() == before


@pytest.mark.req("R-SAFE-3")
@pytest.mark.parametrize(
    ("purpose", "route"),
    [("unsubscribe", "a"), ("answer", "u"), ("share", "a"), ("town_reply", "a"), ("contractor_confirm", "u")],
)
def test_a_link_for_one_purpose_is_refused_at_other_routes(
    client: TestClient, purpose: str, route: str
) -> None:
    tok = link(make_job(), purpose)
    r = client.get(f"/{route}/{tok}")
    assert r.status_code == 404 and "isn't valid" in r.text


@pytest.mark.req("R-SAFE-3")
def test_guessed_and_malformed_tokens(client: TestClient) -> None:
    for t in (secrets.token_urlsafe(32), "x", "A" * 200, "..%2F..%2Fetc"):
        assert client.get(f"/a/{t}").status_code == 404


@pytest.mark.req("R-SAFE-3")
def test_expiry_at_exactly_the_boundary(client: TestClient) -> None:
    job = make_job()
    gone = link(job, "answer", expires="now() - interval '1 millisecond'")
    alive = link(job, "answer", expires="now() + interval '2 seconds'")
    assert "has closed" in client.get(f"/a/{gone}").text
    assert "Was this your job?" in client.get(f"/a/{alive}").text


@pytest.mark.req("R-SAFE-3")
def test_an_old_round_link_says_it_was_replaced(client: TestClient) -> None:
    job = make_job()
    old = link(job, "answer", round_=1)
    with db.tx() as c:
        db.run(c, "UPDATE jobs SET round = 2 WHERE id = :j", j=job)
    assert "replaced by a newer one" in client.get(f"/a/{old}").text


@pytest.mark.req("R-SAFE-3")
def test_answer_post_needs_the_link_bound_csrf(client: TestClient) -> None:
    job = make_job()
    tok = link(job, "answer")
    before = snapshot()
    r = client.post(
        f"/a/{tok}",
        data={"answer": "confirm", "command_id": "00000000-0000-0000-0000-000000000001", "csrf": "forged"},
    )
    assert r.status_code == 403 and snapshot() == before


@pytest.mark.req("R-OUT-5")
def test_unsubscribe_cancels_every_queued_message_to_that_address(seeded) -> None:
    from closeout.act import PerformIn, perform

    job, other = make_job(email="pat@sampleton.example"), make_job(email="pat@sampleton.example")
    claimed = None
    for j in (job, other):
        a = approve(j)
        with db.tx() as c:
            o = perform(
                c,
                PerformIn(
                    approval_id=a["first"], action="send_email", target=f"job-{j}", round=1, step="first"
                ),
            )
        claimed = claimed or o.outbox_id
    with db.tx() as c:  # one message is already being handed to the mail server
        db.run(c, "UPDATE outbox SET state = 'sending' WHERE id = :id", id=claimed)
    rec = row("SELECT homeowner_recipient_id AS r FROM jobs WHERE id = :j", j=job)["r"]
    jobs = unsubscribe_recipient(rec)
    assert set(jobs) == {job, other}
    states = sorted(
        r["state"] for r in [row("SELECT state FROM outbox WHERE job_id=:j", j=j) for j in (job, other)]
    )
    assert states == ["cancelled", "sending"]  # the only possible extra message is the one already claimed
    assert row("SELECT count(*) AS n FROM suppression")["n"] == 1
    assert unsubscribe_recipient(rec) == jobs  # pressing again is harmless


@pytest.mark.req("R-OUT-5")
def test_one_click_unsubscribe_post_needs_no_csrf(client: TestClient, monkeypatch) -> None:
    job = make_job(email="click@sampleton.example")
    tok = link(job, "unsubscribe")

    async def no_temporal():
        class C:
            def get_workflow_handle(self, *_a):
                raise RuntimeError("no temporal in this test")

        return C()

    from closeout.app import deps

    monkeypatch.setattr(deps, "temporal", no_temporal)
    monkeypatch.setattr("closeout.commands.send", _fake_send)
    r = client.post(f"/u/{tok}", data={"List-Unsubscribe": "One-Click"})
    assert r.status_code == 200 and "You're unsubscribed" in r.text
    assert row("SELECT count(*) AS n FROM suppression")["n"] == 1


async def _fake_send(client, wf, ref, wait_seconds=5.0):
    from closeout.commands import Outcome

    return Outcome("saved", "saved")
