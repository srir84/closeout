"""S6: approval tokens (design 3.6, R-OUT-1). Every refusal reason, fixed vectors, and tamper properties."""

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import given, settings
from hypothesis import strategies as st

from closeout.core import approval as ap

pytestmark = [pytest.mark.slice("S6"), pytest.mark.req("R-OUT-1")]
KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUB = KEY.public_key()
CLAIMS = ap.Claims(
    action="send_email",
    account_id="00000000-0000-0000-0000-0000000000aa",
    target="job-00000000-0000-0000-0000-000000000001",
    round=1,
    step="first",
    content_rev=4,
    to_address_hmac="aa" * 32,
    subject_sha256="bb" * 32,
    body_template_sha256="cc" * 32,
    grant_id="00000000-0000-0000-0000-0000000000bb",
    grant_version=3,
    exp="2026-12-31T12:00:00Z",
    nonce="AAECAwQFBgcICQoLDA0ODw",
)
EXPECTED = {k: v for k, v in CLAIMS.model_dump().items() if k in ap.refusal_order()}
VECTOR = Path(__file__).parents[1] / "vectors" / "approval_token.json"


def token() -> str:
    return ap.sign(CLAIMS, KEY)


def test_fixed_vector() -> None:
    """Ed25519 is deterministic: the same claims and key give exactly this token."""
    v = json.loads(VECTOR.read_text())
    assert ap.canonical(CLAIMS).decode() == v["canonical"]
    assert token() == v["token"]
    assert isinstance(ap.check_signature(v["token"], EXPECTED, PUB), ap.Ok)


def test_good_token_returns_its_claims() -> None:
    got = ap.check_signature(token(), EXPECTED, PUB)
    assert isinstance(got, ap.Ok) and got.claims == CLAIMS


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("action", "share_report", "wrong_action"),
        ("target", "job-x", "wrong_target"),
        ("round", 2, "wrong_round"),
        ("step", "reminder_1", "wrong_step"),
        ("account_id", "someone-else", "wrong_account"),
        ("to_address_hmac", "dd" * 32, "wrong_recipient"),
        ("subject_sha256", "ee" * 32, "subject_changed"),
        ("body_template_sha256", "ff" * 32, "body_changed"),
        ("content_rev", 5, "stale_content"),
    ],
)
def test_every_mismatch_has_its_reason(field: str, value, reason: str) -> None:
    got = ap.check_signature(token(), {**EXPECTED, field: value}, PUB)
    assert got == ap.Refused(reason=reason)


def test_signature_by_another_key() -> None:
    other = Ed25519PrivateKey.generate()
    assert ap.check_signature(ap.sign(CLAIMS, other), EXPECTED, PUB) == ap.Refused(reason="bad_signature")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "abc",
        "a.b.c",
        "!!.??",
        token() + "=",
        token().replace(".", ".."),
        ap.b64e(b'{"v":1}') + "." + ap.b64e(b"x" * 64),
    ],
)
def test_malformed(bad: str) -> None:
    assert ap.check_signature(bad, EXPECTED, PUB) == ap.Refused(reason="malformed")


def test_non_canonical_body_is_malformed_even_if_signed() -> None:
    body = json.dumps(CLAIMS.model_dump(), indent=1).encode()  # same claims, not canonical bytes
    t = ap.b64e(body) + "." + ap.b64e(KEY.sign(body))
    assert ap.check_signature(t, EXPECTED, PUB) == ap.Refused(reason="malformed")


@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=0), st.integers(min_value=1, max_value=255))
def test_changing_any_byte_of_the_token_is_refused(pos: int, delta: int) -> None:
    t = token()
    i = pos % len(t)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_."
    new = alphabet[(alphabet.index(t[i]) + delta) % len(alphabet)] if t[i] in alphabet else "A"
    changed = t[:i] + new + t[i + 1 :]
    if changed == t:
        return
    assert isinstance(ap.check_signature(changed, EXPECTED, PUB), ap.Refused)


@settings(max_examples=200, deadline=None)
@given(st.sampled_from(list(ap.refusal_order())), st.text(min_size=1, max_size=20))
def test_changing_any_expected_claim_is_refused(field: str, value: str) -> None:
    if str(EXPECTED[field]) == value:
        return
    got = ap.check_signature(token(), {**EXPECTED, field: value}, PUB)
    assert got == ap.Refused(reason=ap.reason_for(field))
