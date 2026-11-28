"""Reading documents with the model gateway, and deciding facts (design 3.8, 3.10, R-IMP-3, R-OBS-1).

Reads run on their own task queues (`model-reads-A`, `model-reads-B`), one at a time per reader.
Results returned to workflows carry IDs and codes only.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from closeout import db
from closeout.activities.safety import scrubbed
from closeout.core.compare import FactDecision, apply_amount_marks, decide_reads
from closeout.gateway.core import Gateway, ModelError, ReplayMiss
from closeout.settings import get_settings


class ReadIn(BaseModel):
    document_id: UUID
    reader: str
    purpose: str = "invoice"


class ReadOut(BaseModel):
    read_id: UUID
    valid: bool


class FactsIn(BaseModel):
    job_id: UUID
    document_id: UUID
    light: bool = False


class FactsOut(BaseModel):
    kept: int
    asked: int
    unconfirmed: int


_gateway: Gateway | None = None


def gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        _gateway = Gateway()
    return _gateway


def document_bytes(path: str) -> bytes:
    p = Path(path)
    if not p.is_absolute():
        p = Path(get_settings().uploads_dir) / p
    return p.read_bytes()


def store_read(conn: Any, document_id: UUID, res: Any) -> UUID:
    rid = db.scalar(
        conn,
        """INSERT INTO reads (document_id, reader, purpose, model, model_digest, prompt_version, output, valid,
                             errors) VALUES (:d, :r, :pu, :m, :g, :p, CAST(:o AS jsonb), :v, :e)
                             ON CONFLICT (document_id, reader, purpose) DO UPDATE SET model=excluded.model,
                               model_digest=excluded.model_digest, prompt_version=excluded.prompt_version,
                               output=excluded.output, valid=excluded.valid, errors=excluded.errors
                             RETURNING id""",
        d=document_id,
        r=res.reader,
        pu=res.purpose,
        m=res.model,
        g=res.model_digest,
        p=res.prompt_version,
        o=json.dumps(res.output) if res.output is not None else None,
        v=res.valid,
        e=res.errors,
    )
    db.run(
        conn,
        """INSERT INTO model_calls (document_id, reader, purpose, provider, model, model_digest, replay_key,
                    response, ms, tokens_in, tokens_out, cost_usd)
                    VALUES (:d, :r, :pu, :pr, :m, :g, :k, :t, :ms, :ti, :to, :c)""",
        d=document_id,
        r=res.reader,
        pu=res.purpose,
        pr=res.provider,
        m=res.model,
        g=res.model_digest,
        k=res.replay_key,
        t=res.text.replace("\x00", ""),
        ms=res.ms,
        ti=res.tokens_in,
        to=res.tokens_out,
        c=res.cost_usd,
    )
    return rid


@activity.defn
@scrubbed
def read_document(inp: ReadIn) -> ReadOut:
    with db.tx() as c:
        doc = db.one(c, "SELECT id, path, mime, removed_at FROM documents WHERE id = :id", id=inp.document_id)
    if doc is None or doc["removed_at"] is not None:
        raise ApplicationError("document missing", type="BadInput", non_retryable=True)
    t0 = time.monotonic()
    try:
        res = gateway().read(document_bytes(doc["path"]), doc["mime"], inp.reader, inp.purpose)  # type: ignore[arg-type]
    except Exception as e:
        # Failed calls are logged too (R-OBS-1), with a code and no model text.
        log_failed_call(inp, type(e).__name__, int((time.monotonic() - t0) * 1000))
        if isinstance(e, ReplayMiss):
            raise ApplicationError(str(e), type="ReplayMiss", non_retryable=True) from None
        if isinstance(e, ModelError):
            raise ApplicationError(str(e), type="BadInput", non_retryable=True) from None
        raise ApplicationError(type(e).__name__, type="ModelCallFailed") from None
    with db.tx() as c:
        rid = store_read(c, inp.document_id, res)
    return ReadOut(read_id=rid, valid=res.valid)


def log_failed_call(inp: ReadIn, error: str, ms: int) -> None:
    gw = gateway()
    provider = gw.provider_for(inp.reader)  # type: ignore[arg-type]
    with db.tx() as c:
        db.run(
            c,
            """INSERT INTO model_calls (document_id, reader, purpose, provider, model, ms, error)
                     VALUES (:d, :r, :pu, :pr, :m, :ms, :e)""",
            d=inp.document_id,
            r=inp.reader,
            pu=inp.purpose,
            pr=provider,
            m=gw.model_for(inp.reader) if provider != "replay" else "replay",  # type: ignore[arg-type]
            ms=ms,
            e=error,
        )


def fact_text(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def store_facts(conn: Any, job_id: UUID, decisions: list[FactDecision], light: bool) -> FactsOut:
    counts = {"kept": 0, "asked": 0, "unconfirmed": 0}
    for d in decisions:
        counts[d.status] += 1
        db.run(
            conn,
            """INSERT INTO facts (job_id, field, value, value_a, value_b, source, status, reason)
                        VALUES (:j, :f, :v, :a, :b, :s, :st, :r)
                        ON CONFLICT (job_id, field) DO UPDATE SET value=excluded.value, value_a=excluded.value_a,
                          value_b=excluded.value_b, source=excluded.source, status=excluded.status,
                          reason=excluded.reason, updated_at=now()
                        WHERE facts.source NOT IN ('person', 'registry')""",
            j=job_id,
            f=d.field,
            v=fact_text(d.value),
            a=fact_text(d.value_a),
            b=fact_text(d.value_b),
            s="one_read" if light else "both_reads",
            st=d.status,
            r=d.reason,
        )
    return FactsOut(**counts)


@activity.defn
@scrubbed
def decide_facts(inp: FactsIn) -> FactsOut:
    light = inp.light or get_settings().closeout_mode == "light"
    with db.tx() as c:
        reads = {
            r["reader"]: r
            for r in db.all_rows(
                c,
                "SELECT reader, output, valid FROM reads WHERE document_id=:d AND purpose='invoice'",
                d=inp.document_id,
            )
        }
        marks = db.one(
            c,
            """SELECT output FROM reads WHERE document_id=:d AND purpose='amount_marks' AND valid""",
            d=inp.document_id,
        )
        today = db.scalar(c, "SELECT (now() AT TIME ZONE 'UTC')::date")
        a = reads.get("A", {}).get("output") if reads.get("A", {}).get("valid") else None
        b = reads.get("B", {}).get("output") if reads.get("B", {}).get("valid") else None
        decisions = decide_reads(a, b, today, light=light)
        if not light:
            decisions = apply_amount_marks(decisions, marks["output"] if marks else None)
        out = store_facts(c, inp.job_id, decisions, light)
        registry_contacts(c, inp.job_id)
        return out


def registry_contacts(conn: Any, job_id: UUID) -> None:
    """R-IMP-4: for a contractor's own import, the contractor's name, license and email come from our list.

    The reads stay in value_a / value_b as evidence for the same-contractor check.
    """
    row = db.one(
        conn,
        """SELECT k.name, k.license, p.email FROM jobs j JOIN contractors k ON k.id = j.contractor_id
                          JOIN recipients r ON r.id = k.recipient_id JOIN people p ON p.id = r.person_id
                          WHERE j.id = :j AND j.origin = 'contractor'""",
        j=job_id,
    )
    if row is None:
        return
    for field, value in (
        ("contractor_name", row["name"]),
        ("contractor_license", row["license"]),
        ("contractor_email", row["email"]),
    ):
        db.run(
            conn,
            """UPDATE facts SET value = :v, source = 'registry', status = 'kept', reason = 'registry',
                        updated_at = now() WHERE job_id = :j AND field = :f""",
            v=value,
            j=job_id,
            f=field,
        )
