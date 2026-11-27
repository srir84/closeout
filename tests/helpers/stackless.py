"""Run the real workflows and activities against a test database and a dev Temporal server."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from closeout import clockdb, db, files
from closeout.activities.jobs import BatchIn, BatchOut
from closeout.registry import activities, model_activities, workflows

ROOT = Path(__file__).parents[2]
DEMO = ROOT / "data" / "demo"


@contextlib.asynccontextmanager
async def workers(client: Client, task_queue: str):
    """The main worker plus one worker per reader queue, as in production."""
    from closeout.settings import get_settings

    s = get_settings()
    with ThreadPoolExecutor(max_workers=16) as pool:
        ws = [
            Worker(
                client,
                task_queue=task_queue,
                workflows=workflows(),
                activities=activities(client),
                activity_executor=pool,
            )
        ]
        for r in ("A", "B"):
            ws.append(
                Worker(
                    client,
                    task_queue=f"{s.model_task_queue}-{r}",
                    activities=model_activities(),
                    activity_executor=pool,
                    max_concurrent_activities=1,
                )
            )
        async with contextlib.AsyncExitStack() as stack:
            for w in ws:
                await stack.enter_async_context(w)
            yield


def stage_batch(
    account_key: str = "mike", csv_bytes: bytes | None = None, invoice_files: dict[str, bytes] | None = None
) -> uuid.UUID:
    """What the upload page does: check and store the files, save the CSV, add the batch row."""
    from closeout.seed import sid

    csv_bytes = csv_bytes if csv_bytes is not None else (DEMO / "harbor_pine_jobs.csv").read_bytes()
    if invoice_files is None:
        invoice_files = {p.name: p.read_bytes() for p in sorted((DEMO / "invoices").glob("HP-*.png"))}
    batch_id = uuid.uuid4()
    fmap = {}
    for name, data in invoice_files.items():
        chk = files.check(data)
        fmap[name] = (
            {
                "ok": True,
                "path": files.store(data, chk.mime),
                "mime": chk.mime,
                "pages": chk.pages,
                "sha256": chk.sha256,
            }
            if chk.ok and chk.mime
            else {"ok": False, "reason": chk.reason}
        )
    files.store_raw(csv_bytes, f"batches/{batch_id}/import.csv")
    with db.tx() as c:
        db.run(
            c,
            "INSERT INTO import_batches (id, account_id, files) VALUES (:id, :a, CAST(:f AS jsonb))",
            id=batch_id,
            a=sid("account:" + account_key),
            f=json.dumps(fmap),
        )
    return batch_id


async def run_batch(client: Client, task_queue: str, batch_id: uuid.UUID):
    with db.tx() as c:
        clk = clockdb.clock_in(clockdb.read(c))
    return await client.execute_workflow(
        "ImportBatch",
        BatchIn(batch_id=batch_id, clock=clk),
        id=f"import-{batch_id}",
        task_queue=task_queue,
        result_type=BatchOut,
    )


async def wait_jobs(
    batch_id: uuid.UUID, states: tuple[str, ...] = ("ready", "needs_a_look"), timeout: float = 120
):
    end = asyncio.get_event_loop().time() + timeout
    while True:
        with db.tx() as c:
            rows = db.all_rows(
                c,
                "SELECT id, job_ref, status, needs_reason FROM jobs WHERE batch_id=:b ORDER BY job_ref",
                b=batch_id,
            )
        if rows and all(r["status"] in states for r in rows):
            return rows
        if asyncio.get_event_loop().time() > end:
            raise TimeoutError(str([(r["job_ref"], r["status"]) for r in rows]))
        await asyncio.sleep(0.3)


def use_repo_replay(monkeypatch, tmp_path: Path) -> None:
    """Replay mode with the repo's saved answers, and a temporary uploads folder."""
    from closeout.activities import reading
    from closeout.settings import get_settings

    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CLOSEOUT_MODE", "replay")
    monkeypatch.setenv("REPLAY_DIR", str(ROOT / "evals" / "replay"))
    get_settings.cache_clear()
    monkeypatch.setattr(reading, "_gateway", None)
    shutil.rmtree(tmp_path / "uploads", ignore_errors=True)
