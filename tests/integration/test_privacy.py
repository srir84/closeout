"""S12: a privacy request removes a person everywhere (design 3.17, R-OPS-2)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from closeout import db, privacy
from closeout.act import PerformIn, perform
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


def test_the_canary_is_everywhere_before_and_nowhere_after(canary) -> None:
    job, pid, email, doc, uploads = canary
    before = dump()
    assert CANARY_NAME in before and email in before
    with db.tx() as c:
        privacy.mark(c, pid)
        privacy.stop(c, pid)
        assert privacy.busy(c, pid) == 1  # the undelivered click; the workflow waits, then redacts anyway
        privacy.redact(c, pid)
    after = dump()
    assert CANARY_NAME not in after and email not in after
    assert not any(CANARY_NAME.encode() in p.read_bytes() for p in Path(uploads).rglob("*") if p.is_file())
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM suppression") == 1
        assert db.scalar(c, "SELECT count(*) FROM outbox WHERE state = 'pending' AND job_id = :j", j=job) == 0
        assert db.scalar(c, "SELECT erased_at IS NOT NULL FROM people WHERE id = :p", p=pid)


def test_a_late_model_answer_after_the_marker_stores_blanks(canary) -> None:
    from closeout.activities.reading import store_read
    from closeout.gateway.core import ReadResult

    job, pid, email, doc, _ = canary
    with db.tx() as c:
        privacy.mark(c, pid)
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
        privacy.mark(c, pid)
    out = answers.apply_answer(CommandIn(job_id=job, command_id=cid))
    assert (out.ok, out.code) == (False, "bad_correction")


def test_only_ops_reach_the_ops_pages(seeded) -> None:
    from fastapi.testclient import TestClient

    from closeout.app.main import create_app
    from closeout.seed import sid

    for who, code in (("priya", 200), ("mike", 403), ("dana", 403)):
        c = TestClient(create_app(), base_url="http://127.0.0.1:8000")
        c.post("/signin", data={"account_id": str(sid("account:" + who))})
        for path in ("/ops", "/ops/outbox", "/ops/privacy", "/ops/data"):
            assert c.get(path).status_code == code, (who, path)
