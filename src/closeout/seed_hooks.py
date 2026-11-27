"""Steps run after the seed loads. Later slices add theirs (for example, backfilling permit events)."""

from __future__ import annotations

from sqlalchemy import Connection


def after_load(conn: Connection) -> None:
    return None
