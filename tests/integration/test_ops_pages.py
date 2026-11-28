"""S12: the ops "needs a look" list shows each item with its reason and its proof, and can resolve it (R-OPS-1)."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from closeout import db
from closeout.app.main import create_app
from closeout.seed import sid
from tests.helpers.jobs import make_job

pytestmark = [pytest.mark.slice("S12"), pytest.mark.req("R-OPS-1")]


def test_the_list_shows_reason_and_proof_and_resolves(seeded) -> None:
    jid = make_job(email=f"x-{uuid.uuid4().hex[:6]}@sampleton.example", status="needs_a_look")
    with db.tx() as c:
        db.run(
            c,
            """INSERT INTO review_items (kind, ref, reason, evidence, dedupe_key)
                     VALUES ('job', :r, 'other_contractor', '{}'::jsonb, :d)""",
            r=f"job-{jid}",
            d=f"t:{jid}",
        )
    ops = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    ops.post("/signin", data={"account_id": str(sid("account:priya"))})
    page = ops.get("/ops").text
    assert "The invoice looks like it&#39;s from a different contractor." in page
    assert f'href="/runs/{jid}"' in page  # the proof: the job's run
    item = re.search(r'action="(/ops/review/[0-9a-f-]+/resolve)"', page).group(1)  # type: ignore[union-attr]
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
    assert "Resolved" in ops.post(item, data={"csrf": csrf}).text
    with db.tx() as c:
        assert db.scalar(c, "SELECT resolved_by FROM review_items WHERE ref = :r", r=f"job-{jid}") == sid(
            "account:priya"
        )
