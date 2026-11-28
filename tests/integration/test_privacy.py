"""S12: a privacy request removes a person everywhere (design 3.17, R-OPS-2)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from closeout import db, privacy
from closeout.act import PerformIn, perform
from closeout.privacy_hmac import email_hmac
from closeout.seed import sid
from tests.helpers.jobs import approve, make_job

pytestmark = [pytest.mark.slice("S12"), pytest.mark.req("R-OPS-2")]
CANARY_NAME = "Zephyrine Quillfeather"


def dump() -> str:
    """Every row of every table, as text: the database dump the canary is searched in."""
    with db.tx() as c:
        tables = [
            r["tablename"]
            for r in db.all_rows(c, "SELECT tablename FROM pg_tables WHERE schemaname='public'")
        ]
        return "\n".join(json.dumps(db.all_rows(c, f'SELECT * FROM "{t}"'), default=str) for t in tables)


@pytest.fixture
def canary(seeded, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    from closeout.settings import get_settings

    get_settings.cache_clear()
    email = f"zq-{uuid.uuid4().hex[:6]}@sampleton.example"
    job = make_job(email=email)
    make_job(email=email)  # review 1: a second job for the same person makes a second identity row
    other = make_job(email="someone.else@sampleton.example")
    (tmp_path / "inv.png").write_bytes(b"\x89PNG " + CANARY_NAME.encode())
    with db.tx() as c:
        db.run(c, "UPDATE people SET display_name = :n WHERE email = :e", n=CANARY_NAME, e=email)
        db.run(
            c,
            "UPDATE facts SET value = :n, value_a = :n, value_b = :n WHERE job_id = :j AND field='customer_name'",
            n=CANARY_NAME,
            j=job,
        )
        doc = db.scalar(
            c,
            "INSERT INTO documents (job_id, kind, sha256, path, mime) VALUES (:j, 'invoice', 'x', "
            "'inv.png', 'image/png') RETURNING id",
            j=job,
        )
        db.run(
            c,
            "INSERT INTO reads (document_id, reader, model, model_digest, prompt_version, output, valid) VALUES "
            "(:d, 'A', 'm', 'g', 'p', CAST(:o AS jsonb), true)",
            d=doc,
            o=json.dumps({"customer_name": CANARY_NAME}),
        )
        db.run(  # review 8: a failed read's note quoting the model's own text
            c,
            "INSERT INTO reads (document_id, reader, purpose, model, model_digest, prompt_version, valid, errors) "
            "VALUES (:d, 'B', 'amount_marks', 'm', 'g', 'p', false, ARRAY[:e])",
            d=doc,
            e=f"extra:{CANARY_NAME}",
        )
        db.run(  # review 2: the person's address printed on another homeowner's job (as a contractor would be)
            c,
            "UPDATE facts SET value = :e, value_a = :e WHERE job_id = :j AND field = 'contractor_email'",
            e=email,
            j=other,
        )
        db.run(
            c,
            "INSERT INTO model_calls (document_id, reader, purpose, provider, model, model_digest, replay_key, "
            "response, ms) VALUES (:d, 'A', 'invoice', 'replay', 'm', 'g', 'k', :r, 1)",
            d=doc,
            r=CANARY_NAME,
        )
        db.run(
            c,
            "INSERT INTO commands (command_id, target_workflow, kind, payload) VALUES (:c, :w, 'answer', "
            "CAST(:p AS jsonb))",
            c=uuid.uuid4(),
            w=f"job-{job}",
            p=json.dumps({"answer": "correct", "note": CANARY_NAME}),
        )
    a = approve(job)
    with db.tx() as c:
        perform(
            c,
            PerformIn(
                approval_id=a["first"], action="send_email", target=f"job-{job}", round=1, step="first"
            ),
        )
    with db.tx() as c:
        pid = privacy.person_by_email(c, email)
    return job, pid, email, doc, tmp_path


def erase(email: str, busy: int | None = None) -> None:
    """The workflow's four steps, in order, in this process."""
    h = email_hmac(email)
    with db.tx() as c:
        privacy.mark(c, privacy.people_by_hmac(c, h), h)
    with db.tx() as c:
        privacy.stop(c, privacy.people_by_hmac(c, h))
        if busy is not None:
            assert privacy.busy(c, privacy.people_by_hmac(c, h)) == busy
    with db.tx() as c:
        privacy.redact(c, privacy.people_by_hmac(c, h))


def test_the_canary_is_everywhere_before_and_nowhere_after(canary) -> None:
    job, pid, email, doc, uploads = canary
    before = dump()
    assert CANARY_NAME in before and email in before
    assert len(privacy_people(email)) == 2
    erase(email, busy=1)  # the undelivered click; the workflow waits for it, then redacts anyway
    after = dump()
    assert CANARY_NAME not in after and email not in after
    assert not any(CANARY_NAME.encode() in p.read_bytes() for p in Path(uploads).rglob("*") if p.is_file())
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM suppression") == 1
        assert db.scalar(c, "SELECT count(*) FROM outbox WHERE state = 'pending' AND job_id = :j", j=job) == 0
        assert db.scalar(
            c, "SELECT bool_and(erased_at IS NOT NULL) FROM people WHERE email_hmac = :h", h=email_hmac(email)
        )
        assert privacy.erased_address(c, email)  # a later import is refused


def test_a_late_model_answer_after_the_marker_stores_blanks(canary) -> None:
    from closeout.activities.reading import store_read
    from closeout.gateway.core import ReadResult

    job, pid, email, doc, _ = canary
    with db.tx() as c:
        privacy.mark(c, [pid], email_hmac(email))
    late = ReadResult(
        reader="B",
        purpose="invoice",
        provider="replay",
        model="m",
        model_digest="g",
        prompt_version="p",
        replay_key="k",
        text=json.dumps({"customer_name": CANARY_NAME}),
        output={"customer_name": CANARY_NAME},
        valid=True,
        errors=[],
        ms=1,
        tokens_in=1,
        tokens_out=1,
        cost_usd=0,
    )
    with db.tx() as c:
        store_read(c, doc, late)
        row = db.one(c, "SELECT output, valid FROM reads WHERE document_id = :d AND reader = 'B'", d=doc)
        resp = db.scalar(c, "SELECT response FROM model_calls WHERE document_id = :d AND reader = 'B'", d=doc)
    assert row["output"] is None and not row["valid"] and CANARY_NAME not in (resp or "")


def test_a_late_correction_after_the_marker_stores_nothing(canary) -> None:
    from closeout.activities import answers
    from closeout.activities.jobs import CommandIn

    job, pid, email, doc, _ = canary
    cid = uuid.uuid4()
    with db.tx() as c:
        db.run(
            c,
            "INSERT INTO commands (command_id, target_workflow, kind, payload) VALUES (:c, :w, 'answer', "
            "CAST(:p AS jsonb))",
            c=cid,
            w=f"job-{job}",
            p=json.dumps({"answer": "correct", "total": "1.00"}),
        )
        privacy.mark(c, [pid], email_hmac(email))
    out = answers.apply_answer(CommandIn(job_id=job, command_id=cid))
    assert (out.ok, out.code) == (False, "bad_correction")


def privacy_people(email: str) -> list[uuid.UUID]:
    with db.tx() as c:
        return privacy.people_by_hmac(c, email_hmac(email))


def test_a_writer_holding_the_check_blocks_the_marker(canary) -> None:
    """Review 3: the erased check and the write share one lock, so the marker can't land between them."""
    job, pid, email, doc, _ = canary
    with db.tx() as writer:
        assert privacy.erased_job(writer, job, lock=True) is False
        with pytest.raises(Exception, match="lock"):
            with db.tx() as eraser:
                db.run(eraser, "SET LOCAL lock_timeout = '300ms'")
                privacy.mark(eraser, [pid], email_hmac(email))


def test_a_contractor_fix_after_erasure_is_refused_and_not_kept(canary) -> None:
    """Review 4: a contractor typing the erased person's name back in."""
    from closeout.activities.jobs import CommandIn, apply_fix

    job, pid, email, doc, _ = canary
    erase(email)
    cid = uuid.uuid4()
    with db.tx() as c:
        db.run(
            c,
            "INSERT INTO commands (command_id, target_workflow, kind, payload) VALUES (:c, :w, 'fix_fact', "
            "CAST(:p AS jsonb))",
            c=cid,
            w=f"job-{job}",
            p=json.dumps({"field": "customer_name", "value": CANARY_NAME}),
        )
    out = apply_fix(CommandIn(job_id=job, command_id=cid))
    assert (out.ok, out.code) == (False, "erased")
    assert CANARY_NAME not in dump()


def test_a_file_another_document_still_uses_is_kept(canary) -> None:
    """Review 5: files are stored by content, and a town letter (no job) may share the erased invoice's file."""
    job, pid, email, doc, uploads = canary
    with db.tx() as c:
        rid = db.scalar(
            c,
            """INSERT INTO town_requests (home_pin, permit_pk, account_id) SELECT pp.pin, pp.permit_pk,
                              :a FROM permit_parcel pp LIMIT 1 RETURNING id""",
            a=sid("account:marcus"),
        )
        db.run(
            c,
            "INSERT INTO documents (town_request_id, kind, sha256, path, mime) VALUES (:t, 'town_reply', "
            "'x', 'inv.png', 'image/png')",
            t=rid,
        )
    erase(email)
    assert (Path(uploads) / "inv.png").exists()


def test_mailpit_is_searched_until_nothing_is_left(monkeypatch) -> None:
    """Review 7: one search returns at most 500 messages."""
    from closeout import mailpit

    left = {"n": 501}

    class Fake:
        def __init__(self, url: str) -> None: ...

        def search(self, q: str) -> list[dict[str, str]]:
            return [{"ID": str(i)} for i in range(min(500, left["n"]))]

        def delete_search(self, q: str) -> None:
            left["n"] -= min(500, left["n"])

    monkeypatch.setattr(mailpit, "Mailpit", Fake)
    assert privacy.mailpit_delete("http://x", ["a@sampleton.example"]) == 501 and left["n"] == 0


def test_an_import_of_an_erased_address_is_refused(canary) -> None:
    job, pid, email, doc, _ = canary
    erase(email)
    from types import SimpleNamespace

    from closeout.activities.jobs import create_jobs_from_rows

    row = SimpleNamespace(line=2, job_ref="Z-1", invoice_file="f.png", homeowner_email=email.upper())
    parsed = SimpleNamespace(rows=[row], rejected=[])
    with db.tx() as c:
        made = create_jobs_from_rows(
            c,
            {"account_id": sid("account:mike"), "id": None, "town": "9999"},
            parsed,
            {"f.png": {"ok": True}},
        )
    assert made == [] and [r.reason for r in parsed.rejected] == ["do_not_contact"]


def test_only_ops_reach_the_ops_pages(seeded) -> None:
    from fastapi.testclient import TestClient

    from closeout.app.main import create_app
    from closeout.seed import sid

    for who, code in (("priya", 200), ("mike", 403), ("dana", 403)):
        c = TestClient(create_app(), base_url="http://127.0.0.1:8000")
        c.post("/signin", data={"account_id": str(sid("account:" + who))})
        for path in ("/ops", "/ops/outbox", "/ops/privacy", "/ops/data"):
            assert c.get(path).status_code == code, (who, path)
