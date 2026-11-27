"""Steps run inside the permit-sync transaction."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from closeout import db


def after_sync(
    conn: Connection, out: Any, rows: dict[str, dict[str, Any]], old_final: dict[str, Any]
) -> None:
    """S2: permit events for new and changed rows. S5 adds the re-check of unmarked jobs."""
    from closeout.events import permit_events

    pks = sorted(rows)
    if not pks:
        return
    fresh = db.all_rows(
        conn, "SELECT pk, row_hash, issued_on, final_on FROM permits WHERE pk = ANY(:p)", p=pks
    )
    permit_events(conn, fresh, {pk: old_final.get(pk) for pk in out.changed_pks})
