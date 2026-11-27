"""The walking skeleton's trivial workflow: a timer plus an update, using the click protocol."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from closeout.activities import common
    from closeout.workflows.commands import CommandQueue
    from closeout.workflows.payloads import CommandRef, CommandResult, PingIn, PingOut
    from closeout.workflows.util import DB_RETRY, DB_TIMEOUT, to_clock, wait_until


@workflow.defn(name="Ping")
class Ping:
    def __init__(self) -> None:
        self.q = CommandQueue()
        self.state = "waiting"
        self.presses = 0

    def _allowed(self, ref: CommandRef) -> str | None:
        if ref.kind != "press":
            return "unknown_command"
        if self.state != "waiting":
            return "not_waiting"
        return None

    @workflow.update
    async def command(self, ref: CommandRef) -> CommandResult:
        return await self.q.submit(ref)

    @command.validator
    def _validate(self, ref: CommandRef) -> None:
        self.q.validate(ref, self._allowed)

    @workflow.query
    def status(self) -> str:
        return self.state

    @workflow.run
    async def run(self, inp: PingIn) -> PingOut:
        clock = to_clock(inp.clock)
        deadline = workflow.now() + clock.real_wait(timedelta(seconds=inp.wait_virtual_seconds))
        while True:
            await wait_until(lambda: self.q.has_work() or workflow.now() >= deadline, deadline)
            if self.q.has_work():
                ref = self.q.pop()
                self.presses += 1
                result = CommandResult(command_id=ref.command_id, ok=True, code="pressed", state=self.state)
                await workflow.execute_activity(
                    common.finish_command, result, start_to_close_timeout=DB_TIMEOUT, retry_policy=DB_RETRY
                )
                self.q.finish(result)
                continue
            break
        self.state = "finished"
        await workflow.wait_condition(workflow.all_handlers_finished)
        return PingOut(ping_id=inp.ping_id, presses=self.presses, finished_state=self.state)
