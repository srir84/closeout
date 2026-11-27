"""The command queue every long-lived workflow shares (design 3.5).

- The validator accepts a command already handled (so a re-sent click is never refused),
  otherwise asks the workflow whether this kind is allowed now.
- The handler queues the command and waits for its result. One loop applies commands in
  order, so two commands never interleave.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from closeout.workflows.payloads import CommandRef, CommandResult


class CommandQueue:
    def __init__(self) -> None:
        self.results: dict[UUID, CommandResult] = {}
        self.queued: list[CommandRef] = []
        self.seen: set[UUID] = set()

    def validate(self, ref: CommandRef, allowed: Callable[[CommandRef], str | None]) -> None:
        if ref.command_id in self.seen:
            return
        reason = allowed(ref)
        if reason is not None:
            raise ValueError(reason)

    async def submit(self, ref: CommandRef) -> CommandResult:
        if ref.command_id not in self.seen:
            self.seen.add(ref.command_id)
            self.queued.append(ref)
        await workflow.wait_condition(lambda: ref.command_id in self.results)
        return self.results[ref.command_id]

    def has_work(self) -> bool:
        return bool(self.queued)

    def pop(self) -> CommandRef:
        return self.queued.pop(0)

    def finish(self, result: CommandResult) -> None:
        self.results[result.command_id] = result
