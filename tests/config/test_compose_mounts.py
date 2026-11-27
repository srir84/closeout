"""Key files go only where they belong, and no service uses host networking (review fixes)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
pytestmark = [pytest.mark.slice("S0"), pytest.mark.req("R-SAFE-4")]

ALLOWED_KEYS = {
    "app": {"approval_ed25519.key", "approval_ed25519.pub", "hmac.key", "session.key"},
    "worker": {"approval_ed25519.pub", "hmac.key"},
    "sender": {"approval_ed25519.pub", "hmac.key"},
    "migrate": {"hmac.key"},
}


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


def test_each_key_file_is_mounted_at_its_own_name_and_only_where_allowed(compose: dict) -> None:
    for name, svc in compose["services"].items():
        mounted = set()
        for v in svc.get("volumes", []):
            src = str(v.get("source", ""))
            if ".secrets" in src or v["target"].startswith("/keys"):
                assert v["type"] == "bind" and v.get("read_only") is True, (name, v)
                fname = Path(src).name
                assert v["target"] == f"/keys/{fname}", f"{name}: {src} mounted at {v['target']}"
                assert Path(src).parent.name == ".secrets", (name, src)
                mounted.add(fname)
        assert mounted == ALLOWED_KEYS.get(name, set()), name


def test_no_host_networking_or_privileged_services(compose: dict) -> None:
    for name, svc in compose["services"].items():
        assert svc.get("network_mode") in (None, "default", "bridge"), name
        assert not svc.get("privileged"), name
