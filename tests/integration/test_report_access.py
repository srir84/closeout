"""S10: the sale report and the access table (design 3.12, R-REP-1..3, R-REC-4, R-SAFE-2, R-SAFE-3)."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from closeout import db
from closeout.core import report as rp
from closeout.events import append_next
from closeout.seed import sid
from tests.helpers.jobs import make_job

pytestmark = [pytest.mark.slice("S10")]
PRICES = {"visible": "48250.00", "hidden": "21400.00"}


def recorded_job(pin: str, price: str, price_visible: bool) -> uuid.UUID:
    jid = make_job(email=f"o{uuid.uuid4().hex[:6]}@sampleton.example")
    with db.tx() as c:
        db.run(
            c,
            "UPDATE jobs SET pin = :p, status = 'recorded', price_visible = :v WHERE id = :j",
            p=pin,
            v=price_visible,
            j=jid,
        )
        db.run(c, "UPDATE facts SET value = :v WHERE job_id = :j AND field = 'total_amount'", v=price, j=jid)
        db.run(
            c,
            "INSERT INTO documents (job_id, kind, sha256, path, mime) VALUES (:j, 'invoice', 'x', 'none.png', "
            "'image/png')",
            j=jid,
        )
        s = f"job-{jid}"
        cmd = str(uuid.uuid4())
        for kind, p in [
            ("job.imported", {}),
            ("job.ready", {}),
            ("job.approved", {"approval_ids": [], "command_id": cmd}),
            ("job.contractor_confirmed", {"command_id": cmd, "via": "approval"}),
            ("job.answered", {"command_id": cmd, "answer": "confirm"}),
            ("job.homeowner_confirmed", {"command_id": cmd}),
            ("job.recorded", {}),
        ]:
            append_next(c, s, kind, p, f"r:{s}:{kind}")
    return jid


@pytest.fixture
def home(seeded):
    with db.tx() as c:
        pin = db.scalar(c, "SELECT pin FROM homes WHERE owner_account_id = :a", a=sid("account:dana"))
    jobs = {
        "visible": recorded_job(pin, PRICES["visible"], True),
        "hidden": recorded_job(pin, PRICES["hidden"], False),
    }
    return pin, jobs


def browser(account: str | None = None) -> TestClient:
    from closeout.app.main import create_app

    c = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    if account:
        c.post("/signin", data={"account_id": str(sid("account:" + account))})
    return c


def share(role: str, prices: bool, days: int = 14) -> str:
    c = browser("dana")
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/h/share").text).group(1)
    page = c.post(
        "/h/share", data={"csrf": csrf, "role": role, "show_prices": "yes" if prices else "", "days": days}
    )
    return re.search(r'id="share-url" value="http://127.0.0.1:8000(/r/[^"]+)"', page.text).group(1)


def prices_in(html: str) -> set[str]:
    return {p for p, v in (("visible", "48,250.00"), ("hidden", "21,400.00")) if v in html}


@pytest.mark.req("R-REP-1")
def test_the_owner_sees_the_whole_report(home) -> None:
    page = browser("dana").get("/h/report").text
    assert "Home report: 14 OSPREY LN" in page and "Owner" in page
    assert prices_in(page) == {"visible", "hidden"}
    assert "Homeowner confirmed" in page and "Permit history" in page and "13VH01234567" in page


@pytest.mark.req("R-REP-2")
@pytest.mark.parametrize("role", rp.SHARE_ROLES)
@pytest.mark.parametrize("owner_shows_prices", [True, False])
def test_each_role_sees_only_what_the_table_allows(home, role: str, owner_shows_prices: bool) -> None:
    page = browser().get(share(role, owner_shows_prices)).text
    assert "Harbor &amp; Pine Builders" in page and "13VH01234567" in page and "Permit history" in page
    # Price only if the owner allowed it AND the homeowner allowed it when answering.
    assert prices_in(page) == ({"visible"} if owner_shows_prices else set())
    for never in ("@sampleton.example", "Test Owner", "/files/", "none.png"):
        assert never not in page


@pytest.mark.req("R-REP-2")
@pytest.mark.req("R-SAFE-3")
def test_revoked_and_expired_links_show_nothing(home) -> None:
    url = share("buyer", True)
    anon = browser()
    assert anon.get(url).status_code == 200
    with db.tx() as c:
        db.run(c, "UPDATE links SET expires_at = now() WHERE purpose = 'share'")
    r = anon.get(url)
    assert r.status_code == 404 and "48,250" not in r.text
    url2 = share("inspector", True)
    c = browser("dana")
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/h/home").text).group(1)
    with db.tx() as cn:
        lid = db.scalar(
            cn,
            "SELECT id FROM links WHERE purpose='share' AND revoked_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
        )
    c.post(f"/h/share/{lid}/revoke", data={"csrf": csrf})
    assert anon.get(url2).status_code == 404


@pytest.mark.req("R-REP-3")
def test_guessed_and_other_purpose_tokens(home) -> None:
    anon = browser()
    assert anon.get("/r/" + "A" * 43).status_code == 404
    from tests.integration.test_links import link

    tok = link(home[1]["visible"], "answer")
    assert anon.get(f"/r/{tok}").status_code == 404  # an answer link is not a share link


@pytest.mark.req("R-REP-2")
@pytest.mark.parametrize(
    ("who", "status"), [("dana", 200), ("mike", 200), ("pat", 404), ("marcus", 404), ("priya", 404)]
)
def test_invoice_downloads(home, who: str, status: int, tmp_path, monkeypatch) -> None:
    (tmp_path / "none.png").write_bytes(b"\x89PNG fake")
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    from closeout.settings import get_settings

    get_settings.cache_clear()
    with db.tx() as c:
        doc = db.scalar(c, "SELECT id FROM documents WHERE job_id = :j", j=home[1]["visible"])
    assert browser(who).get(f"/files/{doc}").status_code == status
    assert browser().get(f"/files/{doc}").status_code == 401
    assert browser("dana").get(f"/files/{uuid.uuid4()}").status_code == 404


@pytest.mark.req("R-REC-4")
def test_answer_links_give_no_home_access(home) -> None:
    from tests.integration.test_links import link

    a = browser()
    tok = link(home[1]["visible"], "answer")
    a.get(f"/a/{tok}")
    for path in ("/h/home", "/h/report", "/h/share"):
        assert a.get(path).status_code == 401


@pytest.mark.req("R-REC-4")
def test_claim_with_a_letter_code(seeded) -> None:
    from closeout.seed import world

    codes = [h["claim_code"] for h in world()["homes"] if h.get("claim_code")]
    marcus = browser("marcus")
    csrf = re.search(r'name="csrf" value="([^"]+)"', marcus.get("/h/home").text).group(1)
    r = marcus.post("/h/claim", data={"csrf": csrf, "code": "WRONG-0000"})
    assert "didn" in r.text
    r = marcus.post("/h/claim", data={"csrf": csrf, "code": codes[0]})
    assert "now yours" in r.text
    dana = browser("dana")
    csrf2 = re.search(r'name="csrf" value="([^"]+)"', dana.get("/h/home").text).group(1)
    r = dana.post("/h/claim", data={"csrf": csrf2, "code": codes[0]})
    assert "already claimed" in r.text
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM review_items WHERE kind = 'second_claim'") == 1


@pytest.mark.req("R-SAFE-2")
def test_shares_go_through_perform_with_an_audit_row(home) -> None:
    share("buyer", False)
    with db.tx() as c:
        a = db.one(c, "SELECT * FROM audit_log WHERE action = 'share_report'")
        assert a is not None and a["approval_id"] is not None
        assert db.scalar(c, "SELECT used_at IS NOT NULL FROM approvals WHERE id = :a", a=a["approval_id"])
    from pathlib import Path

    src = Path(__file__).parents[2] / "src" / "closeout"
    writers = sorted(str(p.relative_to(src)) for p in src.rglob("*.py") if "'share', :t" in p.read_text())
    assert writers == ["act.py"]


@pytest.mark.req("R-REP-2")
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.sampled_from(rp.SHARE_ROLES), st.booleans(), st.booleans())
def test_a_hidden_price_appears_in_no_rendered_report(role: str, owner: bool, homeowner: bool) -> None:
    from datetime import date
    from decimal import Decimal

    from closeout.app.deps import templates

    job = rp.ReportJob(
        job_id="j",
        work_type="deck",
        finished=date(2025, 1, 1),
        contractor_name="X",
        contractor_license="13VH00000000",
        marks=[],
        warranty_years=None,
        price=Decimal("777777.77"),
        price_visible=homeowner,
    )
    r = rp.build("1 A St", [job], [], role, owner_allows_price=owner)
    html = templates.env.get_template("report.html").render(
        r=r, marks={}, viewer=role, account=None, csrf="", clock={}, request=None
    )
    assert ("777,777.77" in html) == (owner and homeowner)
