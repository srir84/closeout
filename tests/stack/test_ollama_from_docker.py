"""S0 tool proof: the worker container reaches Ollama on the host (owner's Mac; `live`)."""

import json

import pytest

from tests.stack import stack

pytestmark = [pytest.mark.stack, pytest.mark.live, pytest.mark.slice("S0")]


def test_worker_reaches_host_ollama() -> None:
    code = (
        "import httpx, json; r = httpx.get('http://host.docker.internal:11434/api/tags', timeout=5); "
        "print(json.dumps([m['name'] for m in r.json()['models']]))"
    )
    names = json.loads(stack.compose("exec", "-T", "worker", "python", "-c", code).stdout)
    assert any(n.startswith("qwen3-vl") for n in names)
