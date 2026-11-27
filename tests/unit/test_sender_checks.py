"""R-OUT-7: demo emails only go to Mailpit, and only to made-up domains."""

import pytest

from closeout.sender.checks import allowed_recipient, config_problems, mailpit_problems, startup_problems
from closeout.settings import Settings

pytestmark = [pytest.mark.slice("S0"), pytest.mark.req("R-OUT-7")]


@pytest.mark.parametrize("host", ["smtp.gmail.com", "mail.example.com", "10.0.0.5", "mailpit.evil.com"])
def test_a_real_mail_server_refuses_startup(host: str) -> None:
    problems = config_problems(Settings(smtp_host=host))
    assert problems and "SMTP_HOST" in problems[0]


@pytest.mark.parametrize("host", ["mailpit", "localhost", "127.0.0.1", "::1"])
def test_local_mail_servers_are_allowed(host: str) -> None:
    assert config_problems(Settings(smtp_host=host)) == []


def test_timeout_must_be_shorter_than_lease() -> None:
    assert config_problems(Settings(smtp_timeout_seconds=30, sender_lease_seconds=30))


def test_mailpit_api_must_answer() -> None:
    s = Settings(mailpit_api_url="http://127.0.0.1:9")  # nothing listens on port 9
    assert mailpit_problems(s) and startup_problems(s)


@pytest.mark.parametrize(
    ("addr", "ok"),
    [
        ("dana@sampleton.example", True),
        ("Clerk@Town.Sampleton.TEST", True),
        ("  pat@harbor.test ", True),
        ("someone@gmail.com", False),  # publish-check: allow (a refused address)
        ("someone@example.com", False),
        ("x@notexample", False),
        ("x@.example", False),
        ("@sampleton.example", False),
        ("a@b@sampleton.example", False),
        ("x@sampleton.example.com", False),  # publish-check: allow
        ("x@example", False),
        ("x@sampleton..example", False),
    ],
)
def test_only_made_up_domains(addr: str, ok: bool) -> None:
    assert allowed_recipient(addr) is ok
