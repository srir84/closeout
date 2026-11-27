"""Demo seed data. S0: three made-up people. S1 replaces this with the Sampleton seed."""

from __future__ import annotations

import uuid

from sqlalchemy import Connection

from closeout import db
from closeout.core.ids import NAMESPACE
from closeout.privacy_hmac import email_hmac

# Tables wiped by a reset, children first. Later slices add theirs.
TABLES = ["commands", "accounts", "people"]


def sid(name: str) -> uuid.UUID:
    """Stable IDs for seed rows."""
    return uuid.uuid5(NAMESPACE, "seed:" + name)


def wipe(conn: Connection) -> None:
    existing = {
        r["tablename"] for r in db.all_rows(conn, "SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    tables = [t for t in TABLES if t in existing]
    if tables:
        db.run(conn, "TRUNCATE " + ", ".join(tables) + " CASCADE")


def add_person(conn: Connection, key: str, name: str, email: str | None) -> uuid.UUID:
    pid = sid("person:" + key)
    db.run(
        conn,
        """INSERT INTO people (id, display_name, email, email_hmac) VALUES (:id, :n, :e, :h)""",
        id=pid,
        n=name,
        e=email,
        h=email_hmac(email) if email else None,
    )
    return pid


def add_account(
    conn: Connection,
    key: str,
    kind: str,
    person_id: uuid.UUID,
    order: int,
    contractor_id: uuid.UUID | None = None,
) -> uuid.UUID:
    aid = sid("account:" + key)
    db.run(
        conn,
        """INSERT INTO accounts (id, kind, person_id, contractor_id, demo_order)
                    VALUES (:id, :k, :p, :c, :o)""",
        id=aid,
        k=kind,
        p=person_id,
        c=contractor_id,
        o=order,
    )
    return aid


def load(conn: Connection) -> None:
    add_account(
        conn, "mike", "contractor", add_person(conn, "mike", "Mike Brennan", "mike@harborpine.example"), 1
    )
    add_account(
        conn, "dana", "homeowner", add_person(conn, "dana", "Dana Whitfield", "dana@sampleton.example"), 2
    )
    add_account(conn, "priya", "ops", add_person(conn, "priya", "Priya Natarajan", "priya@closeout.test"), 9)
