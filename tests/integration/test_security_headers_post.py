"""S14 security pass: cross-site posts and the size guard (Codex security review)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from closeout.app.main import create_app
from closeout.app.security import COOKIE
from closeout.seed import sid

pytestmark = [pytest.mark.slice("S14"), pytest.mark.req("R-SAFE-2")]


def client() -> TestClient:
    return TestClient(create_app(), base_url="http://127.0.0.1:8000")


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 200),  # tests, scripts and mail clients send neither header
        ({"Sec-Fetch-Site": "same-origin", "Origin": "null"}, 200),  # our own form under no-referrer
        ({"Origin": "http://127.0.0.1:8000"}, 200),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Origin": "http://evil.example"}, 403),
        ({"Origin": "http://127.0.0.1:9999"}, 403),
    ],
)
def test_sign_in_from_another_site_is_refused(seeded, headers: dict[str, str], status: int) -> None:
    c = client()
    r = c.post(
        "/signin", data={"account_id": str(sid("account:dana"))}, headers=headers, follow_redirects=False
    )
    assert (r.status_code == 403) == (status == 403), r.status_code
    if status == 403:
        assert COOKIE not in r.cookies


@pytest.mark.parametrize(
    "ctype",
    [
        "multipart/form-data; boundary=x",
        "Multipart/Form-Data; boundary=x",
        "application/x-www-form-urlencoded",
        "text/plain",
    ],
)
def test_a_huge_body_is_refused_whatever_its_type(seeded, ctype: str) -> None:
    r = client().post(
        "/c/import", content=b"x", headers={"content-type": ctype, "content-length": str(200 * 1024 * 1024)}
    )
    assert r.status_code == 413
