"""S9: a homeowner uploads an invoice and asks the contractor to confirm (design 3.20, R-IMP-6)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from closeout import db
from closeout.act import PerformIn, perform
from closeout.app import deps
from closeout.messages import job_messages
from closeout.seed import sid
from tests.helpers.jobs import approve, make_job

pytestmark = [pytest.mark.slice("S9"), pytest.mark.req("R-IMP-6")]
INVOICE = Path(__file__).parents[2] / "data" / "demo" / "invoices" / "HP-25-001.png"


def dana_pin() -> str:
    with db.tx() as c:
        return db.scalar(c, "SELECT pin FROM homes WHERE owner_account_id = :a", a=sid("account:dana"))


def homeowner_job(status: str = "ready") -> uuid.UUID:
    jid = make_job(email=f"x-{uuid.uuid4().hex[:6]}@sampleton.example", status=status)
    with db.tx() as c:
        db.run(c, "UPDATE jobs SET origin = 'homeowner', pin = :p WHERE id = :j", p=dana_pin(), j=jid)
    return jid


def signed_in(who: str) -> tuple[TestClient, str]:
    c = TestClient(
        __import__("closeout.app.main", fromlist=["x"]).create_app(), base_url="http://127.0.0.1:8000"
    )
    c.post("/signin", data={"account_id": str(sid("account:" + who))})
    page = c.get("/h/home" if who in ("dana", "marcus") else "/c/jobs").text
    return c, re.search(r'name="csrf" value="([^"]+)"', page).group(1) if 'name="csrf"' in page else ""


def test_the_upload_makes_a_homeowner_job_and_starts_it(seeded, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    from closeout.settings import get_settings

    get_settings.cache_clear()
    started = []

    class Fake:
        async def start_workflow(self, name, arg, **kw):  # type: ignore[no-untyped-def]
            started.append((name, kw["id"]))

    async def fake_temporal():  # type: ignore[no-untyped-def]
        return Fake()

    monkeypatch.setattr(deps, "temporal", fake_temporal)
    c, csrf = signed_in("dana")
    r = c.post(
        "/h/upload",
        data={
            "csrf": csrf,
            "contractor_id": str(sid("contractor:harbor")),
            "work_type": "kitchen",
            "finish_date": "2025-03-28",
            "total": "48,250.00",
        },
        files={"invoice": ("inv.png", INVOICE.read_bytes(), "image/png")},
    )
    assert r.status_code == 200 and "reading the invoice" in r.text
    with db.tx() as cx:
        j = db.one(cx, "SELECT * FROM jobs WHERE origin = 'homeowner' ORDER BY created_at DESC LIMIT 1")
        home = db.one(cx, "SELECT p.address FROM parcels p WHERE p.pin = :p", p=dana_pin())
        docs = db.scalar(cx, "SELECT count(*) FROM documents WHERE job_id = :j", j=j["id"])
    assert j["address_raw"] == home["address"] and str(j["csv_total"]) == "48250.00" and docs == 1
    assert started == [("Job", f"job-{j['id']}")]
    bad = c.post(
        "/h/upload",
        data={"csrf": csrf, "contractor_id": str(sid("contractor:harbor")), "work_type": "roof"},
        files={"invoice": ("x.png", b"not an image", "image/png")},
    )
    assert "use that file (wrong_type)" in bad.text


def test_the_messages_go_to_the_contractor_on_our_list(seeded) -> None:
    jid = homeowner_job()
    with db.tx() as c:
        msgs = job_messages(c, jid)
        k_rid = db.scalar(c, "SELECT recipient_id FROM contractors WHERE id = :k", k=sid("contractor:harbor"))
    assert [m.step for m in msgs] == ["first", "reminder_1"]
    assert {m.recipient_id for m in msgs} == {k_rid}
    assert {m.to_address for m in msgs} == {"office@harborpine.example"}
    assert "confirm" in msgs[0].subject and "homeowner" in msgs[0].body_template


def test_only_the_homes_owner_can_approve_and_only_two_messages(seeded) -> None:
    jid = homeowner_job()
    for who in ("mike", "dana"):
        ids = approve(jid, account=who, steps=("first", "reminder_1"))
        with db.tx() as c:
            out = perform(
                c,
                PerformIn(
                    approval_id=ids["first"], action="send_email", target=f"job-{jid}", round=1, step="first"
                ),
            )
        assert out.ok == (who == "dana"), (who, out.code)
    assert approve(jid, account="dana", steps=("reminder_2",)) == {}


def test_a_contractor_cant_approve_a_homeowner_job_on_their_page(seeded) -> None:
    jid = homeowner_job()
    c, csrf = signed_in("mike")
    r = c.post(f"/c/approve/{jid}", data={"csrf": csrf, "command_id": str(uuid.uuid4()), "preview": "x"})
    assert "The homeowner sends this one" in r.text


def test_the_contractor_link_page_offers_confirm_and_not_ours(seeded) -> None:
    import hashlib

    jid = homeowner_job(status="asking_contractor")
    token = "k-" + uuid.uuid4().hex
    with db.tx() as cx:
        rid = db.scalar(cx, "SELECT recipient_id FROM contractors WHERE id = :k", k=sid("contractor:harbor"))
        db.run(
            cx,
            """INSERT INTO links (token_hash, purpose, target, recipient_id, round, expires_at)
                      VALUES (:h, 'contractor_confirm', :t, :r, 1, now() + interval '30 days')""",
            h=hashlib.sha256(token.encode()).hexdigest(),
            t=f"job-{jid}",
            r=rid,
        )
    c = TestClient(
        __import__("closeout.app.main", fromlist=["x"]).create_app(), base_url="http://127.0.0.1:8000"
    )
    page = c.get(f"/k/{token}").text
    assert "Did you do this job?" in page and 'value="not_mine"' in page and 'value="correct"' not in page
    assert c.get(f"/a/{token}").status_code == 404  # a contractor link isn't an answer link
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    r = c.post(f"/k/{token}", data={"csrf": csrf, "command_id": str(uuid.uuid4()), "answer": "correct"})
    assert r.status_code == 400


def test_two_homes_can_send_the_same_contractor_an_upload(seeded, monkeypatch, tmp_path) -> None:
    """Review finding: references were numbered per home, but must be unique per contractor."""
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    from closeout.settings import get_settings

    get_settings.cache_clear()

    class Fake:
        async def start_workflow(self, name, arg, **kw):  # type: ignore[no-untyped-def]
            return None

    async def fake_temporal():  # type: ignore[no-untyped-def]
        return Fake()

    monkeypatch.setattr(deps, "temporal", fake_temporal)
    for who in ("dana", "marcus"):
        c, csrf = signed_in(who)
        r = c.post(
            "/h/upload",
            data={"csrf": csrf, "contractor_id": str(sid("contractor:harbor")), "work_type": "roof"},
            files={"invoice": ("inv.png", INVOICE.read_bytes(), "image/png")},
        )
        assert r.status_code == 200 and "reading the invoice" in r.text, who
