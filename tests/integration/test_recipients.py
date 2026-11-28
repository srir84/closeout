"""R-OUT-6: recipients come only from the recipient list, never from a document."""

import ast
import json
from pathlib import Path

import pytest

from closeout import db
from closeout.messages import job_messages
from tests.helpers.jobs import approve, make_job, row

pytestmark = [pytest.mark.slice("S6"), pytest.mark.req("R-OUT-6")]
ATTACKS = Path(__file__).parents[2] / "evals" / "sets" / "attacks" / "cases.json"


def planted_emails() -> list[str]:
    return sorted(
        {e for c in json.loads(ATTACKS.read_text()) for e in c.get("planted", {}).get("emails", [])}
    )


def test_planted_addresses_never_become_recipients(seeded) -> None:
    planted = planted_emails()
    assert len(planted) >= 10
    job = make_job(email="owner@sampleton.example")
    with db.tx() as c:  # the worst case: a read kept every planted address in every text fact
        for f in ("contractor_email", "customer_name", "job_address"):
            db.run(
                c,
                "UPDATE facts SET value = :v WHERE job_id = :j AND field = :f",
                v=" ".join(planted),
                j=job,
                f=f,
            )
        msgs = job_messages(c, job)
    assert {m.to_address for m in msgs} == {"owner@sampleton.example"}
    a = approve(job)
    for aid in a.values():
        assert (
            row("SELECT to_address FROM approvals WHERE id = :a", a=aid)["to_address"]
            == "owner@sampleton.example"
        )


def test_the_only_source_of_addresses_is_the_recipients_table() -> None:
    """Code check: every address the message builders use is read through recipients -> people."""
    src = (Path(__file__).parents[2] / "src" / "closeout" / "messages.py").read_text()
    assert src.count("FROM recipients r JOIN people p ON p.id = r.person_id") == 1  # job messages
    assert (
        "JOIN recipients r ON r.id = tw.clerk_recipient_id" in src
    )  # town messages: the clerk from town settings
    for chunk in src.split("to_address")[1:]:
        assert "facts" not in chunk.split("\n")[0]
    assert ast.parse(src) is not None
