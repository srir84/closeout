"""Rule 2: only gateway/ talks to models. Models get no tools (R-SAFE-1)."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[2] / "src" / "closeout"
pytestmark = [pytest.mark.slice("S3"), pytest.mark.req("R-SAFE-1")]
MODEL_MARKERS = (
    "/api/chat",
    "/api/generate",
    "chat/completions",
    "api.openai.com",
    "anthropic",
    "ollama_url",
    "hosted_api_key",
)


@pytest.mark.parametrize(
    "path",
    sorted(p for p in SRC.rglob("*.py") if "gateway" not in p.parts),
    ids=lambda p: str(p.relative_to(SRC)),
)
def test_nothing_outside_the_gateway_calls_a_model(path: Path) -> None:
    text = path.read_text()
    if path.name == "settings.py":
        return
    for m in MODEL_MARKERS:
        assert m not in text, f"{path.relative_to(SRC)} mentions {m}"
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(("openai", "ollama")):
            pytest.fail(f"{path.name} imports {node.module}")


def test_the_gateway_never_sends_tools() -> None:
    text = (SRC / "gateway" / "core.py").read_text()
    assert '"tools"' not in text and "tool_choice" not in text and "functions" not in text
