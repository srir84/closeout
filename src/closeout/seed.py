"""Loading the seed: Manasquan's real parcels and permits, and the made-up town Sampleton (design 3.18)."""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import Connection

from closeout import db
from closeout.core import address as addr
from closeout.core.ids import NAMESPACE
from closeout.core.permitdata import clean_permit
from closeout.privacy_hmac import email_hmac

SEED_DIR = Path(__file__).parents[2] / "data" / "seed"

# Tables wiped by a reset (TRUNCATE ... CASCADE). Later slices add theirs.
TABLES = [
    "review_items",
    "model_calls",
    "facts",
    "reads",
    "documents",
    "jobs",
    "import_batches",
    "events",
    "commands",
    "grants",
    "homes",
    "accounts",
    "contractors",
    "recipients",
    "people",
    "sync_runs",
    "permit_parcel",
    "permits",
    "parcels",
    "towns",
]


def seed_dir() -> Path:
    for candidate in (SEED_DIR, Path("/app/data/seed"), Path.cwd() / "data" / "seed"):
        if (candidate / "parcels_1329.csv").exists():
            return candidate
    raise FileNotFoundError("data/seed not found")


def sid(name: str) -> uuid.UUID:
    """Stable IDs for seed rows."""
    return uuid.uuid5(NAMESPACE, "seed:" + name)


def wipe(conn: Connection) -> None:
    existing = {
        r["tablename"] for r in db.all_rows(conn, "SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    tables = [t for t in TABLES if t in existing]
    extra = sorted(existing - set(TABLES) - {"alembic_version", "clock"})
    if tables or extra:
        db.run(conn, "TRUNCATE " + ", ".join(tables + extra) + " CASCADE")


# --- parcels and permits ---------------------------------------------------------------------------


def parcel_rows(path: Path) -> list[dict[str, Any]]:
    out = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            a = addr.parse(r["address"]) if r["address"].strip() else None
            out.append(
                {
                    "pin": r["pin"],
                    "town": r["town"],
                    "block": r["block"],
                    "lot": r["lot"],
                    "qualifier": r["qualifier"],
                    "address": r["address"] or None,
                    "address_norm": a.norm if a else None,
                    "house_no": a.house_no if a else None,
                    "unit": a.unit if a else None,
                    "street": a.street if a else None,
                    "prop_class": r["prop_class"] or None,
                    "year_built": int(r["year_built"]) if r["year_built"] else None,
                    "lat": float(r["lat"]) if r["lat"] else None,
                    "lon": float(r["lon"]) if r["lon"] else None,
                }
            )
    return out


def load_parcels(conn: Connection, rows: list[dict[str, Any]]) -> None:
    if rows:
        db.run_many(
            conn,
            """INSERT INTO parcels (pin, town, block, lot, qualifier, address, address_norm, house_no,
                             unit, street, prop_class, year_built, lat, lon)
                             VALUES (:pin, :town, :block, :lot, :qualifier, :address, :address_norm, :house_no,
                             :unit, :street, :prop_class, :year_built, :lat, :lon)""",
            rows,
        )


def permit_params(row: dict[str, Any], today: date) -> dict[str, Any]:
    p = clean_permit(row, today)
    d = p.model_dump()
    d["raw"] = json.dumps(row, sort_keys=True)
    return d


def insert_permits(conn: Connection, rows: list[dict[str, Any]], today: date) -> None:
    if rows:
        db.run_many(
            conn,
            """INSERT INTO permits (pk, town, permit_no, block, lot, permit_type, trades, use_group,
                             issued_on, final_on, final_type, cost, sq_ft, processed_on, row_hash, raw, bad_fields)
                             VALUES (:pk, :town, :permit_no, :block, :lot, :permit_type, :trades, :use_group,
                             :issued_on, :final_on, :final_type, :cost, :sq_ft, :processed_on, :row_hash,
                             CAST(:raw AS jsonb), :bad_fields)""",
            [permit_params(r, today) for r in rows],
        )


def link_permits(conn: Connection, pks: list[str] | None = None) -> None:
    """Way A of the join differential: match each permit to parcels by block and lot, in SQL."""
    where = "WHERE p.pk = ANY(:pks)" if pks is not None else ""
    db.run(
        conn,
        f"""
        INSERT INTO permit_parcel (permit_pk, pin, status, candidates)
        SELECT p.pk,
               CASE WHEN count(pa.pin) = 1 THEN min(pa.pin) END,
               CASE WHEN count(pa.pin) = 1 THEN 'linked' WHEN count(pa.pin) > 1 THEN 'several' ELSE 'none' END,
               coalesce(array_agg(pa.pin ORDER BY pa.pin) FILTER (WHERE pa.pin IS NOT NULL), '{{}}')
        FROM permits p
        LEFT JOIN parcels pa ON pa.town = p.town AND upper(trim(pa.block)) = upper(trim(p.block))
                            AND upper(trim(pa.lot)) = upper(trim(p.lot))
        {where}
        GROUP BY p.pk
        ON CONFLICT (permit_pk) DO UPDATE SET pin = excluded.pin, status = excluded.status,
                                              candidates = excluded.candidates""",
        pks=pks,
    )


# --- people and the made-up town -------------------------------------------------------------------


def add_person(conn: Connection, key: str, name: str, email: str | None) -> uuid.UUID:
    pid = sid("person:" + key)
    db.run(
        conn,
        "INSERT INTO people (id, display_name, email, email_hmac) VALUES (:id, :n, :e, :h)",
        id=pid,
        n=name,
        e=email,
        h=email_hmac(email) if email else None,
    )
    return pid


def add_recipient(
    conn: Connection, key: str, person_id: uuid.UUID, source: str, entered_by: uuid.UUID | None = None
) -> uuid.UUID:
    rid = sid("recipient:" + key)
    db.run(
        conn,
        "INSERT INTO recipients (id, person_id, source, entered_by) VALUES (:id, :p, :s, :e)",
        id=rid,
        p=person_id,
        s=source,
        e=entered_by,
    )
    return rid


def claim_code_hash(code: str) -> str:
    return hashlib.sha256(("claim:" + code.strip().upper()).encode()).hexdigest()


def load_world(conn: Connection, world: dict[str, Any]) -> None:
    people = {p["key"]: add_person(conn, p["key"], p["name"], p["email"]) for p in world["people"]}
    contractor_ids = {}
    for c in world["contractors"]:
        rid = add_recipient(conn, "contractor:" + c["key"], people[c["person"]], "registry")
        cid = sid("contractor:" + c["key"])
        db.run(
            conn,
            "INSERT INTO contractors (id, name, license, trades, recipient_id) VALUES (:id, :n, :l, :t, :r)",
            id=cid,
            n=c["name"],
            l=c["license"],
            t=c["trades"],
            r=rid,
        )
        contractor_ids[c["key"]] = cid
    clerk_rid = add_recipient(
        conn, "clerk:" + world["town"]["code"], people[world["town"]["clerk"]], "town_setting"
    )
    db.run(
        conn, "UPDATE towns SET clerk_recipient_id = :r WHERE code = :c", r=clerk_rid, c=world["town"]["code"]
    )
    accounts = {}
    for a in world["accounts"]:
        aid = sid("account:" + a["key"])
        db.run(
            conn,
            """INSERT INTO accounts (id, kind, person_id, contractor_id, demo_order)
                        VALUES (:id, :k, :p, :c, :o)""",
            id=aid,
            k=a["kind"],
            p=people[a["person"]],
            c=contractor_ids.get(a.get("contractor", "")),
            o=a["order"],
        )
        accounts[a["key"]] = aid
    for g in world["grants"]:
        db.run(
            conn,
            "INSERT INTO grants (id, account_id, actions, daily_limit) VALUES (:id, :a, :act, :l)",
            id=sid("grant:" + g["account"]),
            a=accounts[g["account"]],
            act=g["actions"],
            l=g["daily_limit"],
        )
    for h in world["homes"]:
        db.run(
            conn,
            "INSERT INTO homes (pin, owner_account_id, claim_code_hash) VALUES (:p, :o, :c)",
            p=h["pin"],
            o=accounts.get(h["owner"] or ""),
            c=claim_code_hash(h["claim_code"]) if h.get("claim_code") else None,
        )


def load(conn: Connection, today: date | None = None) -> None:
    if today is None:
        today = db.scalar(conn, "SELECT (now() AT TIME ZONE 'UTC')::date")
        assert isinstance(today, date)
    d = seed_dir()
    db.run(
        conn,
        """INSERT INTO towns (code, name, synthetic, feed_filter) VALUES
                    ('1329', 'Manasquan', false, 'treasurycode=''1329'''),
                    ('9999', 'Sampleton', true, NULL)""",
    )
    load_parcels(conn, parcel_rows(d / "parcels_1329.csv"))
    load_parcels(conn, parcel_rows(d / "sampleton" / "parcels_9999.csv"))
    insert_permits(conn, json.loads((d / "permits_1329.json").read_text()), today)
    insert_permits(conn, json.loads((d / "sampleton" / "permits_9999.json").read_text()), today)
    link_permits(conn)
    load_world(conn, json.loads((d / "sampleton" / "world.json").read_text()))
    from closeout import seed_hooks

    seed_hooks.after_load(conn)


def world() -> dict[str, Any]:
    return json.loads((seed_dir() / "sampleton" / "world.json").read_text())
