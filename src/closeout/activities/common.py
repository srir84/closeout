"""Activities shared by every workflow: command results."""

from __future__ import annotations

from temporalio import activity

from closeout import db
from closeout.activities.safety import scrubbed
from closeout.workflows.payloads import CommandResult


@activity.defn
@scrubbed
def finish_command(result: CommandResult) -> None:
    """Write a command's result to the database, so pages can show it even if the update timed out."""
    with db.tx() as c:
        db.run(
            c,
            """UPDATE commands SET state = CASE WHEN :ok THEN 'done' ELSE 'rejected' END,
                     result = CAST(:result AS jsonb), updated_at = now()
                     WHERE command_id = :id AND state IN ('saved', 'accepted')""",
            ok=result.ok,
            result=result.model_dump_json(),
            id=result.command_id,
        )
