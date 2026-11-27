"""Reading and writing the one `clock` row."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Connection

from closeout import db
from closeout.core.clock import Clock
from closeout.workflows.payloads import ClockIn


def read(conn: Connection) -> Clock:
    row = db.one(conn, "SELECT epoch_real, epoch_virtual, scale FROM clock")
    if row is None:
        raise RuntimeError("clock row missing: run `make reset`")
    return Clock(**row)


def reset(conn: Connection, scale: float, epoch_virtual: datetime | None = None) -> Clock:
    """Start the clock now. epoch_virtual defaults to now (so virtual == real at scale 1)."""
    now = db.scalar(conn, "SELECT now()")
    ev = epoch_virtual or now
    db.run(conn, "DELETE FROM clock")
    db.run(
        conn, "INSERT INTO clock (epoch_real, epoch_virtual, scale) VALUES (:r, :v, :s)", r=now, v=ev, s=scale
    )
    return Clock(epoch_real=now, epoch_virtual=ev, scale=scale)


def clock_in(c: Clock) -> ClockIn:
    return ClockIn(epoch_real=c.epoch_real, epoch_virtual=c.epoch_virtual, scale=c.scale)


def virtual_now(conn: Connection) -> datetime:
    c = read(conn)
    now = db.scalar(conn, "SELECT now()")
    return c.virtual(now.astimezone(UTC))
