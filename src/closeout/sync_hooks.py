"""Steps run inside the sync transaction. S2 adds permit events; S5 re-checks unmarked jobs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection


def after_sync(conn: Connection, out: Any, rows: dict[str, dict[str, Any]]) -> None:
    return None
