"""S4: a real import, end to end: ImportBatch -> Job workflows -> reads (replay) -> parcel -> ready / needs a look."""

from __future__ import annotations

import uuid

import pytest

from closeout import commands, db
from tests.helpers import stackless as sl

pytestmark = [pytest.mark.slice("S4")]


@pytest.fixture
def env_setup(seeded, tmp_path, monkeypatch):
    sl.use_repo_replay(monkeypatch, tmp_path)
    yield


@pytest.mark.req("R-IMP-1")
@pytest.mark.req("R-IMP-2")
@pytest.mark.req("R-IMP-3")
async def test_demo_import_reaches_ready(dev_env, env_setup) -> None:
    tq = f"tq-{uuid.uuid4()}"
    from closeout.settings import get_settings

    get_settings().task_queue = tq  # type: ignore[misc]
    async with sl.workers(dev_env.client, tq):
        bid = sl.stage_batch()
        out = await sl.run_batch(dev_env.client, tq, bid)
        assert len(out.job_ids) == 12 and out.rejected == 0
        rows = await sl.wait_jobs(bid)
    assert [r["status"] for r in rows] == ["ready"] * 12, [
        (r["job_ref"], r["status"], r["needs_reason"]) for r in rows
    ]
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM jobs WHERE batch_id=:b AND pin IS NOT NULL", b=bid) == 12
        assert (
            db.scalar(
                c,
                "SELECT count(*) FROM facts f JOIN jobs j ON j.id=f.job_id WHERE j.batch_id=:b "
                "AND f.status='kept'",
                b=bid,
            )
            == 12 * 9
        )
        assert (
            db.scalar(
                c,
                "SELECT count(*) FROM facts f JOIN jobs j ON j.id=f.job_id WHERE j.batch_id=:b "
                "AND f.field='contractor_email' AND f.source='registry'",
                b=bid,
            )
            == 12
        )
        # jobs.status always equals what the events say
        from closeout.core.jobs import status_from_events
        from closeout.events import read_stream

        for r in rows:
            assert status_from_events(read_stream(c, f"job-{r['id']}")) == r["status"]
        b = db.one(c, "SELECT status, counts FROM import_batches WHERE id=:b", b=bid)
    assert b["status"] == "done" and b["counts"]["jobs"] == 12
    assert not sl.files.path_of(f"batches/{bid}/import.csv").exists(), (
        "the raw CSV is deleted after the import"
    )


@pytest.mark.req("R-IMP-2")
@pytest.mark.req("R-IMP-4")
@pytest.mark.req("R-OPS-1")
async def test_wrong_address_wrong_total_and_other_contractor_need_a_look(dev_env, env_setup) -> None:
    import csv as csvmod
    import io

    rows = list(csvmod.DictReader(io.StringIO((sl.DEMO / "harbor_pine_jobs.csv").read_text())))[:4]
    rows[0]["job_address"] = "127 Tidewater Rd"  # a neighbour of 126 and 128 that doesn't exist
    rows[1]["total"] = "99999.00"  # the invoice says otherwise
    rows[2]["invoice_file"] = "K1-bayside.png"  # an invoice from another contractor
    buf = io.StringIO()
    w = csvmod.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    files = {p.name: p.read_bytes() for p in (sl.DEMO / "invoices").glob("*.png")}
    tq = f"tq-{uuid.uuid4()}"
    from closeout.settings import get_settings

    get_settings().task_queue = tq  # type: ignore[misc]
    async with sl.workers(dev_env.client, tq):
        bid = sl.stage_batch(csv_bytes=buf.getvalue().encode(), invoice_files=files)
        await sl.run_batch(dev_env.client, tq, bid)
        got = {r["job_ref"]: (r["status"], r["needs_reason"]) for r in await sl.wait_jobs(bid)}
        assert got == {
            "HP-25-001": ("needs_a_look", "address_unmatched"),
            "HP-25-002": ("needs_a_look", "facts_asked"),
            "HP-25-003": ("needs_a_look", "other_contractor"),
            "HP-25-004": ("ready", None),
        }
        with db.tx() as c:
            items = db.all_rows(c, "SELECT kind FROM review_items")
            emails = {
                r["email"]
                for r in db.all_rows(
                    c,
                    "SELECT p.email FROM people p JOIN recipients r ON "
                    "r.person_id=p.id JOIN contractors k ON k.recipient_id=r.id",
                )
            }
            assert "service@baysideph.example" in emails  # the other contractor's stored contact is unchanged
            job1 = db.one(c, "SELECT id FROM jobs WHERE batch_id=:b AND job_ref='HP-25-001'", b=bid)
        assert [i["kind"] for i in items] == ["other_contractor"]

        # A fix made after the batch ended still reaches the job.
        ref = commands.save(
            target_workflow=f"job-{job1['id']}",
            kind="fix_fact",
            payload={"field": "job_address", "value": "14 Osprey Ln"},
            codes={"field": "job_address"},
        )
        out = await commands.send(dev_env.client, f"job-{job1['id']}", ref, wait_seconds=20)
        assert (out.status, out.result.state if out.result else None) == ("done", "ready")
        again = await commands.send(dev_env.client, f"job-{job1['id']}", ref, wait_seconds=20)
        assert again.result == out.result


@pytest.mark.req("R-OPS-1")
async def test_startup_check_finds_jobs_without_a_workflow(dev_env, env_setup) -> None:
    from closeout.startup import jobs_without_workflow

    with db.tx() as c:
        k = db.scalar(c, "SELECT id FROM contractors LIMIT 1")
        orphan = db.scalar(
            c,
            "INSERT INTO jobs (contractor_id, job_ref, status) VALUES (:k, 'X1', 'ready') RETURNING id",
            k=k,
        )
        db.scalar(
            c,
            "INSERT INTO jobs (contractor_id, job_ref, status) VALUES (:k, 'X2', 'dropped') RETURNING id",
            k=k,
        )
    found = await jobs_without_workflow(dev_env.client)
    assert found == [str(orphan)]
    assert await jobs_without_workflow(dev_env.client) == [str(orphan)]
    with db.tx() as c:
        rows = db.all_rows(c, "SELECT kind, reason FROM review_items")
    assert rows == [{"kind": "job_without_workflow", "reason": "workflow_missing"}]
