"""`perform`: the one permission check every action goes through (design 3.6, R-SAFE-2, rule 3).

It is the only code that writes to `outbox`, share `links` and approved `town_requests`. Everything happens
in one database transaction, so a retry either finds the first result or does the whole thing once.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid5

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


def load_target(conn: Connection, target: str, step: str) -> dict[str, Any] | None:
    """What is being acted on, locked for share, with the message it should send now."""
    from closeout.messages import town_messages

    if target.startswith("job-"):
        job = db.one(conn, "SELECT * FROM jobs WHERE id = :id FOR SHARE", id=UUID(target[4:]))
        if job is None:
            return None
        m = expected_job_message(conn, job["id"], step)
        return {
            "kind": "job",
            "job_id": job["id"],
            "town_request_id": None,
            "round": job["round"],
            "content_rev": job["content_rev"],
            "recipient_id": m.recipient_id if m else None,
            "contractor_id": job["contractor_id"],
            "origin": job["origin"],
            "pin": job["pin"],
            "msg": m,
        }
    if target.startswith("town-"):
        tr = db.one(conn, "SELECT * FROM town_requests WHERE id = :id FOR SHARE", id=UUID(target[5:]))
        if tr is None:
            return None
        msgs = town_messages(conn, tr["id"], (step,)) if step in ("request", "reminder") else []
        clerk = db.scalar(
            conn,
            """SELECT tw.clerk_recipient_id FROM parcels pa JOIN towns tw ON tw.code = pa.town
                                   WHERE pa.pin = :p""",
            p=tr["home_pin"],
        )
        return {
            "kind": "town",
            "job_id": None,
            "town_request_id": tr["id"],
            "round": tr["round"],
            "content_rev": tr["content_rev"],
            "recipient_id": clerk,
            "home_pin": tr["home_pin"],
            "msg": msgs[0] if msgs else None,
        }
    return None


def owns(conn: Connection, account_id: UUID, t: dict[str, Any]) -> bool:
    if t["kind"] == "job" and t["origin"] == "homeowner":  # S9: the home's owner asks the contractor
        return db.scalar(conn, "SELECT owner_account_id FROM homes WHERE pin = :p", p=t["pin"]) == account_id
    if t["kind"] == "job":
        return (
            db.scalar(conn, "SELECT contractor_id FROM accounts WHERE id = :a", a=account_id)
            == t["contractor_id"]
        )
    return db.scalar(conn, "SELECT owner_account_id FROM homes WHERE pin = :p", p=t["home_pin"]) == account_id


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
    t = load_target(conn, inp.target, inp.step)
    if t is None:
        return refuse(conn, inp, cid, "wrong_target")
    job = t  # round, content_rev
    msg = t["msg"]
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
    # The stored row must be the one that was signed: same nonce, and the signed expiry is the one that counts.
    if claims.nonce != a["nonce"]:
        return refuse(conn, inp, cid, "malformed")
    # 4. Real expiry, by the database's clock when this runs, against the signed expiry.
    if db.scalar(conn, "SELECT now() >= CAST(:e AS timestamptz)", e=claims.exp):
        return refuse(conn, inp, cid, "expired")
    # 5. Ownership: the approver owns the target, and the recipient belongs to it
    #    (this job's homeowner, this town's clerk).
    if not owns(conn, a["account_id"], t):
        return refuse(conn, inp, cid, "not_owner")
    if a["recipient_id"] != t["recipient_id"]:
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
    # 7. The do-not-contact list, under the same lock unsubscribe and the sender take.
    addr = email_hmac(a["to_address"] or "")
    db.scalar(conn, "SELECT pg_advisory_xact_lock(hashtextextended(:h, 7))", h=addr)
    if db.scalar(conn, "SELECT count(*) FROM suppression WHERE email_hmac = :h", h=addr):
        return refuse(conn, inp, cid, "suppressed")
    # 8. Do it: one outbox row, the approval used, an audit row.
    oid = db.scalar(
        conn,
        """INSERT INTO outbox (command_id, message_id, approval_id, job_id, town_request_id, round, step, recipient_id,
                             grant_id, to_address, subject, body_template, fingerprint)
                             VALUES (:c, :m, :a, :j, :tr, :r, :s, :rec, :g, :to, :sub, :body, :fp) RETURNING id""",
        c=cid,
        m=message_id(cid),
        a=a["id"],
        j=t["job_id"],
        tr=t["town_request_id"],
        r=inp.round,
        s=inp.step,
        rec=a["recipient_id"],
        g=g["id"],
        to=a["to_address"],
        sub=msg.subject,  # exactly the text whose hashes the signature covers
        body=msg.body_template,
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


# --- share links (S10) --------------------------------------------------------------------------------


class ShareIn(BaseModel):
    approval_id: UUID
    pin: str
    role: str
    fields: tuple[str, ...]
    link_hash: str
    link_expires: str  # UTC, to the second, ending in Z
    actor_account_id: UUID | None = None


def fields_sha(fields: tuple[str, ...]) -> str:
    return ap.sha256_hex(",".join(sorted(fields)))


def share_command_id(pin: str, link_hash: str) -> UUID:
    from closeout.core.ids import NAMESPACE

    return uuid5(NAMESPACE, f"share:home-{pin}:{link_hash}")


def perform_share(conn: Connection, inp: ShareIn) -> PerformOut:
    """Make one share link (R-REP-2, R-SAFE-2). Same order as perform: same command, approval, signature,
    expiry, ownership, grant; then the link, the approval used and an audit row."""
    target = f"home-{inp.pin}"
    cid = share_command_id(inp.pin, inp.link_hash)
    done = db.one(conn, "SELECT id FROM links WHERE token_hash = :h", h=inp.link_hash)
    if done is not None:
        return PerformOut(ok=True, code="already_done", outbox_id=None, command_id=cid)
    a = db.one(conn, "SELECT * FROM approvals WHERE id = :id FOR UPDATE", id=inp.approval_id)
    if a is None:
        return _refuse_share(conn, inp, cid, "unknown_nonce")
    if a["voided_at"] is not None:
        return _refuse_share(conn, inp, cid, "voided")
    if a["used_by_command"] is not None and a["used_by_command"] != cid:
        return _refuse_share(conn, inp, cid, "already_used")
    expected = {
        "action": "share_report",
        "target": target,
        "account_id": str(a["account_id"]),
        "role": inp.role,
        "fields_sha256": fields_sha(inp.fields),
        "link_hash": inp.link_hash,
        "link_expires": inp.link_expires,
    }
    checked = ap.check_share_signature(a["token"], expected, keys.public_key())
    if isinstance(checked, ap.Refused):
        return _refuse_share(conn, inp, cid, checked.reason)
    claims = checked.claims
    if claims.nonce != a["nonce"]:
        return _refuse_share(conn, inp, cid, "malformed")
    if db.scalar(conn, "SELECT now() >= CAST(:e AS timestamptz)", e=claims.exp):
        return _refuse_share(conn, inp, cid, "expired")
    owner = db.scalar(conn, "SELECT owner_account_id FROM homes WHERE pin = :p", p=inp.pin)
    if owner is None or owner != a["account_id"]:
        return _refuse_share(conn, inp, cid, "not_owner")
    g = db.one(conn, "SELECT * FROM grants WHERE id = :g FOR UPDATE", g=UUID(claims.grant_id))
    if (
        g is None
        or g["account_id"] != a["account_id"]
        or g["revoked_at"] is not None
        or g["version"] != claims.grant_version
        or "share_report" not in g["actions"]
    ):
        return _refuse_share(conn, inp, cid, "grant_revoked")
    if g["expires_at"] is not None and db.scalar(conn, "SELECT now() > :e", e=g["expires_at"]):
        return _refuse_share(conn, inp, cid, "grant_expired")  # review finding 12: the same check as email
    lid = db.scalar(
        conn,
        """INSERT INTO links (token_hash, purpose, target, role, fields_allowed, expires_at)
                             VALUES (:h, 'share', :t, :r, :f, CAST(:e AS timestamptz)) RETURNING id""",
        h=inp.link_hash,
        t=target,
        r=inp.role,
        f=list(inp.fields),
        e=inp.link_expires,
    )
    db.run(
        conn, "UPDATE approvals SET used_by_command = :c, used_at = now() WHERE id = :id", c=cid, id=a["id"]
    )
    audit(
        conn,
        "share_report",
        target,
        inp.actor_account_id or a["account_id"],
        a["id"],
        cid,
        {"link_id": str(lid), "role": inp.role, "fields": sorted(inp.fields)},
    )
    return PerformOut(ok=True, code="shared", outbox_id=None, command_id=cid)


def _refuse_share(conn: Connection, inp: ShareIn, cid: UUID, code: str) -> PerformOut:
    audit(conn, "refused", f"home-{inp.pin}", inp.actor_account_id, inp.approval_id, cid, {"code": code})
    return PerformOut(ok=False, code=code, command_id=cid)


def new_share_approval(
    conn: Connection,
    account_id: UUID,
    pin: str,
    role: str,
    fields: tuple[str, ...],
    link_hash: str,
    link_expires: str,
    grant: dict[str, Any],
) -> UUID:
    """Sign one share approval (web app only)."""
    import secrets
    from datetime import timedelta

    now = db.scalar(conn, "SELECT date_trunc('second', now())")
    exp = now + timedelta(days=1)
    claims = ap.ShareClaims(
        action="share_report",
        account_id=str(account_id),
        target=f"home-{pin}",
        role=role,
        fields_sha256=fields_sha(fields),
        link_hash=link_hash,
        link_expires=link_expires,
        grant_id=str(grant["id"]),
        grant_version=grant["version"],
        exp=exp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        nonce=ap.b64e(secrets.token_bytes(16)),
    )
    return db.scalar(
        conn,
        """INSERT INTO approvals (nonce, account_id, action, target, claims, token, expires_at)
                              VALUES (:n, :a, 'share_report', :t, CAST(:cl AS jsonb), :tok, :e) RETURNING id""",
        n=claims.nonce,
        a=account_id,
        t=f"home-{pin}",
        cl=claims.model_dump_json(),
        tok=ap.sign(claims, keys.private_key()),
        e=exp,
    )
