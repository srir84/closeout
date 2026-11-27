"""`perform`: the one permission check every action goes through (design 3.6, R-SAFE-2, rule 3).

It is the only code that writes to `outbox`, share `links` and approved `town_requests`. Everything happens
in one database transaction, so a retry either finds the first result or does the whole thing once.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Connection

from closeout import db, keys
from closeout.core import approval as ap
from closeout.core.ids import message_id, send_command_id
from closeout.privacy_hmac import email_hmac


class PerformIn(BaseModel):
    approval_id: UUID
    action: str
    target: str
    round: int
    step: str
    actor_account_id: UUID | None = None


class PerformOut(BaseModel):
    ok: bool
    code: str
    outbox_id: UUID | None = None
    command_id: UUID


def fingerprint(inp: PerformIn) -> str:
    return hashlib.sha256(
        json.dumps(inp.model_dump(mode="json", exclude={"actor_account_id"}), sort_keys=True).encode()
    ).hexdigest()


def audit(
    conn: Connection,
    action: str,
    target: str,
    actor: UUID | None,
    approval_id: UUID | None,
    command_id: UUID | None,
    details: dict[str, Any],
) -> None:
    db.run(
        conn,
        """INSERT INTO audit_log (actor_account_id, action, target, approval_id, command_id, details)
                    VALUES (:a, :ac, :t, :ap, :c, CAST(:d AS jsonb))""",
        a=actor,
        ac=action,
        t=target,
        ap=approval_id,
        c=command_id,
        d=json.dumps(details, default=str),
    )


def virtual_day_count(conn: Connection, grant_id: UUID) -> int:
    """Sends under this grant on the current virtual day (design 3.6: daily limits in virtual days)."""
    return int(
        db.scalar(
            conn,
            """
        WITH c AS (SELECT epoch_real, epoch_virtual, scale FROM clock)
        SELECT count(*) FROM outbox o, c
        WHERE o.grant_id = :g AND o.state <> 'cancelled' AND
              (c.epoch_virtual + (o.created_at - c.epoch_real) * c.scale)::date =
              (c.epoch_virtual + (now() - c.epoch_real) * c.scale)::date""",
            g=grant_id,
        )
        or 0
    )


def refuse(conn: Connection, inp: PerformIn, cid: UUID, code: str) -> PerformOut:
    audit(
        conn,
        "refused",
        inp.target,
        inp.actor_account_id,
        inp.approval_id,
        cid,
        {"code": code, "step": inp.step, "round": inp.round},
    )
    return PerformOut(ok=False, code=code, command_id=cid)


def expected_job_message(conn: Connection, job_id: UUID, step: str) -> Any:
    from closeout.messages import job_messages

    msgs = job_messages(conn, job_id, (step,))
    return msgs[0] if msgs else None


def perform(conn: Connection, inp: PerformIn) -> PerformOut:
    """Send one approved email (action send_email). Later slices add share links and town requests."""
    cid = send_command_id(inp.target, inp.round, inp.step)
    fp = fingerprint(inp)
    # 1. Same command already done? Checked before any expiry, so a retry after a delay still succeeds.
    done = db.one(conn, "SELECT id, fingerprint FROM outbox WHERE command_id = :c", c=cid)
    if done is not None:
        if done["fingerprint"] != fp:
            return refuse(conn, inp, cid, "command_conflict")
        return PerformOut(ok=True, code="already_done", outbox_id=done["id"], command_id=cid)
    # 2. The approval.
    a = db.one(conn, "SELECT * FROM approvals WHERE id = :id FOR UPDATE", id=inp.approval_id)
    if a is None:
        return refuse(conn, inp, cid, "unknown_nonce")
    if a["voided_at"] is not None:
        return refuse(conn, inp, cid, "voided")
    if a["used_by_command"] is not None and a["used_by_command"] != cid:
        return refuse(conn, inp, cid, "already_used")
    if not inp.target.startswith("job-"):
        return refuse(conn, inp, cid, "wrong_target")
    job_id = UUID(inp.target[4:])
    job = db.one(conn, "SELECT * FROM jobs WHERE id = :id FOR SHARE", id=job_id)
    if job is None:
        return refuse(conn, inp, cid, "wrong_target")
    msg = expected_job_message(conn, job_id, inp.step)
    if msg is None:
        return refuse(conn, inp, cid, "wrong_step")
    # 3. The signature, and that the claims match what is being done now.
    expected = {
        "action": inp.action,
        "target": inp.target,
        "round": job["round"],
        "step": inp.step,
        "account_id": str(a["account_id"]),
        "to_address_hmac": email_hmac(a["to_address"] or ""),
        "subject_sha256": ap.sha256_hex(msg.subject),
        "body_template_sha256": ap.sha256_hex(msg.body_template),
        "content_rev": job["content_rev"],
    }
    if inp.round != job["round"]:
        return refuse(conn, inp, cid, "wrong_round")
    checked = ap.check_signature(a["token"], expected, keys.public_key())
    if isinstance(checked, ap.Refused):
        return refuse(conn, inp, cid, checked.reason)
    claims = checked.claims
    # 4. Real expiry, by the database's clock when this runs.
    if db.scalar(conn, "SELECT now() > :e", e=a["expires_at"]):
        return refuse(conn, inp, cid, "expired")
    # 5. Ownership: the approver owns the job, and the recipient is this job's homeowner.
    acct = db.one(conn, "SELECT contractor_id FROM accounts WHERE id = :a", a=a["account_id"])
    if acct is None or acct["contractor_id"] != job["contractor_id"]:
        return refuse(conn, inp, cid, "not_owner")
    if a["recipient_id"] != job["homeowner_recipient_id"]:
        return refuse(conn, inp, cid, "wrong_recipient")
    # 6. The grant: active, same version, under the daily limit in virtual days.
    g = db.one(conn, "SELECT * FROM grants WHERE id = :g FOR UPDATE", g=UUID(claims.grant_id))
    if (
        g is None
        or g["account_id"] != a["account_id"]
        or g["revoked_at"] is not None
        or g["version"] != claims.grant_version
        or inp.action not in g["actions"]
    ):
        return refuse(conn, inp, cid, "grant_revoked")
    if g["expires_at"] is not None and db.scalar(conn, "SELECT now() > :e", e=g["expires_at"]):
        return refuse(conn, inp, cid, "grant_expired")
    if virtual_day_count(conn, g["id"]) >= g["daily_limit"]:
        return refuse(conn, inp, cid, "over_limit")
    # 7. The do-not-contact list.
    if db.scalar(
        conn, "SELECT count(*) FROM suppression WHERE email_hmac = :h", h=email_hmac(a["to_address"] or "")
    ):
        return refuse(conn, inp, cid, "suppressed")
    # 8. Do it: one outbox row, the approval used, an audit row.
    oid = db.scalar(
        conn,
        """INSERT INTO outbox (command_id, message_id, approval_id, job_id, round, step, recipient_id,
                             grant_id, to_address, subject, body_template, fingerprint)
                             VALUES (:c, :m, :a, :j, :r, :s, :rec, :g, :to, :sub, :body, :fp) RETURNING id""",
        c=cid,
        m=message_id(cid),
        a=a["id"],
        j=job_id,
        r=inp.round,
        s=inp.step,
        rec=a["recipient_id"],
        g=g["id"],
        to=a["to_address"],
        sub=a["subject"],
        body=a["body_template"],
        fp=fp,
    )
    db.run(
        conn, "UPDATE approvals SET used_by_command = :c, used_at = now() WHERE id = :id", c=cid, id=a["id"]
    )
    audit(
        conn,
        inp.action,
        inp.target,
        inp.actor_account_id or a["account_id"],
        a["id"],
        cid,
        {"outbox_id": str(oid), "step": inp.step, "round": inp.round},
    )
    return PerformOut(ok=True, code="queued", outbox_id=oid, command_id=cid)


def new_approval(conn: Connection, account_id: UUID, msg: Any, grant: dict[str, Any], days: int = 30) -> UUID:
    """Make and sign one approval for one message (web app only: it holds the private key)."""
    import secrets
    from datetime import timedelta

    now = db.scalar(conn, "SELECT date_trunc('second', now())")
    exp = now + timedelta(days=days)
    claims = ap.Claims(
        action="send_email",
        account_id=str(account_id),
        target=msg.target,
        round=msg.round,
        step=msg.step,
        content_rev=msg.content_rev,
        to_address_hmac=email_hmac(msg.to_address),
        subject_sha256=ap.sha256_hex(msg.subject),
        body_template_sha256=ap.sha256_hex(msg.body_template),
        grant_id=str(grant["id"]),
        grant_version=grant["version"],
        exp=exp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        nonce=ap.b64e(secrets.token_bytes(16)),
    )
    token = ap.sign(claims, keys.private_key())
    return db.scalar(
        conn,
        """INSERT INTO approvals (nonce, account_id, action, target, round, step, content_rev,
                              recipient_id, to_address, subject, body_template, claims, token, expires_at)
                              VALUES (:n, :a, 'send_email', :t, :r, :s, :cr, :rec, :to, :sub, :body,
                                      CAST(:cl AS jsonb), :tok, :e) RETURNING id""",
        n=claims.nonce,
        a=account_id,
        t=msg.target,
        r=msg.round,
        s=msg.step,
        cr=msg.content_rev,
        rec=msg.recipient_id,
        to=msg.to_address,
        sub=msg.subject,
        body=msg.body_template,
        cl=claims.model_dump_json(),
        tok=token,
        e=exp,
    )
