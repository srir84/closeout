"""R-SAFE-4: everything runs on this computer only. Reads the resolved Docker settings."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
pytestmark = [pytest.mark.slice("S0")]


@pytest.fixture(scope="module")
def compose() -> dict:
    if not shutil.which("docker"):
        pytest.skip("docker not available")
    out = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "deploy/compose.yaml"), "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return json.loads(out.stdout)


@pytest.mark.req("R-SAFE-4")
def test_every_published_port_is_loopback(compose: dict) -> None:
    published = []
    for name, svc in compose["services"].items():
        for p in svc.get("ports", []):
            published.append((name, p.get("host_ip"), p.get("published")))
            assert p.get("host_ip") == "127.0.0.1", f"{name} publishes {p} beyond this computer"
    assert {n for n, _, _ in published} == {"app", "temporal", "mailpit"}


@pytest.mark.req("R-SAFE-4")
def test_postgres_and_temporal_main_port_not_published(compose: dict) -> None:
    assert not compose["services"]["postgres"].get("ports")
    for p in compose["services"]["temporal"].get("ports", []):
        assert str(p.get("target")) != "7233"


@pytest.mark.req("R-OUT-7")
def test_mailpit_relay_off_and_saved_to_volume(compose: dict) -> None:
    env = compose["services"]["mailpit"]["environment"]
    assert env["MP_SMTP_RELAY_ALL"] == "false"
    assert "MP_SMTP_RELAY_HOST" not in env
    assert env["MP_DATABASE"].startswith("/data/")
    assert any(
        v["target"] == "/data" and v["type"] == "volume" for v in compose["services"]["mailpit"]["volumes"]
    )


@pytest.mark.req("R-RUN-6")
def test_temporal_saves_to_a_volume(compose: dict) -> None:
    t = compose["services"]["temporal"]
    assert "--db-filename" in t["command"]
    assert any(v["target"] == "/data" and v["type"] == "volume" for v in t["volumes"])


def test_only_the_web_app_gets_the_private_key(compose: dict) -> None:
    for name, svc in compose["services"].items():
        targets = {v["target"] for v in svc.get("volumes", [])}
        has_private = "/keys/approval_ed25519.key" in targets
        assert has_private == (name == "app"), name
        assert "/keys" not in targets, "key files must be mounted one by one"


@pytest.mark.req("R-SAFE-4")
def test_readme_states_the_hosted_mode_exception() -> None:
    text = (ROOT / "README.md").read_text().lower()
    assert "hosted mode" in text and "outside" in text and "made-up documents only" in text
