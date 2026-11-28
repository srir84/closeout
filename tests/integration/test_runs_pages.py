"""S8: the runs view and mark proofs (R-OBS-2, R-REC-2): right people only, no personal details."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from closeout import db
from closeout.act import PerformIn, perform
from closeout.events import append_next
from closeout.seed import sid
from tests.helpers.jobs import approve, make_job

pytestmark = [pytest.mark.slice("S8")]


@pytest.fixture
def job(seeded):
    jid = make_job(email="dana.whitfield@sampleton.example")
    with db.tx() as c:
        dana_pin = db.scalar(c, "SELECT pin FROM homes WHERE owner_account_id = :a", a=sid("account:dana"))
        pk = db.scalar(c, "SELECT permit_pk FROM permit_parcel WHERE pin = :p LIMIT 1", p=dana_pin)
        db.run(c, "UPDATE jobs SET pin = :p WHERE id = :j", p=dana_pin, j=jid)
    a = approve(jid)
    with db.tx() as c:
        perform(
            c,
            PerformIn(
                approval_id=a["first"], action="send_email", target=f"job-{jid}", round=1, step="first"
            ),
        )
        s = f"job-{jid}"
        cmd = uuid.uuid4()
        db.run(
            c,
            "INSERT INTO commands (command_id, target_workflow, kind, refs, result, state) VALUES (:c, :w, "
            "'answer', CAST(:r AS jsonb), CAST(:res AS jsonb), 'done')",
            c=cmd,
            w=s,
            r='{"codes": {"answer": "confirm", "round": "1"}}',
            res='{"code": "confirm"}',
        )
        for kind, p in [
            ("job.imported", {}),
            ("job.ready", {}),
            ("permit.matched", {"pk": pk}),
            ("job.approved", {"approval_ids": [str(a["first"])], "command_id": str(uuid.uuid4())}),
            ("job.contractor_confirmed", {"approval_id": str(a["first"])}),
            ("job.answered", {"command_id": str(cmd), "answer": "confirm"}),
            ("job.homeowner_confirmed", {"command_id": str(cmd)}),
            ("job.recorded", {}),
        ]:
            append_next(c, s, kind, p, f"t:{s}:{kind}")
        db.run(c, "UPDATE jobs SET status = 'recorded' WHERE id = :j", j=jid)
    return jid


def client(account: str) -> TestClient:
    from closeout.app.main import create_app

    c = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    c.post("/signin", data={"account_id": str(sid("account:" + account))})
    return c


def no_personal_details(html: str) -> None:
    text = re.sub(r"<[^>]+>", " ", html)
    assert "@" not in text.replace("@closeout", "")
    assert "$" not in text and "Osprey" not in text and "Test Owner" not in text
    assert "48,250" not in text and "48250" not in text


@pytest.mark.req("R-OBS-2")
@pytest.mark.parametrize(
    ("who", "sees"), [("mike", True), ("priya", True), ("dana", True), ("pat", False), ("marcus", False)]
)
def test_who_can_see_a_run(job, who: str, sees: bool) -> None:
    c = client(who)
    listed = str(job) in c.get("/runs").text
    r = c.get(f"/runs/{job}")
    assert listed is sees and (r.status_code == 200) is sees
    if sees:
        assert "Homeowner confirmed" in r.text and "Recorded" in r.text and "first" in r.text
        no_personal_details(r.text)


@pytest.mark.req("R-OBS-2")
def test_guessed_ids_and_signed_out(job) -> None:
    assert client("pat").get(f"/runs/{uuid.uuid4()}").status_code == 404
    from closeout.app.main import create_app

    anon = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    assert anon.get(f"/runs/{job}").status_code == 401
    assert anon.get(f"/proof/{job}/permit_on_file").status_code == 401


@pytest.mark.req("R-REC-2")
@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("permit_on_file", "construction permit records"),
        ("contractor_confirmed", "approved the emails"),
        ("homeowner_confirmed", "answered through the link"),
    ],
)
def test_each_mark_links_to_its_proof(job, kind: str, text: str) -> None:
    r = client("mike").get(f"/proof/{job}/{kind}")
    assert r.status_code == 200 and text in r.text
    no_personal_details(r.text)
    assert client("pat").get(f"/proof/{job}/{kind}").status_code == 404


@pytest.mark.req("R-REC-2")
def test_a_mark_the_job_does_not_show_has_no_proof(job) -> None:
    with db.tx() as c:
        append_next(c, f"job-{job}", "job.retracted", {}, f"t:{job}:retract")
    assert client("mike").get(f"/proof/{job}/permit_on_file").status_code == 404
