"""S6: perform, the one permission check (design 3.6, R-OUT-1, R-SAFE-2, R-OPS-3)."""

from __future__ import annotations

import pytest

from closeout import db
from closeout.act import PerformIn, perform
from tests.helpers.jobs import approve, make_job, row

pytestmark = [pytest.mark.slice("S6")]


def run(job, approvals, step="first", **kw):
    inp = PerformIn(
        approval_id=approvals[step],
        action=kw.pop("action", "send_email"),
        target=kw.pop("target", f"job-{job}"),
        round=kw.pop("round", 1),
        step=kw.pop("step", step),
    )
    with db.tx() as c:
        return perform(c, inp)


@pytest.mark.req("R-OUT-1")
@pytest.mark.req("R-SAFE-2")
def test_a_good_approval_queues_exactly_one_message(seeded) -> None:
    job = make_job()
    a = approve(job)
    out = run(job, a)
    assert (out.ok, out.code) == (True, "queued")
    o = row("SELECT * FROM outbox WHERE id = :id", id=out.outbox_id)
    assert (o["state"], o["round"], o["step"], o["to_address"]) == (
        "pending",
        1,
        "first",
        "owner@sampleton.example",
    )
    assert "{{ANSWER_LINK}}" in o["body_template"]
    assert row("SELECT used_at IS NOT NULL AS u FROM approvals WHERE id = :id", id=a["first"])["u"]
    au = row("SELECT * FROM audit_log WHERE action = 'send_email' AND approval_id = :a", a=a["first"])
    assert au is not None and au["command_id"] == out.command_id


@pytest.mark.req("R-SAFE-2")
def test_the_same_command_twice_gives_one_row_even_after_expiry(seeded) -> None:
    job = make_job()
    a = approve(job)
    first = run(job, a)
    with db.tx() as c:
        db.run(c, "UPDATE approvals SET expires_at = now() - interval '1 day' WHERE id = :id", id=a["first"])
    again = run(job, a)
    assert (again.ok, again.code, again.outbox_id) == (True, "already_done", first.outbox_id)
    assert row("SELECT count(*) AS n FROM outbox WHERE job_id = :j", j=job)["n"] == 1


def test_two_commands_with_one_approval(seeded) -> None:
    job = make_job()
    a = approve(job)
    assert run(job, a).ok
    other = run(job, {"first": a["first"]}, step="first", target=f"job-{job}", round=1)
    assert other.code == "already_done"
    # The same approval used for another step is a different command: already used.
    got = run(job, {"reminder_1": a["first"]}, step="reminder_1")
    assert got.code in ("already_used", "wrong_step")


@pytest.mark.req("R-OUT-1")
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("UPDATE facts SET value = '49000.00' WHERE job_id = :j AND field = 'total_amount'", "body_changed"),
        ("UPDATE jobs SET content_rev = content_rev + 1 WHERE id = :j", "stale_content"),
        ("UPDATE jobs SET work_type = 'bathroom' WHERE id = :j", "subject_changed"),
    ],
)
def test_changed_words_subject_or_content_are_refused(seeded, change: str, reason: str) -> None:
    job = make_job()
    a = approve(job)
    with db.tx() as c:
        db.run(c, change, j=job)
    assert run(job, a).code == reason
    assert row("SELECT count(*) AS n FROM outbox WHERE job_id = :j", j=job)["n"] == 0
    assert (
        row(
            "SELECT details->>'code' AS c FROM audit_log WHERE action='refused' AND approval_id=:a",
            a=a["first"],
        )["c"]
        == reason
    )


@pytest.mark.req("R-OUT-1")
def test_round_step_target_account_and_recipient(seeded) -> None:
    job, other = make_job(), make_job(email="someone@sampleton.example")
    a = approve(job)
    assert run(job, a, round=2).code == "wrong_round"
    assert run(job, {"reminder_1": a["first"]}, step="reminder_1").code == "wrong_step"
    assert run(other, a).code == "wrong_target"
    assert run(job, a, action="share_report").code == "wrong_action"
    # Another contractor's account approving this job: not the owner.
    b = approve(job, account="pat")
    assert run(job, b).code == "not_owner"
    # The approval's recipient isn't this job's homeowner.
    c2 = approve(other)
    with db.tx() as c:
        db.run(
            c,
            "UPDATE approvals SET recipient_id = (SELECT homeowner_recipient_id FROM jobs WHERE id=:o), "
            "to_address = 'someone@sampleton.example' WHERE id = :a",
            o=other,
            a=a["first"],
        )
    assert run(job, a).code == "wrong_recipient"
    assert c2


@pytest.mark.req("R-OUT-1")
def test_expired_reused_voided_unknown(seeded) -> None:
    import uuid

    job = make_job()
    a = approve(job, days=0)
    with db.tx() as c:
        db.run(
            c, "UPDATE approvals SET expires_at = now() - interval '1 second' WHERE id = :id", id=a["first"]
        )
    assert run(job, a).code == "expired"
    b = approve(job)
    with db.tx() as c:
        db.run(c, "UPDATE approvals SET voided_at = now() WHERE id = :id", id=b["first"])
    assert run(job, b).code == "voided"
    assert run(job, {"first": uuid.uuid4()}).code == "unknown_nonce"
    d = approve(job)
    with db.tx() as c:
        db.run(
            c,
            "UPDATE approvals SET used_by_command = gen_random_uuid(), used_at = now() WHERE id = :id",
            id=d["first"],
        )
    assert run(job, d).code == "already_used"


@pytest.mark.req("R-OUT-1")
def test_a_tampered_token_is_refused(seeded) -> None:
    job = make_job()
    a = approve(job)
    with db.tx() as c:
        t = db.scalar(c, "SELECT token FROM approvals WHERE id = :id", id=a["first"])
        db.run(
            c,
            "UPDATE approvals SET token = :t WHERE id = :id",
            t=t[:-2] + ("AA" if t[-2:] != "AA" else "BB"),
            id=a["first"],
        )
    assert run(job, a).code in ("bad_signature", "malformed")


@pytest.mark.req("R-OUT-1")
def test_contact_edited_after_approval_still_goes_to_the_snapshot(seeded) -> None:
    job = make_job(email="first@sampleton.example")
    a = approve(job)
    with db.tx() as c:
        db.run(
            c,
            """UPDATE people SET email = 'changed@sampleton.example' WHERE id = (SELECT person_id FROM recipients
                     WHERE id = (SELECT homeowner_recipient_id FROM jobs WHERE id = :j))""",
            j=job,
        )
    out = run(job, a)
    assert out.ok
    out2 = run(job, a, step="reminder_1")
    assert out2.ok
    tos = {
        r["to_address"]
        for r in (
            row("SELECT to_address FROM outbox WHERE id=:i", i=out.outbox_id),
            row("SELECT to_address FROM outbox WHERE id=:i", i=out2.outbox_id),
        )
    }
    assert tos == {"first@sampleton.example"}


def test_grant_revoked_expired_and_over_limit(seeded) -> None:
    job = make_job()
    a = approve(job)
    with db.tx() as c:
        db.run(
            c,
            "UPDATE grants SET revoked_at = now() WHERE account_id = (SELECT account_id FROM approvals "
            "WHERE id = :a)",
            a=a["first"],
        )
    assert run(job, a).code == "grant_revoked"
    with db.tx() as c:
        db.run(c, "UPDATE grants SET revoked_at = NULL, expires_at = now() - interval '1 day'")
    assert run(job, a).code == "grant_expired"
    with db.tx() as c:
        db.run(c, "UPDATE grants SET expires_at = NULL, daily_limit = 0")
    assert run(job, a).code == "over_limit"
    with db.tx() as c:
        db.run(c, "UPDATE grants SET daily_limit = 200, version = version + 1")
    assert run(job, a).code == "grant_revoked"  # a changed grant version voids older approvals


def test_suppressed_address(seeded) -> None:
    from closeout.privacy_hmac import email_hmac

    job = make_job(email="gone@sampleton.example")
    a = approve(job)
    with db.tx() as c:
        db.run(
            c,
            "INSERT INTO suppression (email_hmac, reason) VALUES (:h, 'unsubscribed')",
            h=email_hmac("Gone@Sampleton.example "),
        )
    assert run(job, a).code == "suppressed"


@pytest.mark.req("R-SAFE-2")
def test_only_perform_writes_to_the_outbox() -> None:
    """Architecture: one door. No other code inserts outbox rows or share links."""
    from pathlib import Path

    src = Path(__file__).parents[2] / "src" / "closeout"
    writers = sorted(
        str(p.relative_to(src)) for p in src.rglob("*.py") if "INSERT INTO outbox" in p.read_text()
    )
    assert writers == ["act.py"]
