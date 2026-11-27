"""The worker: runs workflows and activities, and the command sweep (design 3.5)."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from closeout import commands
from closeout.registry import activities, model_activities, workflows
from closeout.settings import get_settings
from closeout.temporal import connect

log = logging.getLogger("closeout.worker")


async def sweep_forever(client: Client, every: float) -> None:
    while True:
        try:
            n = await commands.sweep(client)
            if n:
                log.info("sweep re-sent %d commands", n)
        except Exception as e:  # keep sweeping through outages
            log.warning("sweep failed: %s", type(e).__name__)
        await asyncio.sleep(every)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = get_settings()
    client = await connect()
    from closeout import startup

    await startup.on_worker_start(client)
    with ThreadPoolExecutor(max_workers=32) as pool, ThreadPoolExecutor(max_workers=2) as model_pool:
        main_worker = Worker(
            client,
            task_queue=s.task_queue,
            workflows=workflows(),
            activities=activities(),
            activity_executor=pool,
            max_concurrent_activities=32,
        )
        # One read at a time per reader: one queue per reader, one slot each (design 3.8).
        runs = [main_worker.run(), sweep_forever(client, s.sweep_seconds)]
        for reader in ("A", "B"):
            acts = model_activities()
            if acts:
                runs.append(
                    Worker(
                        client,
                        task_queue=f"{s.model_task_queue}-{reader}",
                        activities=acts,
                        activity_executor=model_pool,
                        max_concurrent_activities=1,
                    ).run()
                )
        await asyncio.gather(*runs)


if __name__ == "__main__":
    asyncio.run(main())
