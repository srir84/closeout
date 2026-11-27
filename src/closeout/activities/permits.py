"""Permit feed: fetch the whole town, then compare row by row (design 3.8, R-DATA-1).

Nothing is saved unless every page arrived. New rows are inserted, changed rows updated, and rows
that disappeared get `missing_from_source_since` and are kept.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy import Connection
from temporalio import activity

from closeout import db
from closeout.core.permitdata import row_hash
from closeout.seed import insert_permits, link_permits, permit_params

FEED_URL = "https://data.nj.gov/resource/w9se-dmra.json"
PAGE = 1000


class SyncIn(BaseModel):
    town: str
    feed_url: str | None = None


class SyncOut(BaseModel):
    town: str
    rows_seen: int
    rows_new: int
    rows_changed: int
    rows_missing: int
    changed_pks: list[str]
    new_pks: list[str]


class FeedError(Exception):
    pass


def fetch_town(
    town: str, feed_url: str | None = None, client: httpx.Client | None = None, page: int = PAGE
) -> list[dict[str, Any]]:
    """Every row for the town, ordered by pk, paged. Raises if any page fails."""
    url = feed_url or os.environ.get("PERMIT_FEED_URL", FEED_URL)
    own = client is None
    c = client or httpx.Client(timeout=60, headers={"User-Agent": "closeout-demo"})
    rows: list[dict[str, Any]] = []
    try:
        offset = 0
        while True:
            r = c.get(url, params={"treasurycode": town, "$order": "pk", "$limit": page, "$offset": offset})
            if r.status_code != 200:
                raise FeedError(f"feed page at offset {offset} answered {r.status_code}")
            batch = r.json()
            if not isinstance(batch, list):
                raise FeedError("feed page is not a list")
            rows += batch
            if len(batch) < page:
                return rows
            offset += page
    finally:
        if own:
            c.close()


def apply_sync(conn: Connection, town: str, rows: list[dict[str, Any]], today: date) -> SyncOut:
    existing = {
        r["pk"]: r
        for r in db.all_rows(
            conn,
            "SELECT pk, row_hash, missing_from_source_since, final_on FROM permits WHERE town = :t",
            t=town,
        )
    }
    seen = {str(r["pk"]): r for r in rows}
    new = [r for pk, r in seen.items() if pk not in existing]
    changed = [r for pk, r in seen.items() if pk in existing and existing[pk]["row_hash"] != row_hash(r)]
    back = [pk for pk, r in seen.items() if pk in existing and existing[pk]["missing_from_source_since"]]
    missing = [pk for pk, e in existing.items() if pk not in seen and e["missing_from_source_since"] is None]
    insert_permits(conn, new, today)
    for r in changed:
        p = permit_params(r, today)
        db.run(
            conn,
            """UPDATE permits SET permit_no=:permit_no, block=:block, lot=:lot, permit_type=:permit_type,
                        trades=:trades, use_group=:use_group, issued_on=:issued_on, final_on=:final_on,
                        final_type=:final_type, cost=:cost, sq_ft=:sq_ft, processed_on=:processed_on,
                        row_hash=:row_hash, raw=CAST(:raw AS jsonb), bad_fields=:bad_fields, updated_at=now(),
                        missing_from_source_since=NULL WHERE pk=:pk""",
            **p,
        )
    if back:
        db.run(conn, "UPDATE permits SET missing_from_source_since=NULL WHERE pk = ANY(:pks)", pks=back)
    if missing:
        db.run(conn, "UPDATE permits SET missing_from_source_since=now() WHERE pk = ANY(:pks)", pks=missing)
    touched = [str(r["pk"]) for r in new + changed]
    if touched:
        link_permits(conn, touched)
    out = SyncOut(
        town=town,
        rows_seen=len(rows),
        rows_new=len(new),
        rows_changed=len(changed),
        rows_missing=len(missing),
        new_pks=sorted(str(r["pk"]) for r in new),
        changed_pks=sorted(str(r["pk"]) for r in changed),
    )
    db.run(
        conn,
        """INSERT INTO sync_runs (town, rows_seen, rows_new, rows_changed, rows_missing, finished_at)
                    VALUES (:t, :s, :n, :c, :m, now())""",
        t=town,
        s=out.rows_seen,
        n=out.rows_new,
        c=out.rows_changed,
        m=out.rows_missing,
    )
    from closeout import sync_hooks

    sync_hooks.after_sync(
        conn, out, {str(r["pk"]): r for r in new + changed}, {pk: e["final_on"] for pk, e in existing.items()}
    )
    return out


@activity.defn
def sync_permits(inp: SyncIn) -> SyncOut:
    rows = fetch_town(inp.town, inp.feed_url)
    with db.tx() as c:
        today = db.scalar(c, "SELECT (now() AT TIME ZONE 'UTC')::date")
        out = apply_sync(c, inp.town, rows, today)
    activity.logger.info(
        "permit sync %s: %s", inp.town, json.dumps(out.model_dump(exclude={"new_pks", "changed_pks"}))
    )
    return out
