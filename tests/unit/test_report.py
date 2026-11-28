"""S10: every cell of the access table (design 3.12), written out independently of the code."""

import pytest

from closeout.core import report as rp

pytestmark = [pytest.mark.slice("S10"), pytest.mark.req("R-REP-2")]
Y, N = True, False
# field -> role -> allowed (price rows given separately)
TABLE = {
    "work": dict(owner=Y, buyers_agent=Y, buyer=Y, inspector=Y, contractor=Y, answer_link=Y),
    "contractor": dict(owner=Y, buyers_agent=Y, buyer=Y, inspector=Y, contractor=Y, answer_link=Y),
    "invoice_file": dict(owner=Y, buyers_agent=N, buyer=N, inspector=N, contractor=Y, answer_link=N),
    "town_reply_file": dict(owner=Y, buyers_agent=N, buyer=N, inspector=N, contractor=N, answer_link=N),
    "homeowner_contact": dict(owner=Y, buyers_agent=N, buyer=N, inspector=N, contractor=Y, answer_link=N),
    "permit_history": dict(owner=Y, buyers_agent=Y, buyer=Y, inspector=Y, contractor=Y, answer_link=N),
}


@pytest.mark.parametrize("field", list(TABLE))
@pytest.mark.parametrize("role", rp.ROLES)
def test_cell(field: str, role: str) -> None:
    for o in (True, False):
        for h in (True, False):
            assert rp.allowed(field, role, o, h) is TABLE[field][role]


@pytest.mark.parametrize("role", rp.ROLES)
@pytest.mark.parametrize(("owner", "homeowner"), [(True, True), (True, False), (False, True), (False, False)])
def test_price(role: str, owner: bool, homeowner: bool) -> None:
    want = True if role in ("owner", "contractor", "answer_link") else (owner and homeowner)
    assert rp.allowed("price", role, owner, homeowner) is want


def test_unknown_roles_and_fields_see_nothing() -> None:
    assert not rp.allowed("work", "stranger") and not rp.allowed("secret", "owner")


def test_build_drops_hidden_values() -> None:
    from datetime import date
    from decimal import Decimal

    j = rp.ReportJob(
        job_id="1",
        work_type="roof",
        finished=date(2025, 1, 1),
        contractor_name="X",
        contractor_license="L",
        marks=["homeowner_confirmed"],
        warranty_years=10,
        price=Decimal(5),
        price_visible=False,
    )
    p = rp.ReportPermit(permit_no="1", permit_type="Alteration", trades=[], issued_on=None, final_on=None)
    assert rp.build("a", [j], [p], "buyer", True).jobs[0].price is None
    assert rp.build("a", [j], [p], "owner").jobs[0].price == Decimal(5)
    assert rp.build("a", [j], [p], "answer_link").permits is None
    assert rp.build("a", [j], [p], "inspector").permits == [p]
