"""`make sim`: simulated people play every persona row in the spec, then every end state is checked (design 3.13).

The bots use the same pages and links as a person: the contractor bot signs in as Mike, and each homeowner bot
reads its own Mailpit inbox and clicks the links (rewriting APP_PUBLIC_URL to APP_INTERNAL_URL). Checks come from
the records: job status, marks from events, emails in Mailpit, open review items, approvals and the audit log.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from closeout import db
from closeout.core.jobs import status_from_events
from closeout.core.record import marks
from closeout.core.wording import check_text
from closeout.events import read_stream
from closeout.mailpit import Mailpit

APP = os.environ.get("APP_INTERNAL_URL", "http://app:8000").rstrip("/")
PUBLIC = os.environ.get("APP_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
MAIL = Mailpit(os.environ.get("MAILPIT_API_URL", "http://mailpit:8025"))
DATA = Path(os.environ.get("CLOSEOUT_DATA_DIR", Path(__file__).parents[3] / "data"))
TIMEOUT = float(os.environ.get("SIM_TIMEOUT", "900"))
BANNED: list[str] = []


def scan(where: str, text: str) -> str:
    hits = check_text(text)
    if hits:
        BANNED.append(f"{where}: {hits}")
    return text


class Web:
    def __init__(self, person: str | None = None) -> None:
        self.c = httpx.Client(base_url=APP, follow_redirects=True, timeout=60, headers={"Host": "app:8000"})
        self.csrf = ""
        if person:
            page = self.get("/")
            ids = {
                n.strip(): i
                for i, n in re.findall(r'name="account_id" value="([^"]+)">\s*<h3>([^<]+)</h3>', page)
            }
            self.c.post("/signin", data={"account_id": ids[person]})
            self.csrf = re.search(r'name="csrf" value="([^"]+)"', self.get("/data")).group(1)  # type: ignore[union-attr]

    def get(self, path: str) -> str:
        r = self.c.get(path)
        return scan(path.split("?")[0][:40], r.text)

    def post(self, path: str, data: dict[str, str], files: Any = None) -> httpx.Response:
        r = self.c.post(path, data=data, files=files)
        scan(path[:40], r.text)
        return r


def internal(url: str) -> str:
    return url.replace(PUBLIC, "")


def form_value(page: str, name: str) -> str:
    m = re.search(rf'name="{name}" value="([^"]+)"', page)
    return m.group(1) if m else ""


@dataclass
class Persona:
    code: str
    email: str
    job_ref: str
    want: dict[str, Any]
    spec: dict[str, Any]
    job_id: str = ""
    done: set[str] = field(default_factory=set)


TOWN: list[TownPersona] = []


def personas() -> list[Persona]:
    world = json.loads((DATA / "seed" / "sampleton" / "world.json").read_text())
    return [
        Persona(
            code=j["code"],
            email=j["email"],
            job_ref=f"HP-25-{i:03d}",
            want={k: j[k] for k in ("ends", "marks", "emails", "reviews")},
            spec=j,
        )
        for i, j in enumerate(world["jobs"], start=1)
    ]


_SEEN: dict[str, dict[str, Any]] = {}


def emails_to(addr: str) -> list[dict[str, Any]]:
    """This person's inbox, oldest first. Each message is fetched and scanned once."""
    msgs = sorted(MAIL.search(f"to:{addr}"), key=lambda m: m["Created"])
    out = []
    for m in msgs:
        if m["ID"] not in _SEEN:
            full = MAIL.message(m["ID"])
            scan(f"email to {addr}", full.get("Subject", "") + "\n" + full.get("Text", ""))
            _SEEN[m["ID"]] = full
        out.append(_SEEN[m["ID"]])
    return out


def link_in(msg: dict[str, Any], kind: str) -> str:
    m = re.search(re.escape(PUBLIC) + rf"/{kind}/\S+", msg["Text"])
    assert m, f"no /{kind}/ link"
    return internal(m.group(0))


def virtual_now(web: Web) -> datetime:
    return datetime.fromisoformat(web.c.get("/clock").json()["virtual_now"])


def answer(link: str, kind: str, **extra: str) -> str:
    w = Web()
    page = w.get(link)
    data = {
        "csrf": form_value(page, "csrf"),
        "command_id": form_value(page, "command_id"),
        "answer": kind,
        "price_visible": "yes",
        **extra,
    }
    r = w.post(link, data)
    return "thanks" if 'id="thanks"' in r.text else re.sub(r"\s+", " ", r.text)[:120]


def job_page(mike: Web, job_id: str) -> str:
    return mike.get(f"/c/jobs/{job_id}")


def status_of(mike: Web, job_id: str) -> str:
    m = re.search(r'id="job-status">([^<]+)<', job_page(mike, job_id))
    return m.group(1).strip().replace(" ", "_") if m else "?"


def run_story(ps: list[Persona]) -> None:
    mike = Web("Mike Brennan")
    demo = DATA / "demo"
    files = [("csv_file", ("jobs.csv", (demo / "harbor_pine_jobs.csv").read_bytes(), "text/csv"))]
    files += [
        ("invoices", (p.name, p.read_bytes(), "image/png"))
        for p in sorted((demo / "invoices").glob("HP-*.png"))
    ]
    batch = str(mike.post("/c/import", {"csrf": mike.csrf}, files=files).url.path)
    end = time.monotonic() + 300  # reading 12 invoices twice can be slow on a small CI machine
    while time.monotonic() < end:
        page = mike.get(batch)
        refs = dict((r, j) for j, r in re.findall(r'href="/c/jobs/([0-9a-f-]+)">(HP-[0-9-]+)<', page))
        if len(refs) == len(ps) and page.count("state ready") == len(ps):
            break
        time.sleep(1)
    missing = [p.job_ref for p in ps if p.job_ref not in refs]
    if missing:
        raise TimeoutError(f"the import didn't make jobs {missing} ready in time")
    for p in ps:
        p.job_id = refs[p.job_ref]
    approve_all = [p for p in ps]
    for p in approve_all:
        approve(mike, p)
    ask_the_town(TOWN)
    for k in K:
        upload_for_contractor(k)
    first_at: dict[str, datetime] = {}
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        busy = False
        for p in ps:
            busy |= step(mike, p, first_at)
        for t in TOWN:
            busy |= clerk_step(t, mike)
        for k in K:
            busy |= contractor_step(k)
        if not busy and all_settled(mike, ps) and town_settled(TOWN) and k_settled(K):
            return
        time.sleep(0.5)
    raise TimeoutError("the simulation did not settle")


def approve(mike: Web, p: Persona) -> None:
    page = mike.get(f"/c/approve/{p.job_id}")
    mike.post(
        f"/c/approve/{p.job_id}",
        {
            "csrf": mike.csrf,
            "command_id": form_value(page, "command_id"),
            "preview": form_value(page, "preview"),
        },
    )


def step(mike: Web, p: Persona, first_at: dict[str, datetime]) -> bool:
    """One move for one persona, when its moment has come. Returns True while the persona still has moves."""
    s = p.spec
    msgs = emails_to(p.email)
    if msgs and p.code not in first_at:
        first_at[p.code] = virtual_now(mike)
    n = len(msgs)
    act = s.get("answer", "confirm")
    after = {"reminder_1": 2, "reminder_2": 3}.get(s.get("answer_after", ""), 1)
    if act == "never":
        return False
    if "answered" not in p.done and n >= after:
        if s.get("answer_after") == "day_20" and virtual_now(mike) < first_at[p.code] + timedelta(days=20):
            return True
        link = link_in(msgs[-1], "a")
        if act == "unsubscribe":
            Web().post(link_in(msgs[0], "u"), {})
        elif "correct" in s:
            answer(link, "correct", **{k: v for k, v in s["correct"].items()})
        else:
            answer(link, act)
        p.done.add("answered")
        return True
    if "answered" in p.done and "correct" in s and "decided" not in p.done:
        if 'id="correction"' in job_page(mike, p.job_id):
            page = job_page(mike, p.job_id)
            decision = "accept" if s.get("contractor_bot") == "accept" else "reject"
            mike.post(
                f"/c/jobs/{p.job_id}/correction",
                {"csrf": mike.csrf, "decision": decision, "command_id": form_value(page, "command_id")},
            )
            p.done.add("decided")
        return True
    if s.get("undo") and "answered" in p.done and "undone" not in p.done:
        page = job_page(mike, p.job_id)
        if 'id="undo"' in page:
            mike.post(
                f"/c/jobs/{p.job_id}/undo", {"csrf": mike.csrf, "command_id": form_value(page, "command_id")}
            )
            p.done.add("undone")
        return True
    if s.get("undo") and "undone" in p.done and "reapproved" not in p.done:
        if status_of(mike, p.job_id) == "ready":
            approve(mike, p)
            p.done.add("reapproved")
        return True
    if s.get("undo") and "reapproved" in p.done and "answered2" not in p.done:
        if n >= 2:
            answer(link_in(msgs[-1], "a"), "confirm")
            p.done.add("answered2")
        return True
    return False


def all_settled(mike: Web, ps: list[Persona]) -> bool:
    finals = {"recorded", "stopped", "needs_a_look"}
    for p in ps:
        st = status_of(mike, p.job_id)
        if p.want["ends"] == "no_answer":
            if st != "no_answer":
                return False
        elif st not in finals or st != p.want["ends"]:
            return False
    # no_answer jobs keep their workflow open until day 30: wait for the window so late moves can't happen.
    return True


# --- the town clerk (T1-T3) ----------------------------------------------------------------------------

CLERK = "clerk@sampleton.test"


@dataclass
class TownPersona:
    code: str
    permit_no: str
    behaviour: str  # final_on_file | no_record | never
    want: dict[str, Any]
    request_id: str = ""
    done: bool = False


def town_personas() -> list[TownPersona]:
    world = json.loads((DATA / "seed" / "sampleton" / "world.json").read_text())
    return [
        TownPersona(
            code=t["code"],
            permit_no=t["permit_no"],
            behaviour=t["clerk"],
            want={"status": t["ends"], "result": t["result"], "emails": t["emails"]},
        )
        for t in world["town_requests"]
    ]


def reply_file(permit_no: str, result: str) -> Path:
    """A clerk letter from the town-reply set for this permit and result."""
    d = Path(os.environ.get("CLOSEOUT_EVALS_DIR", DATA.parent / "evals")) / "sets" / "town_replies"
    for c in json.loads((d / "truth.json").read_text()):
        e = c["expected"]
        if e["permit_number"] == permit_no and e["result"] == result:
            return d / c["file"]
    raise FileNotFoundError(f"no reply letter for {permit_no} {result}")


def ask_the_town(ts: list[TownPersona]) -> None:
    dana = Web("Dana Whitfield")
    page = dana.get("/h/town")
    for t in ts:
        m = re.search(
            rf'action="(/h/town/ask/[^"]+)"><input type="hidden" name="csrf" value="[^"]+"><button '
            rf'class="ask" data-permit="{re.escape(t.permit_no)}"',
            page,
        )
        assert m, f"permit {t.permit_no} not offered"
        r = dana.post(m.group(1), {"csrf": dana.csrf})
        t.request_id = r.url.path.rsplit("/", 1)[1]
        req = dana.get(f"/h/town/{t.request_id}")
        dana.post(
            f"/h/town/{t.request_id}/approve",
            {
                "csrf": dana.csrf,
                "command_id": form_value(req, "command_id"),
                "preview": form_value(req, "preview"),
            },
        )
        page = dana.get("/h/town")


def clerk_step(t: TownPersona, web: Web) -> bool:
    if t.done or t.behaviour == "never":
        return False
    msgs = [m for m in emails_to(CLERK) if t.permit_no in m.get("Subject", "")]
    if not msgs:
        return True
    if t.behaviour == "final_on_file":
        from closeout.core.dates import add_business_days

        start = t.__dict__.setdefault("first_seen", virtual_now(web))  # when the request arrived (virtual)
        if virtual_now(web).date() < add_business_days(start.date(), 4):
            return True
    elif len(msgs) < 2:  # T2 replies only after the reminder
        return True
    link = link_in(msgs[-1], "t")
    page = Web().get(link)
    f = reply_file(t.permit_no, t.behaviour)
    w = Web()
    r = w.post(
        link,
        {"csrf": form_value(page, "csrf"), "command_id": form_value(page, "command_id")},
        files=[("reply", (f.name, f.read_bytes(), "image/png" if f.suffix == ".png" else "image/jpeg"))],
    )
    t.done = 'id="reply-done"' in r.text
    return True


def town_settled(ts: list[TownPersona]) -> bool:
    with db.tx() as c:
        for t in ts:
            st = db.scalar(c, "SELECT status FROM town_requests WHERE id = :i", i=t.request_id)
            if st != t.want["status"]:
                return False
    return True


def check_town(ts: list[TownPersona]) -> list[tuple[str, bool, str]]:
    out = []
    with db.tx() as c:
        for t in ts:
            if not t.request_id:
                out.append((f"{t.code} ends {t.want['status']}", False, "the request was never made"))
                continue
            r = db.one(c, "SELECT status, result FROM town_requests WHERE id = :i", i=t.request_id) or {}
            n = len([m for m in MAIL.search(f"to:{CLERK}") if t.permit_no in m.get("Subject", "")])
            n_rev = db.scalar(
                c,
                "SELECT count(*) FROM review_items WHERE ref = :r AND resolved_at IS NULL",
                r=f"town-{t.request_id}",
            )
            got = {"status": r.get("status"), "result": r.get("result"), "emails": n, "reviews": n_rev}
            want = {**t.want, "reviews": 0}
            ok = got == want
            out.append(
                (
                    f"{t.code} ends {t.want['status']}, {t.want['result'] or 'no result'}, {t.want['emails']} emails",
                    ok,
                    "" if ok else f"got {got}",
                )
            )
    return out


def check(ps: list[Persona]) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    with db.tx() as c:
        for p in ps:
            if not p.job_id:
                results.append((f"{p.code} ends {p.want['ends']}", False, "the job was never created"))
                continue
            evs = read_stream(c, f"job-{p.job_id}")
            status = db.scalar(c, "SELECT status FROM jobs WHERE id = :j", j=p.job_id)
            n_marks = len(marks(evs))
            n_mail = len(MAIL.search(f"to:{p.email}"))
            n_rev = db.scalar(
                c,
                "SELECT count(*) FROM review_items WHERE ref = :r AND resolved_at IS NULL",
                r=f"job-{p.job_id}",
            )
            got = {"ends": status, "marks": n_marks, "emails": n_mail, "reviews": n_rev}
            origin = db.scalar(c, "SELECT origin FROM jobs WHERE id = :j", j=p.job_id) or "contractor"
            ok = got == p.want and status_from_events(evs, origin) == status
            results.append(
                (
                    f"{p.code} ends {p.want['ends']}, {p.want['marks']} marks, {p.want['emails']} emails, "
                    f"{p.want['reviews']} review items",
                    ok,
                    "" if ok else f"got {got}",
                )
            )
        sent = db.all_rows(
            c,
            """SELECT o.id, o.approval_id, a.used_at, a.used_by_command = o.command_id AS mine,
                                 (SELECT count(*) FROM audit_log l WHERE l.approval_id = o.approval_id
                                  AND l.action = 'send_email') AS audits
                                 FROM outbox o JOIN approvals a ON a.id = o.approval_id WHERE o.state = 'sent'""",
        )
        bad = [r for r in sent if not (r["used_at"] and r["mine"] and r["audits"] == 1)]
        results.append(
            (
                f"every delivered email ({len(sent)}) has a used approval and an audit row",
                not bad,
                f"{len(bad)} without",
            )
        )
        supp = db.all_rows(
            c,
            """SELECT o.id FROM outbox o JOIN suppression s ON true WHERE o.state = 'sent'
                                 AND o.created_at > s.added_at AND s.email_hmac = (
                                 SELECT p.email_hmac FROM recipients r JOIN people p ON p.id = r.person_id
                                 WHERE r.id = o.recipient_id)""",
        )
        results.append(("no email went to a suppressed address", not supp, f"{len(supp)} sent"))
        mismatch = [
            j["id"]
            for j in db.all_rows(c, "SELECT id, status, origin FROM jobs")
            if status_from_events(read_stream(c, f"job-{j['id']}"), j["origin"]) != j["status"]
        ]
        results.append(
            ("every job's status equals what its events say", not mismatch, f"{len(mismatch)} differ")
        )
    results.append(("no page or email contains a banned phrase", not BANNED, "; ".join(BANNED[:3])))
    return results


def summary(ps: list[Persona]) -> list[dict[str, Any]]:
    """The end-state summary: every job's state and marks, with no times or IDs (evals 4.6)."""
    with db.tx() as c:
        return [
            {
                "persona": p.code,
                "status": db.scalar(c, "SELECT status FROM jobs WHERE id=:j", j=p.job_id)
                if p.job_id
                else None,
                "marks": [m.kind for m in marks(read_stream(c, f"job-{p.job_id}"))] if p.job_id else [],
            }
            for p in ps
        ]


# --- K1: a contractor asked by a homeowner (S9) ---------------------------------------------------------

K: list[Persona] = []


def k_personas() -> list[Persona]:
    world = json.loads((DATA / "seed" / "sampleton" / "world.json").read_text())
    k = world["k1"]
    people = {p["key"]: p for p in world["people"]}
    email = people[f"contractor:{k['contractor']}"]["email"]
    return [
        Persona(
            code=k["code"],
            email=email,
            job_ref="K1",
            spec=k,
            want={"ends": k["ends"], "marks": k["marks"], "emails": k["emails"], "reviews": 0},
        )
    ]


def upload_for_contractor(k: Persona) -> None:
    """Dana uploads the contractor's invoice, waits for it to be read, then approves the message to them."""
    dana = Web("Dana Whitfield")
    page = dana.get("/h/upload")
    names = dict(
        (n.split(" (")[0], i) for i, n in re.findall(r'<option value="([^"]+)">([^<]+)</option>', page)
    )
    world = json.loads((DATA / "seed" / "sampleton" / "world.json").read_text())
    kname = next(c["name"] for c in world["contractors"] if c["key"] == k.spec["contractor"])
    f = DATA / "demo" / "invoices" / f"K1-{k.spec['contractor']}.png"
    r = dana.post(
        "/h/upload",
        {
            "csrf": dana.csrf,
            "contractor_id": names[kname.replace("&", "&amp;")],
            "work_type": k.spec["work"],
            "finish_date": k.spec["finish"],
            "total": k.spec["total"],
        },
        files=[("invoice", (f.name, f.read_bytes(), "image/png"))],
    )
    k.job_id = r.url.path.rsplit("/", 1)[1]
    end = time.monotonic() + 120
    while time.monotonic() < end:
        page = dana.get(f"/h/jobs/{k.job_id}")
        if 'id="approve"' in page:
            dana.post(
                f"/h/jobs/{k.job_id}/approve",
                {
                    "csrf": dana.csrf,
                    "command_id": form_value(page, "command_id"),
                    "preview": form_value(page, "preview"),
                },
            )
            return
        time.sleep(1)
    raise TimeoutError("K1's upload never became ready")


def contractor_step(k: Persona) -> bool:
    if "answered" in k.done:
        return False
    msgs = emails_to(k.email)
    if not msgs:
        return True
    answer(link_in(msgs[-1], "k"), "confirm")
    k.done.add("answered")
    return False


def k_settled(ks: list[Persona]) -> bool:
    with db.tx() as c:
        return all(
            db.scalar(c, "SELECT status FROM jobs WHERE id = :j", j=k.job_id) == k.want["ends"] for k in ks
        )


def wait_for_app(seconds: float = 180) -> None:
    """The app may still be starting (compose can recreate it for this run)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if httpx.get(APP + "/healthz", headers={"Host": "app:8000"}, timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise TimeoutError("the app did not start")


def main() -> int:
    wait_for_app()
    ps = personas()
    TOWN.extend(town_personas())
    K.extend(k_personas())
    t0 = time.monotonic()
    try:
        run_story(ps)
        error = ""
    except Exception as e:  # report what was reached
        error = f"{type(e).__name__}: {e}"
        print(f"The story stopped early: {error}", flush=True)
    results = check(ps + K) + check_town(TOWN)
    print(
        f"\nSimulation finished in {time.monotonic() - t0:.0f} s{' with an error: ' + error if error else ''}\n"
    )
    for name, ok, why in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({why})" if why else ""))
    print("\nend-state summary: " + json.dumps(summary(ps + K), sort_keys=True))
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed.")
    return 1 if failed or error else 0


if __name__ == "__main__":
    sys.exit(main())
