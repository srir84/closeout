"""S11: which permits the owner is offered to ask the town about (R-TOWN-1)."""

from datetime import UTC, datetime

import pytest

from closeout import clockdb, db
from closeout.app.town_pages import eligible_permits
from closeout.seed import sid

pytestmark = [pytest.mark.slice("S11"), pytest.mark.req("R-TOWN-1")]


def test_old_permits_with_no_final_approval_on_the_owners_home(seeded) -> None:
    with db.tx() as c:
        clockdb.reset(c, scale=1, epoch_virtual=datetime(2026, 11, 27, tzinfo=UTC))
        pin = db.scalar(c, "SELECT pin FROM homes WHERE owner_account_id = :a", a=sid("account:dana"))
    got = sorted(p["permit_no"] for p in eligible_permits(pin))
    assert got == [
        "21-0912",
        "22-0147",
        "23-0388",
    ]  # the town personas T3, T1, T2; finished permits are not offered


def test_a_permit_newer_than_12_months_is_not_offered(seeded) -> None:
    with db.tx() as c:
        clockdb.reset(c, scale=1, epoch_virtual=datetime(2024, 1, 15, tzinfo=UTC))
        pin = db.scalar(c, "SELECT pin FROM homes WHERE owner_account_id = :a", a=sid("account:dana"))
    assert sorted(p["permit_no"] for p in eligible_permits(pin)) == [
        "21-0912",
        "22-0147",
    ]  # 23-0388 is too new
