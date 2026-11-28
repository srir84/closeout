"""The outbox sender service. S0: startup checks only (the outbox arrives in S6)."""

from __future__ import annotations

import asyncio
import logging
import sys

from closeout.sender.checks import startup_problems
from closeout.settings import get_settings

log = logging.getLogger("closeout.sender")


async def main() -> None:
    from closeout import logsetup

    logsetup.setup()
    s = get_settings()
    for _ in range(30):
        problems = startup_problems(s)
        if not problems or not any("not reachable" in p for p in problems):
            break
        await asyncio.sleep(1)
    if problems:
        for p in problems:
            log.error("refusing to start: %s", p)
        sys.exit(2)
    from closeout.sender.loop import run_forever

    await run_forever(s)


if __name__ == "__main__":
    asyncio.run(main())
