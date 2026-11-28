"""S13: the cost and speed page adds up model_calls exactly (R-OBS-3)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from closeout import db
from closeout.app.main import create_app
from closeout.seed import sid

pytestmark = [pytest.mark.slice("S13"), pytest.mark.req("R-OBS-3")]


def test_the_page_numbers_match_model_calls(seeded) -> None:
    with db.tx() as c:
        for ms, err, cost in ((1000, None, 0), (3000, None, 0), (2000, "timeout", 0)):
            db.run(
                c,
                """INSERT INTO model_calls (reader, purpose, provider, model, model_digest, ms, tokens_in,
                         tokens_out, cost_usd, error) VALUES ('A', 'invoice', 'ollama', 'qwen3-vl:4b', 'd', :ms, 100,
                         20, :cost, :err)""",
                ms=ms,
                err=err,
                cost=cost,
            )
        db.run(
            c,
            """INSERT INTO model_calls (reader, purpose, provider, model, model_digest, ms, cost_usd)
                     VALUES ('B', 'invoice', 'hosted', 'some-model', 'd', 500, 0.0125)""",
        )
    cl = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    cl.post("/signin", data={"account_id": str(sid("account:priya"))})
    page = cl.get("/ops/cost").text
    rows = re.findall(
        r"<tr><td>(\w+)</td><td>([^<]+)</td><td>[^<]+</td><td class=\"calls\">(\d+)</td><td>(\d+)</td>"
        r"<td>([\d.]+) s</td>",
        page,
    )
    got = {(p, m): (int(n), int(f), float(med)) for p, m, n, f, med in rows}
    assert got[("ollama", "qwen3-vl:4b")] == (3, 1, 2.0)
    assert got[("hosted", "some-model")] == (1, 0, 0.5)
    assert "$0.0125" in page
    for who in ("mike", "dana"):
        o = TestClient(create_app(), base_url="http://127.0.0.1:8000")
        o.post("/signin", data={"account_id": str(sid("account:" + who))})
        assert o.get("/ops/cost").status_code == 403
