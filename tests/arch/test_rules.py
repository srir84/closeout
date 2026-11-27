"""Architecture rules (test strategy 5.2): checked by reading the source."""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

SRC = Path(__file__).parents[2] / "src" / "closeout"
pytestmark = [pytest.mark.slice("S0")]

CORE_ALLOWED = {"pydantic", "rapidfuzz", "cryptography", "closeout"}
PERSON_WORDS = (
    "name",
    "email",
    "phone",
    "address",
    "street",
    "body",
    "subject",
    "text",
    "note",
    "comment",
    "display",
    "to_addr",
)


def py_files(sub: str) -> list[Path]:
    return sorted((SRC / sub).rglob("*.py"))


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module.split(".")[0])
    return mods


@pytest.mark.parametrize("path", py_files("core"), ids=lambda p: p.name)
def test_core_imports_only_stdlib_pydantic_rapidfuzz_cryptography(path: Path) -> None:
    bad = {m for m in imports(path) if m not in sys.stdlib_module_names and m not in CORE_ALLOWED}
    assert not bad, f"{path.name} imports {bad}"
    for m in imports(path):
        if m == "closeout":
            src = path.read_text()
            assert "closeout.core" in src or "from closeout" not in src


def calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            out.append(ast.unparse(node.func))
    return out


FORBIDDEN_TIME = {
    "datetime.now",
    "datetime.utcnow",
    "time.time",
    "date.today",
    "datetime.today",
    "uuid.uuid4",
    "random.random",
    "random.choice",
    "secrets.token_bytes",
    "secrets.token_urlsafe",
    "os.urandom",
}


@pytest.mark.parametrize("path", py_files("core") + py_files("workflows"), ids=lambda p: p.name)
def test_core_and_workflows_read_no_clock_and_make_no_random_values(path: Path) -> None:
    bad = [
        c
        for c in calls(path)
        if c in FORBIDDEN_TIME or c.endswith((".now", ".utcnow")) and not c.startswith("workflow.")
    ]
    assert not bad, f"{path.name} calls {bad}"


@pytest.mark.parametrize("path", py_files("activities"), ids=lambda p: p.name)
def test_activities_never_call_datetime_now(path: Path) -> None:
    bad = [c for c in calls(path) if c in {"datetime.now", "datetime.utcnow", "time.time", "date.today"}]
    assert not bad, f"{path.name} calls {bad}: use the database's now() or the clock row"


def test_workflows_do_no_io() -> None:
    io_modules = {"httpx", "sqlalchemy", "psycopg", "smtplib", "socket", "requests", "urllib", "subprocess"}
    for path in py_files("workflows"):
        bad = imports(path) & io_modules
        assert not bad, f"{path.name} imports {bad}"
        assert "closeout.db" not in path.read_text() and "from closeout import db" not in path.read_text()


def payload_models() -> list[type[BaseModel]]:
    from closeout.workflows import payloads

    return [
        m
        for _, m in inspect.getmembers(payloads, inspect.isclass)
        if issubclass(m, BaseModel) and m.__module__ == payloads.__name__
    ]


@pytest.mark.parametrize("model", payload_models(), ids=lambda m: m.__name__)
def test_payload_models_carry_no_person_fields(model: type[BaseModel]) -> None:
    for name, field in model.model_fields.items():
        assert not any(w in name.lower() for w in PERSON_WORDS), f"{model.__name__}.{name} looks personal"
        ann = str(field.annotation).replace("dict[str, ", "dict[")  # keys are code names
        assert "str" not in ann or name in model_string_allow(model), f"{model.__name__}.{name} is free text"


def model_string_allow(model: type[BaseModel]) -> set[str]:
    """String fields that hold codes, not text. Each is listed here on purpose."""
    return set(getattr(model, "CODE_FIELDS", ())) | {
        "kind",
        "code",
        "state",
        "finished_state",
        "step",
        "reader",
        "purpose",
        "result",
        "reason",
        "work_type",
        "mode",
        "status",
    }


def test_command_codes_refuse_typed_text() -> None:
    import uuid
    from datetime import UTC, datetime

    from closeout.workflows.payloads import CommandRef

    with pytest.raises(ValueError):
        CommandRef(
            command_id=uuid.uuid4(),
            kind="answer",
            saved_at=datetime.now(UTC),
            codes={"answer": "Dana Whitfield, 14 Osprey Ln"},
        )
    ok = CommandRef(
        command_id=uuid.uuid4(), kind="answer", saved_at=datetime.now(UTC), codes={"answer": "confirm"}
    )
    assert ok.codes == {"answer": "confirm"}


def test_id_names_and_kinds_refuse_personal_text() -> None:
    import uuid
    from datetime import UTC, datetime

    from closeout.workflows.payloads import CommandRef

    with pytest.raises(ValueError):
        CommandRef(
            command_id=uuid.uuid4(),
            kind="press",
            saved_at=datetime.now(UTC),
            ids={"alice@example.com": uuid.uuid4()},
        )  # publish-check: allow
    with pytest.raises(ValueError):
        CommandRef(command_id=uuid.uuid4(), kind="Dana Whitfield", saved_at=datetime.now(UTC))


def event_models() -> list[type[BaseModel]]:
    from closeout.core.events import kinds

    return sorted(set(kinds().values()), key=lambda m: m.__name__)


@pytest.mark.slice("S2")
@pytest.mark.req("R-REC-1")
@pytest.mark.parametrize("model", event_models(), ids=lambda m: m.__name__)
def test_event_payloads_carry_no_person_fields(model: type[BaseModel]) -> None:
    for name, field in model.model_fields.items():
        assert not any(w in name.lower() for w in PERSON_WORDS), f"{model.__name__}.{name} looks personal"
        ann = str(field.annotation)
        if "str" in ann:
            assert name in {
                "reason",
                "field",
                "step",
                "pk",
                "pin",
                "via",
                "origin",
                "answer",
                "state",
                "result",
                "permit_pk",
            }, f"{model.__name__}.{name} is free text"
    assert model.model_config.get("extra") == "forbid"
