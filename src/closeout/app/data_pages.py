"""The data pages: real Manasquan numbers, no people (R-DATA-2, R-DATA-3, R-DATA-5)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from closeout import db
from closeout.app.deps import render

REAL_TOWN = "1329"


def data_numbers(town: str = REAL_TOWN) -> dict[str, Any]:
    with db.tx() as c:
        n = db.one(
            c,
            """SELECT
            (SELECT count(*) FROM parcels WHERE town = :t) AS parcels,
            (SELECT count(*) FROM parcels WHERE town = :t AND address IS NOT NULL) AS parcels_with_address,
            (SELECT count(*) FROM permits WHERE town = :t) AS permits,
            (SELECT count(*) FROM permits WHERE town = :t AND cardinality(trades) = 0) AS permits_no_trades,
            (SELECT count(*) FROM permits WHERE town = :t AND missing_from_source_since IS NOT NULL) AS permits_missing,
            (SELECT count(*) FROM permits WHERE town = :t AND final_on IS NOT NULL) AS permits_final""",
            t=town,
        )
        links = {
            r["status"]: r["n"]
            for r in db.all_rows(
                c,
                """SELECT pp.status, count(*) n FROM permit_parcel pp
                 JOIN permits p ON p.pk = pp.permit_pk WHERE p.town = :t GROUP BY pp.status""",
                t=town,
            )
        }
        one_address = db.scalar(
            c,
            """SELECT count(*) FROM permit_parcel pp JOIN permits p ON p.pk = pp.permit_pk
                                      JOIN parcels pa ON pa.pin = pp.pin
                                      WHERE p.town = :t AND pp.status = 'linked' AND pa.address IS NOT NULL""",
            t=town,
        )
        last = db.one(
            c, "SELECT * FROM sync_runs WHERE town = :t ORDER BY finished_at DESC NULLS LAST LIMIT 1", t=town
        )
        types = db.all_rows(
            c,
            """SELECT permit_type, count(*) n FROM permits WHERE town = :t
                                  GROUP BY permit_type ORDER BY n DESC""",
            t=town,
        )
    assert n is not None
    return {
        **n,
        "linked": links.get("linked", 0),
        "several": links.get("several", 0),
        "none": links.get("none", 0),
        "one_address": one_address,
        "last_sync": last,
        "types": types,
    }


def register(app: FastAPI) -> None:
    @app.get("/data", response_class=HTMLResponse)
    def data_page(request: Request) -> Any:
        return render(request, "data.html", n=data_numbers(), real=True)

    @app.get("/data/feed", response_class=HTMLResponse)
    def data_feed(request: Request, page: int = 1) -> Any:
        page = max(1, min(page, 200))
        with db.tx() as c:
            rows = db.all_rows(
                c,
                """SELECT p.pk, p.permit_no, p.permit_type, p.trades, p.issued_on, p.final_on,
                                            p.final_type, pp.status AS link, pa.address
                                     FROM permits p LEFT JOIN permit_parcel pp ON pp.permit_pk = p.pk
                                     LEFT JOIN parcels pa ON pa.pin = pp.pin AND pp.status = 'linked'
                                     WHERE p.town = :t ORDER BY p.issued_on DESC NULLS LAST, p.pk DESC
                                     LIMIT 50 OFFSET :o""",
                t=REAL_TOWN,
                o=(page - 1) * 50,
            )
        return render(request, "data_feed.html", rows=rows, page=page, real=True)
