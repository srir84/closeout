"""Approvals: signed claims for exactly one action (design 3.6, R-OUT-1). Pure.

Token = base64url_nopad(canonical claims JSON) + "." + base64url_nopad(Ed25519 signature).
A token that doesn't re-encode to exactly the same string is `malformed`.
Expiry is not checked here: it needs real time, so `perform` checks it with the database's now().
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict


class Claims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    v: int = 1
    action: str
    account_id: str
    target: str
    round: int
    step: str
    content_rev: int
    to_address_hmac: str
    subject_sha256: str
    body_template_sha256: str
    grant_id: str
    grant_version: int
    exp: str
    nonce: str


class Ok(BaseModel):
    claims: Claims


class Refused(BaseModel):
    reason: str


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def b64d(s: str) -> bytes:
    if not s or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in s):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical(claims: Claims | dict[str, Any]) -> bytes:
    data = claims.model_dump() if isinstance(claims, Claims) else claims
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sign(claims: Claims, key: Ed25519PrivateKey) -> str:
    body = canonical(claims)
    return b64e(body) + "." + b64e(key.sign(body))


def refusal_order() -> tuple[str, ...]:
    """Expected claims are compared in this order; the first difference is the reason."""
    return (
        "action",
        "target",
        "round",
        "step",
        "account_id",
        "to_address_hmac",
        "subject_sha256",
        "body_template_sha256",
        "content_rev",
    )


def reason_for(field: str) -> str:
    return {
        "action": "wrong_action",
        "target": "wrong_target",
        "round": "wrong_round",
        "step": "wrong_step",
        "account_id": "wrong_account",
        "to_address_hmac": "wrong_recipient",
        "subject_sha256": "subject_changed",
        "body_template_sha256": "body_changed",
        "content_rev": "stale_content",
    }[field]


def check_signature(token: str, expected: dict[str, Any], public_key: Ed25519PublicKey) -> Ok | Refused:
    """Ok(claims) when the token is well formed, signed by our key, and matches what is being done now."""
    parts = token.split(".")
    if len(parts) != 2:
        return Refused(reason="malformed")
    try:
        body, sig = b64d(parts[0]), b64d(parts[1])
        data = json.loads(body.decode("ascii"))
        claims = Claims.model_validate(data)
    except (ValueError, UnicodeDecodeError):
        return Refused(reason="malformed")
    if b64e(canonical(claims)) + "." + b64e(sig) != token or canonical(claims) != body:
        return Refused(reason="malformed")
    try:
        public_key.verify(sig, body)
    except InvalidSignature:
        return Refused(reason="bad_signature")
    have = claims.model_dump()
    for field in refusal_order():
        if field not in expected:
            continue
        a, b = str(have[field]), str(expected[field])
        if not hmac.compare_digest(a.encode(), b.encode()):
            return Refused(reason=reason_for(field))
    return Ok(claims=claims)
