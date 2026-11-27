"""Fixes from the independent S3/S4 review: no values in errors, registry facts stay, light mode reads once."""

import uuid

import pytest
from temporalio.exceptions import ApplicationError

from closeout import commands, db
from closeout.activities import jobs as acts
from closeout.activities.safety import scrubbed

pytestmark = [pytest.mark.slice("S4")]


def test_errors_carry_a_type_name_only(seeded: str) -> None:
    @scrubbed
    def leaky() -> None:
        with db.tx() as c:
            db.run(c, "INSERT INTO people (id, display_name) VALUES ('not-a-uuid', :n)", n="Canary Person")

    with pytest.raises(ApplicationError) as e:
        leaky()
    assert "Canary" not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__
    assert e.value.type == "StepError" and e.value.message in (
        "DataError",
        "ProgrammingError",
        "StatementError",
    )


@pytest.mark.req("R-IMP-4")
def test_a_person_cannot_type_over_the_contractor_registry(seeded: str) -> None:
    with db.tx() as c:
        k = db.scalar(c, "SELECT id FROM contractors WHERE name='Harbor & Pine Builders'")
        job = db.scalar(c, "INSERT INTO jobs (contractor_id, job_ref) VALUES (:k, 'R1') RETURNING id", k=k)
    for field in acts.REGISTRY_FIELDS:
        ref = commands.save(
            target_workflow=f"job-{job}",
            kind="fix_fact",
            payload={"field": field, "value": "evil@attacker.example"},
        )
        out = acts.apply_fix(acts.CommandIn(job_id=job, command_id=ref.command_id))
        assert (out.ok, out.code) == (False, "registry_field")


@pytest.mark.req("R-IMP-3")
async def test_light_mode_batch_reads_once(dev_env, seeded, tmp_path, monkeypatch) -> None:
    from closeout.settings import get_settings
    from tests.helpers import stackless as sl

    sl.use_repo_replay(monkeypatch, tmp_path)
    tq = f"tq-{uuid.uuid4()}"
    get_settings().task_queue = tq  # type: ignore[misc]
    rows = (sl.DEMO / "harbor_pine_jobs.csv").read_text().splitlines()[:2]
    async with sl.workers(dev_env.client, tq):
        bid = sl.stage_batch(csv_bytes=("\n".join(rows) + "\n").encode())
        with db.tx() as c:
            from closeout import clockdb

            clk = clockdb.clock_in(clockdb.read(c))
        await dev_env.client.execute_workflow(
            "ImportBatch",
            acts.BatchIn(batch_id=bid, clock=clk, light=True),
            id=f"import-{bid}",
            task_queue=tq,
            result_type=acts.BatchOut,
        )
        got = await sl.wait_jobs(bid)
    assert [(r["status"], r["needs_reason"]) for r in got] == [("needs_a_look", "light_review")]
    with db.tx() as c:
        assert db.scalar(c, "SELECT array_agg(DISTINCT reader) FROM model_calls") == ["A"]
