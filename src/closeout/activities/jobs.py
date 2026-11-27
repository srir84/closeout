"""Activities for the Job and ImportBatch workflows (design 3.8). Results carry IDs and codes only."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel
from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from closeout import db, events, files
from closeout.activities.safety import scrubbed
from closeout.core import address as addr
from closeout.core.clean import clean_field, invoice_fields
from closeout.core.compare import other_contractor, same_value
from closeout.core.imports import parse_csv
from closeout.core.jobs import Refused, homeowner_started_next, next_state
from closeout.privacy_hmac import email_hmac
from closeout.settings import get_settings
from closeout.workflows.payloads import ClockIn

# R-IMP-4: who the contractor is comes from our list. A person can't type over it.
REGISTRY_FIELDS = ("contractor_name", "contractor_license", "contractor_email")


class RecordIn(BaseModel):
    job_id: UUID
    kind: str
    payload: dict[str, object]
    event_key: str
    actor: UUID | None = None
    CODE_FIELDS: ClassVar[tuple[str, ...]] = ("payload", "event_key")  # validated against the event models


class RecordOut(BaseModel):
    status: str
    seq: int


class JobRef(BaseModel):
    job_id: UUID


class PrepOut(BaseModel):
    document_id: UUID | None
    origin: str


class MatchOut(BaseModel):
    pin: str | None
    reason: str


class CheckOut(BaseModel):
    reasons: list[str]
    review_item_ids: list[UUID]


class CommandIn(BaseModel):
    job_id: UUID
    command_id: UUID


class CommandOut(BaseModel):
    ok: bool
    code: str


def job_stream(job_id: UUID) -> str:
    return f"job-{job_id}"


@activity.defn
@scrubbed
def record(inp: RecordIn) -> RecordOut:
    """Add one job event and keep jobs.status equal to what the events say."""
    with db.tx() as c:
        job = db.one(c, "SELECT status, origin FROM jobs WHERE id = :id FOR UPDATE", id=inp.job_id)
        assert job is not None
        stream = job_stream(inp.job_id)
        existing = db.one(c, "SELECT seq FROM events WHERE event_key = :k", k=inp.event_key)
        if existing is not None:
            return RecordOut(status=job["status"], seq=existing["seq"])
        step = homeowner_started_next if job["origin"] == "homeowner" else next_state
        first = events.last_seq(c, stream) == 0
        to = step(None if first else job["status"], inp.kind)
        if isinstance(to, Refused):
            raise ValueError(f"{inp.kind} refused in {job['status']}: {to.reason}")
        e = events.append_next(c, stream, inp.kind, inp.payload, inp.event_key, inp.actor)
        rnd = int(inp.payload.get("round", 1) or 1)  # type: ignore[arg-type]
        db.run(
            c,
            "UPDATE jobs SET status = :s, round = GREATEST(round, :r), updated_at = now() WHERE id = :id",
            s=to,
            r=rnd,
            id=inp.job_id,
        )
        return RecordOut(status=to, seq=e.seq)


@activity.defn
@scrubbed
def prepare_job(inp: JobRef) -> PrepOut:
    with db.tx() as c:
        job = db.one(c, "SELECT origin FROM jobs WHERE id = :id", id=inp.job_id)
        doc = db.one(
            c,
            "SELECT id FROM documents WHERE job_id = :id AND kind = 'invoice' AND removed_at IS NULL "
            "ORDER BY created_at LIMIT 1",
            id=inp.job_id,
        )
    assert job is not None
    return PrepOut(document_id=doc["id"] if doc else None, origin=job["origin"])


def parcel_addresses(conn: Any, town: str) -> list[addr.ParcelAddress]:
    rows = db.all_rows(
        conn,
        """SELECT pin, address_norm, house_no, street, unit, qualifier FROM parcels
                                WHERE town = :t AND address_norm IS NOT NULL""",
        t=town,
    )
    return [
        addr.ParcelAddress(
            pin=r["pin"],
            address_norm=r["address_norm"],
            house_no=r["house_no"],
            street=r["street"] or "",
            unit=r["unit"],
            condo=(r["qualifier"] or "").upper().startswith("C"),
        )
        for r in rows
    ]


@activity.defn
@scrubbed
def match_job_address(inp: JobRef) -> MatchOut:
    """R-IMP-2: one parcel, or needs a look, never a guess."""
    with db.tx() as c:
        job = db.one(c, "SELECT address_raw, town FROM jobs WHERE id = :id", id=inp.job_id)
        assert job is not None
        if not job["address_raw"]:
            return MatchOut(pin=None, reason="no_address")
        m = addr.match(job["address_raw"], parcel_addresses(c, job["town"] or "9999"))
        db.run(c, "UPDATE jobs SET pin = :p, updated_at = now() WHERE id = :id", p=m.pin, id=inp.job_id)
        return MatchOut(pin=m.pin, reason=m.reason)


def open_review(conn: Any, kind: str, ref: str, reason: str, evidence: dict[str, Any], dedupe: str) -> UUID:
    rid = db.scalar(
        conn,
        """INSERT INTO review_items (kind, ref, reason, evidence, dedupe_key)
                             VALUES (:k, :r, :re, CAST(:e AS jsonb), :d)
                             ON CONFLICT (dedupe_key) DO UPDATE SET resolved_at = NULL, resolution = NULL RETURNING id""",
        k=kind,
        r=ref,
        re=reason,
        e=json.dumps(evidence, default=str),
        d=dedupe,
    )
    if rid is None:
        rid = db.scalar(conn, "SELECT id FROM review_items WHERE dedupe_key = :d", d=dedupe)
    return rid


def facts_of(conn: Any, job_id: UUID) -> dict[str, dict[str, Any]]:
    return {r["field"]: r for r in db.all_rows(conn, "SELECT * FROM facts WHERE job_id = :j", j=job_id)}


@activity.defn
@scrubbed
def check_job(inp: JobRef) -> CheckOut:
    """After reading: what still needs a person? Returns reason codes; opens review items where ops must look."""
    reasons: list[str] = []
    items: list[UUID] = []
    with db.tx() as c:
        job = db.one(
            c,
            """SELECT j.*, k.license AS our_license, k.name AS our_name FROM jobs j
                           LEFT JOIN contractors k ON k.id = j.contractor_id WHERE j.id = :id""",
            id=inp.job_id,
        )
        assert job is not None
        today = db.scalar(c, "SELECT (now() AT TIME ZONE 'UTC')::date")
        facts = facts_of(c, inp.job_id)
        if not facts:
            reasons.append("read_failed")
        # CSV values the contractor typed must agree with the invoice, or the person is asked.
        for field, col in (("completion_date", "csv_finish_date"), ("total_amount", "csv_total")):
            f = facts.get(field)
            typed = job[col]
            if typed is None or f is None or f["source"] == "person":
                continue
            read = clean_field(field, f["value"], today).value if f["value"] else None
            want = Decimal(typed).quantize(Decimal("0.01")) if field == "total_amount" else typed
            if f["status"] == "kept" and not same_value(field, read, want):
                db.run(
                    c,
                    "UPDATE facts SET status='asked', reason='csv_disagrees', updated_at=now() WHERE id=:i",
                    i=f["id"],
                )
                f["status"] = "asked"
        a = facts.get("job_address")
        if (
            a
            and a["status"] == "kept"
            and a["source"] != "person"
            and job["address_raw"]
            and addr.normalise(a["value"] or "") != addr.normalise(job["address_raw"])
        ):
            db.run(
                c,
                "UPDATE facts SET status='asked', reason='csv_disagrees', updated_at=now() WHERE id=:i",
                i=a["id"],
            )
            a["status"] = "asked"
        # R-IMP-4: the invoice must be from the uploader. The read license or name is only compared.
        if job["origin"] == "contractor" and job["our_license"]:
            lic = facts.get("contractor_license", {})
            name = facts.get("contractor_name", {})
            # Each read on its own: if either read names someone else, a person looks.
            reads = [(lic.get(k), name.get(k)) for k in ("value_a", "value_b")]
            if any(other_contractor(rl, rn, job["our_license"], job["our_name"]) for rl, rn in reads):
                reasons.append("other_contractor")
                items.append(
                    open_review(
                        c,
                        "other_contractor",
                        f"job-{inp.job_id}",
                        "other_contractor",
                        {"job_id": str(inp.job_id)},
                        f"other_contractor:{inp.job_id}",
                    )
                )
        statuses = {f["status"] for f in facts.values()}
        if "asked" in statuses:
            reasons.append("facts_asked")
        if "unconfirmed" in statuses:
            reasons.append("light_review")
        if job["pin"] is None:
            reasons.append("address_unmatched")
        order = ("read_failed", "other_contractor", "address_unmatched", "facts_asked", "light_review")
        reasons.sort(key=order.index)
        # R-OPS-1: everything the system won't guess is on the one list, with the reason and IDs as proof.
        for r in reasons:
            if r != "other_contractor":
                items.append(
                    open_review(
                        c,
                        "needs_a_look",
                        f"job-{inp.job_id}",
                        r,
                        {"job_id": str(inp.job_id), "pin": job["pin"]},
                        f"{r}:{inp.job_id}",
                    )
                )
        # Reasons that no longer apply are settled.
        db.run(
            c,
            """UPDATE review_items SET resolved_at = now(), resolution = 'fixed'
                     WHERE ref = :ref AND resolved_at IS NULL AND kind IN ('needs_a_look', 'other_contractor')
                     AND NOT (reason = ANY(:keep))""",
            ref=f"job-{inp.job_id}",
            keep=reasons,
        )
        db.run(
            c,
            "UPDATE jobs SET needs_reason = :r WHERE id = :id",
            r=reasons[0] if reasons else None,
            id=inp.job_id,
        )
    return CheckOut(reasons=reasons, review_item_ids=items)


@activity.defn
@scrubbed
def apply_fix(inp: CommandIn) -> CommandOut:
    """A person fixed a fact (or approved all facts in light mode). The typed value stays in the database."""
    with db.tx() as c:
        cmd = db.one(c, "SELECT kind, payload FROM commands WHERE command_id = :id", id=inp.command_id)
        if cmd is None:
            return CommandOut(ok=False, code="unknown_command")
        today = db.scalar(c, "SELECT (now() AT TIME ZONE 'UTC')::date")
        if cmd["kind"] == "approve_facts":
            db.run(
                c,
                """UPDATE facts SET status='kept', source='person', reason='approved', updated_at=now()
                         WHERE job_id = :j AND status = 'unconfirmed' AND value IS NOT NULL""",
                j=inp.job_id,
            )
            left = db.scalar(
                c, "SELECT count(*) FROM facts WHERE job_id=:j AND status='unconfirmed'", j=inp.job_id
            )
            if left:
                db.run(
                    c,
                    "UPDATE facts SET status='asked', reason='no_value' WHERE job_id=:j AND status='unconfirmed'",
                    j=inp.job_id,
                )
            # The approved job address is the address now: it is matched again before the job can be ready.
            approved = db.scalar(
                c,
                """SELECT value FROM facts WHERE job_id = :j AND field = 'job_address'
                                       AND status = 'kept' AND source = 'person'""",
                j=inp.job_id,
            )
            if approved:
                db.run(c, "UPDATE jobs SET address_raw = :a WHERE id = :id", a=approved, id=inp.job_id)
            return CommandOut(ok=True, code="approved")
        field = str(cmd["payload"].get("field", ""))
        raw = cmd["payload"].get("value")
        if field in REGISTRY_FIELDS:
            return CommandOut(ok=False, code="registry_field")
        if field not in invoice_fields():
            return CommandOut(ok=False, code="bad_field")
        cleaned = clean_field(field, raw, today)
        if not cleaned.ok:
            return CommandOut(ok=False, code="bad_value")
        value = cleaned.value.isoformat() if isinstance(cleaned.value, date) else str(cleaned.value)
        db.run(
            c,
            """INSERT INTO facts (job_id, field, value, source, status, reason)
                     VALUES (:j, :f, :v, 'person', 'kept', 'fixed')
                     ON CONFLICT (job_id, field) DO UPDATE SET value = excluded.value, source = 'person',
                       status = 'kept', reason = 'fixed', updated_at = now()""",
            j=inp.job_id,
            f=field,
            v=value,
        )
        if field == "job_address":
            db.run(
                c,
                "UPDATE jobs SET address_raw = :a, content_rev = content_rev + 1 WHERE id = :id",
                a=value,
                id=inp.job_id,
            )
        elif field in ("completion_date", "total_amount"):
            db.run(c, "UPDATE jobs SET content_rev = content_rev + 1 WHERE id = :id", id=inp.job_id)
        return CommandOut(ok=True, code="fixed")


# --- import batches --------------------------------------------------------------------------------


class BatchIn(BaseModel):
    batch_id: UUID
    clock: ClockIn
    light: bool = False  # fixed when the batch starts, so every job reads the same way


class BatchOut(BaseModel):
    job_ids: list[UUID]
    rejected: int


def create_jobs_from_rows(c: Any, batch: dict[str, Any], parsed: Any, file_map: dict[str, Any]) -> list[UUID]:
    acct = db.one(c, "SELECT id, contractor_id FROM accounts WHERE id = :a", a=batch["account_id"])
    assert acct is not None
    job_ids = []
    for row in parsed.rows:
        f = file_map.get(row.invoice_file)
        if f is None or not f.get("ok"):
            parsed.rejected.append(_rej(row.line, row.job_ref, "missing_file" if f is None else "bad_file"))
            continue
        pid = db.scalar(
            c,
            "INSERT INTO people (display_name, email, email_hmac) VALUES (:n, :e, :h) RETURNING id",
            n=row.homeowner_name,
            e=row.homeowner_email,
            h=email_hmac(row.homeowner_email),
        )
        rid = db.scalar(
            c,
            """INSERT INTO recipients (person_id, source, entered_by) VALUES (:p, 'owner_entered', :a)
                              RETURNING id""",
            p=pid,
            a=acct["id"],
        )
        jid = db.scalar(
            c,
            """INSERT INTO jobs (origin, contractor_id, batch_id, job_ref, homeowner_recipient_id,
                              address_raw, work_type, stated_like_for_like, stated_detached_1_2_family,
                              csv_finish_date, csv_total, town)
                              VALUES ('contractor', :k, :b, :ref, :r, :addr, :wt, :ll, :dd, :fd, :tot, :town)
                              RETURNING id""",
            k=acct["contractor_id"],
            b=batch["id"],
            ref=row.job_ref,
            r=rid,
            addr=row.job_address,
            wt=row.work_type,
            ll=row.same_size_replacement,
            dd=row.detached_1_2_family,
            fd=row.finish_date,
            tot=row.total,
            town=batch["town"],
        )
        db.run(
            c,
            """INSERT INTO documents (job_id, kind, sha256, path, mime, pages)
                     VALUES (:j, 'invoice', :s, :p, :m, :n)""",
            j=jid,
            s=f["sha256"],
            p=f["path"],
            m=f["mime"],
            n=f["pages"],
        )
        job_ids.append(jid)
    return job_ids


def _rej(line: int, ref: str | None, reason: str) -> Any:
    from closeout.core.imports import Rejected

    return Rejected(line=line, job_ref=ref, reason=reason)


@activity.defn
@scrubbed
def prepare_batch(inp: BatchIn) -> BatchOut:
    """Check the CSV rows and files, make the people, recipients, jobs and documents. Idempotent per batch."""
    with db.tx() as c:
        batch = db.one(c, "SELECT * FROM import_batches WHERE id = :id FOR UPDATE", id=inp.batch_id)
        assert batch is not None
        done = [
            r["id"]
            for r in db.all_rows(
                c, "SELECT id FROM jobs WHERE batch_id = :b ORDER BY created_at", b=inp.batch_id
            )
        ]
        if batch["status"] in ("running", "done") and done:
            return BatchOut(job_ids=done, rejected=len(batch["rejected"]))
        csv_path = files.path_of(f"batches/{inp.batch_id}/import.csv")
        data = csv_path.read_bytes() if csv_path.exists() else b""
        already = {
            r["job_ref"]
            for r in db.all_rows(
                c,
                """SELECT job_ref FROM jobs j JOIN accounts a
                   ON a.contractor_id = j.contractor_id WHERE a.id = :a""",
                a=batch["account_id"],
            )
        }
        today = db.scalar(c, "SELECT (now() AT TIME ZONE 'UTC')::date")
        parsed = parse_csv(data, today, already)
        job_ids = [] if parsed.error else create_jobs_from_rows(c, batch, parsed, batch["files"])
        rejected = [r.model_dump() for r in parsed.rejected]
        if parsed.error:
            rejected = [{"line": 1, "job_ref": None, "reason": parsed.error}]
        db.run(
            c,
            """UPDATE import_batches SET status = 'running', rejected = CAST(:r AS jsonb),
                     counts = CAST(:n AS jsonb) WHERE id = :id""",
            r=json.dumps(rejected),
            n=json.dumps({"rows": len(parsed.rows), "jobs": len(job_ids), "rejected": len(rejected)}),
            id=inp.batch_id,
        )
    if csv_path.exists():
        csv_path.unlink()  # the typed names and emails now live only in people (cleanable, design 3.17)
    return BatchOut(job_ids=job_ids, rejected=len(rejected))


@activity.defn
@scrubbed
def finish_batch(inp: BatchIn) -> None:
    with db.tx() as c:
        db.run(
            c,
            "UPDATE import_batches SET status = 'done', finished_at = now() WHERE id = :id",
            id=inp.batch_id,
        )


class StartJobsIn(BaseModel):
    job_ids: list[UUID]
    clock: ClockIn
    light: bool = False


class Starter:
    """Starts Job workflows through the client, so jobs live on after the batch ends (design 3.8)."""

    def __init__(self, client: Client) -> None:
        self.client = client

    @activity.defn(name="start_jobs")
    @scrubbed
    async def start_jobs(self, inp: StartJobsIn) -> int:
        from closeout.workflows.job import JobIn

        n = 0
        for jid in inp.job_ids:
            try:
                await self.client.start_workflow(
                    "Job",
                    JobIn(job_id=jid, clock=inp.clock, light=inp.light),
                    id=f"job-{jid}",
                    task_queue=get_settings().task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                )
            except WorkflowAlreadyStartedError:
                pass  # started by an earlier try, even if it has finished since: that counts
            n += 1
        return n
