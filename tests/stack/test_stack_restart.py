"""S0 stack proofs: restart keeps waiting workflows and mail (R-RUN-6); reset wipes everything (R-RUN-4).

These drive the real stack: `make up` first. They stop and start it.
"""

from __future__ import annotations

import re
import time
import uuid

import pytest

from tests.stack import stack

pytestmark = [pytest.mark.stack, pytest.mark.slice("S0")]


@pytest.fixture(scope="module", autouse=True)
def stack_up() -> None:
    stack.make("up")
    stack.wait_app()


@pytest.mark.req("R-RUN-6")
def test_down_and_up_keeps_a_waiting_workflow_and_mail() -> None:
    subject = f"restart probe {uuid.uuid4().hex[:8]}"
    stack.send_probe_email(subject)
    c, csrf = stack.signed_in()
    scale = float(c.get("/clock").json()["scale"])
    wait_virtual = 45 * scale  # 45 real seconds: long enough to outlive the restart
    r = c.post("/skeleton/start", data={"csrf": csrf, "wait_seconds": str(wait_virtual)})
    ping = re.search(r'id="ping-id">([^<]+)<', r.text).group(1)
    wf = f"ping-{ping}"

    stack.make("down")
    stack.make("up")
    stack.wait_app()

    assert stack.mail_count(f'subject:"{subject}"') == 1
    deadline = time.monotonic() + 120
    status = ""
    while time.monotonic() < deadline:
        c2, _ = stack.signed_in()
        status = re.search(r'id="ping-status">([^<]+)<', c2.get(f"/skeleton?ping={ping}").text).group(1)
        if status == "COMPLETED":
            break
        time.sleep(2)
    assert status == "COMPLETED"
    events = stack.workflow_events(wf)
    kinds = [e.get("eventType", "") for e in events]
    assert sum("WORKFLOW_EXECUTION_COMPLETED" in k or k == "WorkflowExecutionCompleted" for k in kinds) == 1
    assert sum("TIMER_FIRED" in k or k == "TimerFired" for k in kinds) == 1


@pytest.mark.req("R-RUN-4")
def test_reset_wipes_temporal_mailpit_uploads_and_keys() -> None:
    stack.send_probe_email("before reset")
    c, csrf = stack.signed_in()
    c.post("/skeleton/start", data={"csrf": csrf, "wait_seconds": "999999999"})
    stack.compose("exec", "-T", "worker", "sh", "-c", "echo x > /app/var/uploads/probe.txt")
    old_key = (stack.ROOT / ".secrets" / "session.key").read_text()

    stack.make("reset")
    stack.wait_app()

    assert stack.mail_count('subject:"before reset"') == 0
    out = stack.temporal_cli("workflow", "count")
    assert re.search(r"\b0\b", out), out
    assert stack.compose("exec", "-T", "worker", "ls", "-A", "/app/var/uploads").stdout.strip() == ""
    assert (stack.ROOT / ".secrets" / "session.key").read_text() != old_key
    c, _ = stack.signed_in()
    assert c.get("/clock").json()["scale"] > 0
