"""S0 tool proof: Postgres and Mailpit start from pytest, and tests use their mapped ports."""

import smtplib
import time
from email.message import EmailMessage

import pytest

from closeout import db
from closeout.mailpit import Mailpit

pytestmark = [pytest.mark.slice("S0")]


def test_postgres_from_testcontainers(database: str) -> None:
    with db.tx() as c:
        assert db.scalar(c, "SELECT count(*) FROM clock") == 0
        assert "closeout" not in database or database.split("/")[-1].startswith("t_")


def test_seed_loads_demo_accounts(seeded: str) -> None:
    with db.tx() as c:
        kinds = sorted(r["kind"] for r in db.all_rows(c, "SELECT kind FROM accounts"))
        assert kinds == ["contractor", "contractor", "homeowner", "homeowner", "ops"]
        assert db.scalar(c, "SELECT scale FROM clock") == 1.0


def test_mailpit_from_testcontainers(mailpit: dict) -> None:
    msg = EmailMessage()
    msg["From"] = "closeout@closeout.test"
    msg["To"] = "probe@sampleton.example"
    msg["Subject"] = "container probe"
    msg.set_content("hello")
    with smtplib.SMTP(str(mailpit["host"]), int(mailpit["smtp_port"]), timeout=10) as s:
        s.send_message(msg)
    mp = Mailpit(str(mailpit["api"]))
    for _ in range(50):
        found = mp.search('subject:"container probe"')
        if found:
            break
        time.sleep(0.1)
    assert len(found) == 1
