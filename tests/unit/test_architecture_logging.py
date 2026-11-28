"""No service logs request URLs from httpx: Mailpit searches carry email addresses (privacy canary, R-OPS-2)."""

import logging
from pathlib import Path

from closeout import logsetup

SRC = Path(__file__).parents[2] / "src" / "closeout"


def test_httpx_request_logging_is_quiet() -> None:
    logsetup.setup()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_every_service_uses_the_one_setup() -> None:
    direct = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if "logging.basicConfig(" in p.read_text()]
    assert direct == ["logsetup.py"]
