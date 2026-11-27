"""Helpers for tests that drive the real docker compose stack (marker: stack)."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[2]
APP = "http://127.0.0.1:8000"
MAILPIT = "http://127.0.0.1:8025"


def compose(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "deploy/compose.yaml"), *args],
        capture_output=True,
        text=True,
        check=check,
        cwd=ROOT,
        timeout=timeout,
    )


def make(*targets: str, env: dict[str, str] | None = None, timeout: int = 900) -> None:
    import os

    subprocess.run(
        ["make", *targets],
        check=True,
        cwd=ROOT,
        timeout=timeout,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
    )


def wait_app(timeout: float = 120) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if httpx.get(APP + "/healthz", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise TimeoutError("app did not come up")


def temporal_cli(*args: str) -> str:
    return compose("exec", "-T", "temporal", "temporal", *args, "--address", "127.0.0.1:7233").stdout


def workflow_events(workflow_id: str) -> list[dict]:
    out = temporal_cli("workflow", "show", "-w", workflow_id, "-o", "json")
    data = json.loads(out)
    return data.get("events", data if isinstance(data, list) else [])


def signed_in(person_index: int = 0) -> tuple[httpx.Client, str]:
    c = httpx.Client(base_url=APP, follow_redirects=True, timeout=30)
    r = c.get("/")
    ids = re.findall(r'name="account_id" value="([^"]+)"', r.text)
    r = c.post("/signin", data={"account_id": ids[person_index]})
    m = re.search(r'name="csrf" value="([^"]+)"', r.text)
    assert m, "no csrf token on page after sign-in"
    return c, m.group(1)


def send_probe_email(subject: str) -> None:
    code = (
        "import smtplib; from email.message import EmailMessage; m=EmailMessage(); "
        "m['From']='closeout@closeout.test'; m['To']='probe@sampleton.example'; "
        f"m['Subject']={subject!r}; m.set_content('probe'); "
        "s=smtplib.SMTP('mailpit',1025,timeout=10); s.send_message(m); s.quit()"
    )
    compose("exec", "-T", "sender", "python", "-c", code)


def mail_count(query: str) -> int:
    r = httpx.get(MAILPIT + "/api/v1/search", params={"query": query}, timeout=10)
    r.raise_for_status()
    return int(r.json().get("messages_count", 0))
