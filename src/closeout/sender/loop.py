"""Sender loop. S0 stub; replaced in S6."""

from __future__ import annotations

import asyncio
import logging

from closeout.settings import Settings

log = logging.getLogger("closeout.sender")


async def run_forever(s: Settings) -> None:
    log.info("sender ready (stub)")
    while True:
        await asyncio.sleep(s.sender_poll_seconds)
