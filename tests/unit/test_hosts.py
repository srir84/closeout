"""The demo sign-in answers only the hosts in ALLOWED_HOSTS, and pages carry safety headers."""

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.slice("S0"), pytest.mark.req("R-SAFE-4")]


@pytest.fixture
def client() -> TestClient:
    from closeout.app.main import create_app

    return TestClient(create_app(), base_url="http://127.0.0.1:8000")


@pytest.mark.parametrize("host", ["evil.example", "192.168.1.20:8000", "127.0.0.1:9000", "app.attacker.test"])
def test_unknown_host_refused(client: TestClient, host: str) -> None:
    r = client.get("/healthz", headers={"Host": host})
    assert r.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "app:8000"])
def test_known_host_answers_with_safety_headers(client: TestClient, host: str) -> None:
    r = client.get("/healthz", headers={"Host": host})
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "no-referrer"


def test_templates_autoescape() -> None:
    from closeout.app.deps import templates

    t = templates.env.from_string("{{ x }}")
    assert templates.env.autoescape is not False
    assert "<b>" not in templates.env.get_template("error.html").render(
        request=None, message="<b>hi</b>", account=None, csrf="", clock={}
    )
    assert t is not None


@pytest.mark.slice("S4")
@pytest.mark.req("R-IMP-1")
def test_oversized_upload_refused_before_it_is_read(client: TestClient) -> None:
    from closeout.app.security import MAX_UPLOAD_BYTES

    r = client.post(
        "/c/import",
        headers={
            "content-type": "multipart/form-data; boundary=x",
            "content-length": str(MAX_UPLOAD_BYTES + 1),
        },
        content=b"--x--",
    )
    assert r.status_code == 413
