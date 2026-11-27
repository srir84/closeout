"""S1: the permit feed (R-DATA-1). A fake API serves the town; each run is a full fetch and compare."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from closeout import db
from closeout.activities.permits import FeedError, apply_sync, fetch_town

pytestmark = [pytest.mark.slice("S1"), pytest.mark.req("R-DATA-1")]
ROWS = json.loads((Path(__file__).parents[2] / "data/seed/permits_1329.json").read_text())
TODAY = date(2026, 11, 27)


class FakeFeed:
    """Serves rows like data.nj.gov: filter by treasurycode, order by pk, $limit/$offset paging."""

    def __init__(self, rows: list[dict], fail_at_offset: int | None = None) -> None:
        self.rows = sorted(rows, key=lambda r: int(r["pk"]))
        self.fail_at = fail_at_offset
        self.requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        q = request.url.params
        off, lim = int(q["$offset"]), int(q["$limit"])
        if self.fail_at is not None and off >= self.fail_at:
            return httpx.Response(503, json={"error": "busy"})
        rows = [r for r in self.rows if r["treasurycode"] == q["treasurycode"]]
        return httpx.Response(200, json=rows[off : off + lim])

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def empty_town(conn) -> None:
    db.run(conn, "DELETE FROM permit_parcel; DELETE FROM permits WHERE town='1329'")


def test_fetch_pages_until_a_short_page() -> None:
    feed = FakeFeed(ROWS)
    got = fetch_town("1329", "http://feed.test/x", client=feed.client(), page=1000)
    assert len(got) == 2914 and feed.requests == 3
    assert [r["pk"] for r in got] == sorted((r["pk"] for r in got), key=int)


def test_a_failed_page_raises_and_nothing_is_saved(seeded: str) -> None:
    feed = FakeFeed(ROWS, fail_at_offset=1000)
    with pytest.raises(FeedError):
        fetch_town("1329", "http://feed.test/x", client=feed.client())
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM sync_runs") == 0


def test_100_then_5_new_1_changed_1_missing(seeded: str) -> None:
    first = copy.deepcopy(ROWS[:100])
    with db.tx() as c:
        empty_town(c)
        out1 = apply_sync(c, "1329", first, TODAY)
    assert (out1.rows_new, out1.rows_changed, out1.rows_missing) == (100, 0, 0)

    second = copy.deepcopy(first)
    gone = second.pop(10)
    target = next(r for r in second if not r.get("certdate"))
    target["certdate"] = "2026-11-20T00:00:00.000"
    target["certtypedesc"] = "Certificate of Approval"
    second += copy.deepcopy(ROWS[100:105])
    with db.tx() as c:
        out2 = apply_sync(c, "1329", second, TODAY)
        assert (out2.rows_seen, out2.rows_new, out2.rows_changed, out2.rows_missing) == (104, 5, 1, 1)
        assert out2.changed_pks == [target["pk"]]
        row = db.one(
            c, "SELECT final_on, missing_from_source_since FROM permits WHERE pk=:pk", pk=target["pk"]
        )
        assert row["final_on"] == date(2026, 11, 20) and row["missing_from_source_since"] is None
        assert db.scalar(
            c, "SELECT missing_from_source_since IS NOT NULL FROM permits WHERE pk=:pk", pk=gone["pk"]
        )
        assert (
            db.scalar(c, "SELECT count(*) FROM permits WHERE town='1329'") == 105
        )  # the missing one is kept
        assert (
            db.scalar(
                c,
                "SELECT count(*) FROM permit_parcel pp JOIN permits p ON p.pk=pp.permit_pk "
                "WHERE p.town='1329'",
            )
            == 105
        )
        runs = db.all_rows(
            c, "SELECT rows_new, rows_changed, rows_missing FROM sync_runs ORDER BY finished_at"
        )
        assert [tuple(r.values()) for r in runs] == [(100, 0, 0), (5, 1, 1)]

    with db.tx() as c:
        out3 = apply_sync(c, "1329", second, TODAY)
    assert (out3.rows_new, out3.rows_changed, out3.rows_missing) == (0, 0, 0)


def test_a_row_that_comes_back_is_unflagged(seeded: str) -> None:
    rows = copy.deepcopy(ROWS[:20])
    with db.tx() as c:
        empty_town(c)
        apply_sync(c, "1329", rows, TODAY)
        apply_sync(c, "1329", rows[1:], TODAY)
        assert db.scalar(
            c, "SELECT missing_from_source_since IS NOT NULL FROM permits WHERE pk=:p", p=rows[0]["pk"]
        )
        out = apply_sync(c, "1329", rows, TODAY)
        assert out.rows_missing == 0
        assert db.scalar(
            c, "SELECT missing_from_source_since IS NULL FROM permits WHERE pk=:p", p=rows[0]["pk"]
        )


def test_sync_of_full_snapshot_changes_nothing(seeded: str) -> None:
    with db.tx() as c:
        out = apply_sync(c, "1329", ROWS, TODAY)
    assert (out.rows_seen, out.rows_new, out.rows_changed, out.rows_missing) == (2914, 0, 0, 0)
