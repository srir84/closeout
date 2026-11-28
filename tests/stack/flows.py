"""Drive the running stack like a person: sign in, import the demo jobs, approve, read Mailpit."""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx

from tests.stack.stack import APP, MAILPIT

DEMO = Path(__file__).parents[2] / "data" / "demo"


def person(name: str) -> tuple[httpx.Client, str]:
    c = httpx.Client(base_url=APP, follow_redirects=True, timeout=60)
    page = c.get("/").text
    ids = dict(
        (n.strip(), i) for i, n in re.findall(r'name="account_id" value="([^"]+)">\s*<h3>([^<]+)</h3>', page)
    )
    c.post("/signin", data={"account_id": ids[name]})
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/data").text).group(1)
    return c, csrf


def import_demo(rows: list[int] | None = None) -> dict[str, str]:
    """Import Mike's demo CSV (or some of its rows). Returns job_ref -> job id once all have settled."""
    c, csrf = person("Mike Brennan")
    lines = (DEMO / "harbor_pine_jobs.csv").read_text().splitlines()
    pick = [lines[0]] + ([lines[i] for i in rows] if rows else lines[1:])
    files = [("csv_file", ("jobs.csv", ("\n".join(pick) + "\n").encode(), "text/csv"))]
    files += [
        ("invoices", (p.name, p.read_bytes(), "image/png"))
        for p in sorted((DEMO / "invoices").glob("HP-*.png"))
    ]
    r = c.post("/c/import", data={"csrf": csrf}, files=files)
    batch = r.url.path
    want = len(pick) - 1
    for _ in range(120):
        t = c.get(batch).text
        if t.count("state ready") + t.count("state needs_a_look") >= want:
            return dict(
                (ref, jid) for jid, ref in re.findall(r'href="/c/jobs/([0-9a-f-]+)">(HP-[0-9-]+)<', t)
            )
        time.sleep(1)
    raise TimeoutError("import did not settle")


def approve(job_id: str) -> str:
    c, csrf = person("Mike Brennan")
    page = c.get(f"/c/approve/{job_id}").text
    cmd = re.search(r'name="command_id" value="([^"]+)"', page).group(1)
    preview = re.search(r'name="preview" value="([^"]+)"', page).group(1)
    r = c.post(f"/c/approve/{job_id}", data={"csrf": csrf, "command_id": cmd, "preview": preview})
    return re.search(r'id="msg">([^<]+)<', r.text).group(1)


def emails(to: str) -> list[dict]:
    r = httpx.get(MAILPIT + "/api/v1/search", params={"query": f"to:{to}"}, timeout=10).json()
    return [
        httpx.get(f"{MAILPIT}/api/v1/message/{m['ID']}", timeout=10).json() for m in r.get("messages") or []
    ]


def wait_emails(to: str, n: int, timeout: float = 120) -> list[dict]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = emails(to)
        if len(got) >= n:
            return got
        time.sleep(1)
    raise TimeoutError(f"{to}: {len(emails(to))} emails, wanted {n}")


def answer_link(msg: dict) -> str:
    return re.search(r"http://127.0.0.1:8000/a/\S+", msg["Text"]).group(0)


def unsub_link(msg: dict) -> str:
    return re.search(r"http://127.0.0.1:8000/u/\S+", msg["Text"]).group(0)


def job_status(job_id: str) -> str:
    c, _ = person("Mike Brennan")
    return re.search(r'id="job-status">([^<]+)<', c.get(f"/c/jobs/{job_id}").text).group(1).strip()
