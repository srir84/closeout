"""Steps run after the seed loads."""

from __future__ import annotations

from sqlalchemy import Connection

from closeout import db


def after_load(conn: Connection) -> None:
    """S2: backfill permit events from the permits table."""
    from closeout.events import permit_events

    rows = db.all_rows(conn, "SELECT pk, row_hash, issued_on, final_on FROM permits ORDER BY pk")
    permit_events(conn, rows)
