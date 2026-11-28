"""S12: the privacy canary on the real stack (R-OPS-2, evals 4.4).

A unique made-up name and email go through the whole flow: import, the email, a correction and a privacy
request. Afterwards they appear nowhere: database dump, uploads, Temporal histories, Mailpit, container logs.
"""

import json
import re
import time
import uuid

import pytest

from tests.stack import flows, stack

pytestmark = [pytest.mark.stack, pytest.mark.slice("S12"), pytest.mark.req("R-OPS-2")]


def test_canary_is_gone_everywhere() -> None:
    stack.make("reset")
    stack.wait_app()
    tag = uuid.uuid4().hex[:8]
    name, email = f"Canary Quillfeather{tag}", f"canary-{tag}@sampleton.example"
    mike, csrf = flows.person("Mike Brennan")
    head = (flows.DEMO / "harbor_pine_jobs.csv").read_text().splitlines()[0]
    row = f"CAN-{tag},{name},{email},14 Osprey Ln,kitchen,,,2025-03-28,48250.00,HP-25-001.png"
    files = [
        ("csv_file", ("j.csv", f"{head}\n{row}\n".encode(), "text/csv")),
        (
            "invoices",
            ("HP-25-001.png", (flows.DEMO / "invoices" / "HP-25-001.png").read_bytes(), "image/png"),
        ),
    ]
    batch = mike.post("/c/import", data={"csrf": csrf}, files=files).url.path
    for _ in range(60):
        m = re.search(r'href="/c/jobs/([0-9a-f-]+)">CAN-', mike.get(batch).text)
        if m and "state ready" in mike.get(batch).text:
            break
        time.sleep(1)
    job = m.group(1)
    assert flows.approve(job).startswith("Approved")
    msg = flows.wait_emails(email, 1)[0]
    link = flows.answer_link(msg)
    import httpx

    h = httpx.Client(follow_redirects=True, timeout=30)
    page = h.get(link).text
    h.post(
        link,
        data={
            "csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1),
            "command_id": re.search(r'name="command_id" value="([^"]+)"', page).group(1),
            "answer": "correct",
            "total": "47250.00",
        },
    )
    assert name in stack.compose("exec", "-T", "postgres", "pg_dump", "-U", "closeout", "closeout").stdout

    ops, ocsrf = flows.person("Priya Natarajan")
    ops.post("/ops/privacy", data={"csrf": ocsrf, "email": email})
    for _ in range(90):
        if "done" in ops.get("/ops/privacy").text:
            break
        time.sleep(1)
    assert "done" in ops.get("/ops/privacy").text

    hits = []
    dump = stack.compose("exec", "-T", "postgres", "pg_dump", "-U", "closeout", "closeout").stdout
    hits += [f"database dump: {w}" for w in (name, email) if w in dump]
    uploads = stack.compose(
        "exec", "-T", "worker", "sh", "-c", f"grep -rl -e '{tag}' /app/var/uploads || true"
    ).stdout.strip()
    if uploads:
        hits.append(f"uploads: {uploads}")
    ids = re.findall(
        r"\s(job-[0-9a-f-]+|import-[0-9a-f-]+|privacy-[0-9a-f-]+)\s",
        stack.temporal_cli("workflow", "list", "--limit", "500"),
    )
    for wid in set(ids):
        hist = stack.temporal_cli("workflow", "show", "-w", wid, "-o", "json")
        hits += [f"temporal history {wid}" for w in (name, email, tag) if w in hist]
    if flows.emails(email):
        hits.append("mailpit")
    logs = stack.compose("logs", "--no-color").stdout
    hits += [f"container logs: {w}" for w in (name, email, tag) if w in logs]
    assert hits == [], json.dumps(hits, indent=1)
