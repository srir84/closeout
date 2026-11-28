"""Fixes from the independent review of S9-S12 (town requests, sharing, downloads, the report)."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from closeout import db
from closeout.act import PerformIn, new_approval, perform
from closeout.messages import town_messages
from closeout.seed import sid

pytestmark = [pytest.mark.slice("S11")]
PERMIT = "999900000013"  # 22-0147 on Dana's home


def client(who: str | None = None) -> TestClient:
    from closeout.app.main import create_app

    c = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    if who:
        c.post("/signin", data={"account_id": str(sid("account:" + who))})
    return c


def town_request(status: str = "approved", **cols: object) -> uuid.UUID:
    with db.tx() as c:
        pin = db.scalar(c, "SELECT pin FROM homes WHERE owner_account_id = :a", a=sid("account:dana"))
        rid = db.scalar(
            c,
            "INSERT INTO town_requests (home_pin, permit_pk, account_id, status) VALUES (:p, :k, :a, :s) RETURNING id",
            p=pin,
            k=PERMIT,
            a=sid("account:dana"),
            s=status,
        )
        for k, v in cols.items():
            db.run(c, f"UPDATE town_requests SET {k} = :v WHERE id = :id", v=v, id=rid)
    return rid


def test_a_town_reply_link_lasts_past_the_60_day_window(seeded) -> None:
    """Review 11: town links expired at 30 days, before the late window closed."""
    from closeout.sender.loop import Sender
    from closeout.settings import Settings

    rid = town_request()
    with db.tx() as c:
        grant = db.one(c, "SELECT * FROM grants WHERE account_id = :a", a=sid("account:dana"))
        m = [m for m in town_messages(c, rid) if m.step == "request"][0]
        aid = new_approval(c, sid("account:dana"), m, grant)
    with db.tx() as c:
        out = perform(
            c, PerformIn(approval_id=aid, action="send_email", target=f"town-{rid}", round=1, step="request")
        )
    assert out.ok, out.code
    Sender(Settings()).claim(out.outbox_id)
    with db.tx() as c:
        days = db.scalar(
            c,
            """SELECT extract(day FROM expires_at - created_at) FROM links
                               WHERE purpose = 'town_reply' AND target = :t""",
            t=f"town-{rid}",
        )
    assert days >= 70


def test_a_permit_waits_until_its_request_has_ended(seeded) -> None:
    """Review 10: no_reply let a second request in while the first could still get a late reply."""
    import sqlalchemy

    first = town_request(status="no_reply")
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        town_request(status="drafted")
    from closeout.app.town_pages import eligible_permits

    with db.tx() as c:
        pin = db.scalar(c, "SELECT home_pin FROM town_requests WHERE id = :i", i=first)
    assert PERMIT not in {p["pk"] for p in eligible_permits(pin)}
    from closeout.activities.town import TownRef, town_ended

    town_ended(TownRef(request_id=first))
    assert PERMIT in {p["pk"] for p in eligible_permits(pin)}
    town_request(status="drafted")  # now allowed


def test_a_definite_answer_is_not_offered_again(seeded) -> None:
    from closeout.app.town_pages import eligible_permits

    rid = town_request(status="replied", result="final_on_file")
    with db.tx() as c:
        db.run(c, "UPDATE town_requests SET ended_at = now() WHERE id = :i", i=rid)
        pin = db.scalar(c, "SELECT home_pin FROM town_requests WHERE id = :i", i=rid)
    assert PERMIT not in {p["pk"] for p in eligible_permits(pin)}


def clerk_link(rid: uuid.UUID) -> str:
    import hashlib

    token = "t-" + uuid.uuid4().hex
    with db.tx() as c:
        clerk = db.scalar(c, "SELECT clerk_recipient_id FROM towns LIMIT 1")
        db.run(
            c,
            """INSERT INTO links (token_hash, purpose, target, recipient_id, round, expires_at)
                     VALUES (:h, 'town_reply', :t, :r, 1, now() + interval '75 days')""",
            h=hashlib.sha256(token.encode()).hexdigest(),
            t=f"town-{rid}",
            r=clerk,
        )
    return f"/t/{token}"


def test_a_closed_request_stores_no_upload(seeded) -> None:
    """Review 14: the file was stored before the request's state was checked."""
    from pathlib import Path

    rid = town_request(status="replied", result="no_record")
    with db.tx() as c:
        db.run(c, "UPDATE town_requests SET ended_at = now() WHERE id = :i", i=rid)
    link = clerk_link(rid)
    c = client()
    page = c.get(link).text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    letter = (Path(__file__).parents[2] / "evals/sets/town_replies/tr01.jpg").read_bytes()
    r = c.post(
        link,
        data={"csrf": csrf.group(1) if csrf else "", "command_id": str(uuid.uuid4())},
        files={"reply": ("l.jpg", letter, "image/jpeg")},
    )
    assert "closed" in r.text.lower() or r.status_code in (403, 404)
    with db.tx() as cx:
        assert db.scalar(cx, "SELECT count(*) FROM documents WHERE town_request_id = :t", t=rid) == 0


@pytest.mark.slice("S10")
def test_the_owner_can_download_a_town_letter_and_others_cant(seeded, tmp_path, monkeypatch) -> None:
    """Review 15: town letters have no job, so the owner got a 404."""
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    from closeout.settings import get_settings

    get_settings.cache_clear()
    (tmp_path / "letter.png").write_bytes(b"\x89PNG letter")
    rid = town_request(status="replied", result="final_on_file")
    with db.tx() as c:
        doc = db.scalar(
            c,
            """INSERT INTO documents (town_request_id, kind, sha256, path, mime)
                              VALUES (:t, 'town_reply', 'x', 'letter.png', 'image/png') RETURNING id""",
            t=rid,
        )
    assert client("dana").get(f"/files/{doc}").status_code == 200
    for who in ("marcus", "mike"):
        assert client(who).get(f"/files/{doc}").status_code == 404


@pytest.mark.slice("S10")
def test_the_report_says_no_reply(seeded) -> None:
    """Review 16: a request that ended with no reply vanished from the report."""
    town_request(status="no_reply")
    page = client("dana").get("/h/report").text
    assert "no reply" in page


@pytest.mark.slice("S10")
def test_a_share_with_an_expired_grant_is_refused(seeded) -> None:
    """Review 12: sharing skipped the grant's expiry."""
    with db.tx() as c:
        db.run(
            c,
            "UPDATE grants SET expires_at = now() - interval '1 minute' WHERE account_id = :a",
            a=sid("account:dana"),
        )
    c = client("dana")
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/h/share").text).group(1)  # type: ignore[union-attr]
    page = c.post("/h/share", data={"csrf": csrf, "role": "buyer", "days": 14}).text
    assert 'id="share-url"' not in page
    with db.tx() as cx:
        assert db.scalar(cx, "SELECT count(*) FROM links WHERE purpose = 'share'") == 0
