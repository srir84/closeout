"""S7: stopping and starting the whole stack while a job waits for a reminder (R-RUN-6).

Runs at the restart-test clock speed (1 day = 60 s), so reminder 1 is due 3 minutes after the first email.
The reminder must still go out exactly once, and Mailpit must still have the first email.
"""

import time

import pytest

from tests.stack import flows, stack

pytestmark = [pytest.mark.stack, pytest.mark.slice("S7"), pytest.mark.req("R-RUN-6")]
TO = "rosa.delgado@sampleton.example"


def test_restart_while_waiting_for_reminder_1() -> None:
    stack.make("reset", env={"SCALE": "1440"})
    stack.wait_app()
    try:
        jobs = flows.import_demo(rows=[4])
        assert flows.approve(jobs["HP-25-004"]).startswith("Approved")
        first = flows.wait_emails(TO, 1)
        sent_at = time.monotonic()
        stack.make("down")
        stack.make("up", env={"SCALE": "1440"})
        stack.wait_app()
        assert len(flows.emails(TO)) == 1  # the first email survived the restart, nothing sent twice
        got = flows.wait_emails(TO, 2, timeout=420)
        assert time.monotonic() - sent_at >= 170  # not before 3 virtual days
        time.sleep(20)
        subjects = sorted(m["Subject"] for m in flows.emails(TO))
        assert (
            len(subjects) == 2
            and subjects[0].startswith("Please confirm")
            and subjects[1].startswith("Reminder")
        )
        assert first[0]["MessageID"] in {m["MessageID"] for m in got}
    finally:
        stack.make("reset")
        stack.wait_app()
