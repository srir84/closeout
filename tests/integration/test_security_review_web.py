"""S14: fixes from the web-layer security review."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from closeout import db
from closeout.app.main import create_app
from closeout.seed import sid
from tests.helpers.jobs import make_job

pytestmark = [pytest.mark.slice("S14"), pytest.mark.req("R-SAFE-2")]


def client(who: str | None = None) -> TestClient:
    c = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    if who:
        c.post("/signin", data={"account_id": str(sid("account:" + who))})
    return c


def csrf(c: TestClient, path: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)  # type: ignore[union-attr]


def test_a_contractor_cannot_change_a_job_a_homeowner_started(seeded) -> None:
    jid = make_job(email=f"x-{uuid.uuid4().hex[:6]}@sampleton.example", status="needs_a_look")
    with db.tx() as c:
        db.run(c, "UPDATE jobs SET origin = 'homeowner' WHERE id = :j", j=jid)
    mike = client("mike")
    token = csrf(mike, "/c/jobs")
    for path, data in [
        (f"/c/review/{jid}/drop", {}),
        (f"/c/review/{jid}/fix", {"field": "total_amount", "value": "1.00"}),
        (f"/c/review/{jid}/approve", {}),
        (f"/c/jobs/{jid}/undo", {}),
        (f"/c/jobs/{jid}/retract", {}),
        (f"/c/jobs/{jid}/pick", {"pk": "x"}),
    ]:
        r = mike.post(
            path, data={"csrf": token, "command_id": str(uuid.uuid4()), **data}, follow_redirects=False
        )
        assert r.status_code == 404, path
    assert mike.get(f"/c/jobs/{jid}").status_code == 200  # still visible to the contractor


def test_wrong_claim_codes_are_limited(seeded) -> None:
    marcus = client("marcus")
    token = csrf(marcus, "/h/home")
    for i in range(5):
        r = marcus.post("/h/claim", data={"csrf": token, "code": f"SAMPLE-{i:04d}"})
        assert "didn" in r.text
    r = marcus.post(
        "/h/claim", data={"csrf": token, "code": "SAMPLE-4721"}
    )  # right code, but too many misses
    assert "Too many wrong codes" in r.text
    with db.tx() as c:
        assert (
            db.scalar(
                c,
                "SELECT owner_account_id FROM homes WHERE claim_code_hash IS NOT NULL AND "
                "owner_account_id = :a",
                a=sid("account:marcus"),
            )
            is None
        )


def test_only_signed_notices_are_shown(seeded) -> None:
    dana = client("dana")
    assert "Re-verify at evil.example" not in dana.get("/h/home?msg=Re-verify at evil.example").text
    r = dana.post(f"/h/share/{uuid.uuid4()}/revoke", data={"csrf": csrf(dana, "/h/home")})
    assert "ms=" in str(r.url) and 'id="msg"' in r.text  # the site's own notice still shows


def test_a_pdf_is_downloaded_not_shown_inline(seeded, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    from closeout.settings import get_settings

    get_settings.cache_clear()
    (tmp_path / "x.pdf").write_bytes(b"%PDF-1.4 test")
    jid = make_job(email=f"x-{uuid.uuid4().hex[:6]}@sampleton.example", status="recorded")
    with db.tx() as c:
        doc = db.scalar(
            c,
            "INSERT INTO documents (job_id, kind, sha256, path, mime) VALUES (:j, 'invoice', 'x', "
            "'x.pdf', 'application/pdf') RETURNING id",
            j=jid,
        )
    r = client("mike").get(f"/files/{doc}")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")


def test_a_retracted_job_is_hidden_from_the_homeowner(seeded) -> None:
    jid = make_job(email=f"x-{uuid.uuid4().hex[:6]}@sampleton.example", status="retracted")
    with db.tx() as c:
        pin = db.scalar(c, "SELECT pin FROM homes WHERE owner_account_id = :a", a=sid("account:dana"))
        db.run(c, "UPDATE jobs SET pin = :p WHERE id = :j", p=pin, j=jid)
    assert client("dana").get(f"/runs/{jid}").status_code == 404
    assert client("mike").get(f"/runs/{jid}").status_code == 200
