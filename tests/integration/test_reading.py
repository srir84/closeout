"""S3: reading a document records the read and the model call (R-OBS-1), and decides facts (R-IMP-3)."""

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from closeout import db
from closeout.activities import reading
from closeout.gateway.core import Gateway, replay_key
from closeout.settings import Settings, get_settings

pytestmark = [pytest.mark.slice("S3")]
GOOD = {
    "contractor_name": "Inlet Roofing Co.",
    "contractor_license": "13VH00077302",
    "contractor_email": "estimates@inletroof.example",
    "customer_name": "Lena Ostrowski",
    "job_address": "11 Sandpiper Dr",
    "work_type": "roof",
    "completion_date": "2025-12-24",
    "total_amount": 19300.0,
    "warranty_years": 10,
}


@pytest.fixture
def replay_env(tmp_path: Path, seeded: str, monkeypatch: pytest.MonkeyPatch):
    rd = tmp_path / "replay"
    (tmp_path / "models.lock").write_text(
        json.dumps({"readers": {"A": {"model": "ma", "digest": "da"}, "B": {"model": "mb", "digest": "db"}}})
    )
    doc = tmp_path / "uploads" / "inv.png"
    doc.parent.mkdir()
    doc.write_bytes(b"\x89PNG fake invoice bytes")
    sha = hashlib.sha256(doc.read_bytes()).hexdigest()
    for r, m, d, out in (("A", "ma", "da", GOOD), ("B", "mb", "db", dict(GOOD, total_amount=19400.0))):
        (rd / r).mkdir(parents=True)
        (rd / r / f"{replay_key(r, m, d, 'invoice', sha)}.json").write_text(
            json.dumps({"text": json.dumps(out), "ms": 4100, "tokens_in": 2200, "tokens_out": 110})
        )
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CLOSEOUT_MODE", "replay")
    get_settings.cache_clear()
    monkeypatch.setattr(reading, "_gateway", Gateway(Settings(closeout_mode="replay"), replay_dir=rd))
    with db.tx() as c:
        contractor = db.scalar(c, "SELECT id FROM contractors WHERE name='Inlet Roofing Co.'")
        job = db.scalar(
            c, "INSERT INTO jobs (contractor_id, job_ref) VALUES (:c, 'r1') RETURNING id", c=contractor
        )
        docid = db.scalar(
            c,
            """INSERT INTO documents (job_id, kind, sha256, path, mime)
                                VALUES (:j, 'invoice', :s, 'inv.png', 'image/png') RETURNING id""",
            j=job,
            s=sha,
        )
    yield job, docid
    get_settings.cache_clear()


@pytest.mark.req("R-OBS-1")
def test_every_model_call_is_logged(replay_env) -> None:
    job, docid = replay_env
    for r in ("A", "B"):
        out = reading.read_document(reading.ReadIn(document_id=docid, reader=r))
        assert out.valid
    with db.tx() as c:
        calls = db.all_rows(
            c,
            "SELECT reader, provider, model, model_digest, ms, tokens_in, tokens_out, cost_usd "
            "FROM model_calls WHERE document_id=:d ORDER BY reader",
            d=docid,
        )
        reads = db.all_rows(
            c,
            "SELECT reader, model, prompt_version, valid FROM reads WHERE document_id=:d ORDER BY reader",
            d=docid,
        )
    assert [
        (
            x["reader"],
            x["provider"],
            x["model"],
            x["model_digest"],
            x["ms"],
            x["tokens_in"],
            x["tokens_out"],
            float(x["cost_usd"]),
        )
        for x in calls
    ] == [
        ("A", "replay", "ma", "da", 4100, 2200, 110, 0.0),
        ("B", "replay", "mb", "db", 4100, 2200, 110, 0.0),
    ]
    assert [(x["reader"], x["prompt_version"], x["valid"]) for x in reads] == [
        ("A", "invoice_v1", True),
        ("B", "invoice_v1", True),
    ]


@pytest.mark.req("R-IMP-3")
def test_facts_kept_only_when_both_reads_agree(replay_env) -> None:
    job, docid = replay_env
    for r in ("A", "B"):
        reading.read_document(reading.ReadIn(document_id=docid, reader=r))
    out = reading.decide_facts(reading.FactsIn(job_id=job, document_id=docid))
    assert (out.kept, out.asked, out.unconfirmed) == (8, 1, 0)
    with db.tx() as c:
        f = {r["field"]: r for r in db.all_rows(c, "SELECT * FROM facts WHERE job_id=:j", j=job)}
    assert f["total_amount"]["status"] == "asked" and f["total_amount"]["value"] is None
    assert (f["total_amount"]["value_a"], f["total_amount"]["value_b"]) == ("19300.00", "19400.00")
    assert f["completion_date"]["value"] == "2025-12-24" and f["completion_date"]["source"] == "both_reads"


@pytest.mark.req("R-IMP-3")
def test_light_mode_keeps_nothing_without_a_person(replay_env, monkeypatch: pytest.MonkeyPatch) -> None:
    job, docid = replay_env
    reading.read_document(reading.ReadIn(document_id=docid, reader="A"))
    monkeypatch.setenv("CLOSEOUT_MODE", "light")
    get_settings.cache_clear()
    out = reading.decide_facts(reading.FactsIn(job_id=job, document_id=docid))
    assert (out.kept, out.unconfirmed) == (0, 9)
    with db.tx() as c:
        # Nothing a model read is kept. The contractor's details come from our list, not the read.
        assert (
            db.scalar(
                c,
                "SELECT count(*) FROM facts WHERE job_id=:j AND status='kept' AND source<>'registry'",
                j=job,
            )
            == 0
        )
        assert db.scalar(c, "SELECT count(*) FROM facts WHERE job_id=:j AND status='unconfirmed'", j=job) == 6


def test_a_replay_miss_is_not_retried(replay_env, tmp_path: Path) -> None:
    from temporalio.exceptions import ApplicationError

    job, _ = replay_env
    with db.tx() as c:
        other = db.scalar(
            c,
            """INSERT INTO documents (job_id, kind, sha256, path, mime)
                                VALUES (:j, 'invoice', 'x', 'inv.png', 'image/png') RETURNING id""",
            j=job,
        )
    (tmp_path / "uploads" / "inv.png").write_bytes(b"different bytes")
    with pytest.raises(ApplicationError) as e:
        reading.read_document(reading.ReadIn(document_id=other, reader="A"))
    assert e.value.type == "ReplayMiss" and e.value.non_retryable
    assert uuid.UUID(str(other))
