"""Adding and reading events (design 3.9). The only writer of the `events` table."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from closeout import db
from closeout.core.events import Event, parse
from closeout.core.ids import event_id


class SeqClash(Exception):
    """Another step added an event to this stream first. Read again and decide again."""


def _row_to_event(r: dict[str, Any]) -> Event:
    return Event(
        event_id=r["event_id"], stream=r["stream"], seq=r["seq"], kind=r["kind"], payload=r["payload"]
    )


def append_event(
    conn: Connection,
    stream: str,
    expected_seq: int,
    kind: str,
    payload: dict[str, Any],
    event_key: str,
    actor: UUID | None = None,
) -> Event:
    """Add one event after `expected_seq`. A retry with the same key returns the first event and adds nothing."""
    model = parse(kind, payload)
    body = json.loads(model.model_dump_json())
    eid = event_id(event_key)
    existing = db.one(conn, "SELECT * FROM events WHERE event_id = :id", id=eid)
    if existing is not None:
        return _row_to_event(existing)
    try:
        with conn.begin_nested():
            db.run(
                conn,
                """INSERT INTO events (event_id, stream, seq, kind, v, payload, actor_account_id, event_key)
                            VALUES (:id, :s, :q, :k, :v, CAST(:p AS jsonb), :a, :key)
                            ON CONFLICT (event_id) DO NOTHING""",
                id=eid,
                s=stream,
                q=expected_seq + 1,
                k=kind,
                v=model.v,
                p=json.dumps(body),
                a=actor,
                key=event_key,
            )
    except IntegrityError as e:
        if "events_stream_seq_key" in str(e.orig):
            raise SeqClash(f"{stream} seq {expected_seq + 1}") from None
        raise
    row = db.one(conn, "SELECT * FROM events WHERE event_id = :id", id=eid)
    assert row is not None
    return _row_to_event(row)


def last_seq(conn: Connection, stream: str) -> int:
    return int(db.scalar(conn, "SELECT coalesce(max(seq), 0) FROM events WHERE stream = :s", s=stream))


def append_next(
    conn: Connection,
    stream: str,
    kind: str,
    payload: dict[str, Any],
    event_key: str,
    actor: UUID | None = None,
) -> Event:
    """Add an event at the end of the stream, holding a lock on the stream for this transaction."""
    db.scalar(conn, "SELECT pg_advisory_xact_lock(hashtextextended(:s, 0))", s=stream)
    return append_event(conn, stream, last_seq(conn, stream), kind, payload, event_key, actor)


def read_stream(conn: Connection, stream: str) -> list[Event]:
    return [
        _row_to_event(r)
        for r in db.all_rows(conn, "SELECT * FROM events WHERE stream = :s ORDER BY seq", s=stream)
    ]


def permit_events(
    conn: Connection, rows: list[dict[str, Any]], old_final: dict[str, Any] | None = None
) -> int:
    """Permit events for new or changed rows: issued, changed, and final_recorded when a final first appears."""
    old_final = old_final or {}
    n = 0
    for r in rows:
        pk, h = r["pk"], r["row_hash"]
        stream = f"permit-{pk}"
        is_new = pk not in old_final
        kind = "permit.issued" if is_new else "permit.changed"
        body: dict[str, Any] = {"pk": pk}
        if is_new:
            body["issued_on"] = r["issued_on"].isoformat() if r.get("issued_on") else None
        append_next(conn, stream, kind, body, f"permit:{pk}:{kind}:{h}")
        n += 1
        if r.get("final_on") and not old_final.get(pk):
            append_next(
                conn,
                stream,
                "permit.final_recorded",
                {"pk": pk, "final_on": r["final_on"].isoformat()},
                f"permit:{pk}:permit.final_recorded:{h}",
            )
            n += 1
    return n
