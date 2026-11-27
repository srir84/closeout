"""S1: the seed loads with exact counts (R-DATA-2, R-DATA-3), matches the independent join check
row by row (differential), and real-data counts only count town 1329."""

import pytest
from tools import join_check

from closeout import db

pytestmark = [pytest.mark.slice("S1")]


@pytest.mark.req("R-DATA-2")
def test_exact_parcel_counts(seeded: str) -> None:
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM parcels WHERE town='1329'") == 3982
        assert db.scalar(c, "SELECT count(*) FROM parcels WHERE town='1329' AND address IS NOT NULL") == 3362
        cols = {
            r["column_name"]
            for r in db.all_rows(
                c, "SELECT column_name FROM information_schema.columns WHERE table_name='parcels'"
            )
        }
        assert not cols & {"owner_name", "owner", "mailing_address", "sale_price", "st_address"}


@pytest.mark.req("R-DATA-3")
def test_exact_link_counts(seeded: str) -> None:
    with db.tx() as c:
        rows = db.all_rows(
            c,
            """SELECT pp.status, count(*) n FROM permit_parcel pp JOIN permits p
                                 ON p.pk=pp.permit_pk WHERE p.town='1329' GROUP BY pp.status""",
        )
    assert {r["status"]: r["n"] for r in rows} == {"linked": 2712, "several": 82, "none": 120}


@pytest.mark.req("R-DATA-3")
def test_sql_join_agrees_with_join_check_row_by_row(seeded: str) -> None:
    independent = join_check.statuses()
    with db.tx() as c:
        ours = {
            r["permit_pk"]: r["status"]
            for r in db.all_rows(
                c,
                "SELECT pp.permit_pk, pp.status FROM permit_parcel pp JOIN permits p ON p.pk=pp.permit_pk "
                "WHERE p.town='1329'",
            )
        }
    assert len(ours) == len(independent) == 2914
    diff = {pk: (ours.get(pk), st) for pk, st in independent.items() if ours.get(pk) != st}
    assert diff == {}


@pytest.mark.req("R-DATA-3")
def test_linked_permits_point_at_one_parcel_and_others_keep_candidates(seeded: str) -> None:
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM permit_parcel WHERE status='linked' AND pin IS NULL") == 0
        assert (
            db.scalar(c, "SELECT count(*) FROM permit_parcel WHERE status<>'linked' AND pin IS NOT NULL") == 0
        )
        assert (
            db.scalar(
                c, "SELECT count(*) FROM permit_parcel WHERE status='several' AND cardinality(candidates) < 2"
            )
            == 0
        )


@pytest.mark.req("R-DATA-1")
def test_permits_keyed_on_pk_with_trades(seeded: str) -> None:
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM permits WHERE town='1329'") == 2914
        assert db.scalar(c, "SELECT count(DISTINCT permit_no) FROM permits WHERE town='1329'") < 2914
        assert db.scalar(c, "SELECT count(*) FROM permits WHERE town='1329' AND cardinality(trades)=0") == 280


@pytest.mark.req("R-DATA-6")
def test_real_data_counts_only_count_town_1329(seeded: str) -> None:
    from closeout.app.data_pages import data_numbers

    n = data_numbers()
    assert (n["parcels"], n["parcels_with_address"], n["permits"]) == (3982, 3362, 2914)
    assert (n["linked"], n["several"], n["none"]) == (2712, 82, 120)
    assert n["one_address"] == 2706
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM parcels WHERE town='9999'") > 0
