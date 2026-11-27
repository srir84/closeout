"""Shared pytest set-up: requirement/slice tags, a fresh database per test, keys, Mailpit, Temporal."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
PG_IMAGE = "postgres:16.16"
MAILPIT_IMAGE = "axllent/mailpit:v1.34.0"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--slice", action="store", default=None, help="run only tests tagged with this slice")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    want = config.getoption("--slice")
    if not want:
        return
    keep, drop = [], []
    for item in items:
        slices = {m.args[0] for m in item.iter_markers("slice")}
        (keep if want in slices else drop).append(item)
    config.hook.pytest_deselected(items=drop)
    items[:] = keep


# Results file read by tools/trace.py: one line per test with its req/slice tags and outcome.
_RESULTS: list[dict[str, object]] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):  # type: ignore[no-untyped-def]
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" or (rep.when == "setup" and rep.outcome != "passed"):
        _RESULTS.append({
            "test": item.nodeid,
            "reqs": sorted({m.args[0] for m in item.iter_markers("req")}),
            "slices": sorted({m.args[0] for m in item.iter_markers("slice")}),
            "outcome": rep.outcome,
        })


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    import json

    out = os.environ.get("CLOSEOUT_TRACE_RESULTS", str(ROOT / "build" / "trace" / "trace.jsonl"))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as f:
        for r in _RESULTS:
            f.write(json.dumps(r) + "\n")


# --- keys -------------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def test_keys(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from closeout import keys

    d = tmp_path_factory.mktemp("keys")
    keys.create(d)
    os.environ["CLOSEOUT_KEYS_DIR"] = str(d)
    keys.clear_cache()
    return d


# --- Postgres ---------------------------------------------------------------------------


def _docker_ok() -> bool:
    return shutil.which("docker") is not None


@pytest.fixture(scope="session")
def pg_server() -> Iterator[str]:
    """One Postgres container per test session; returns a base URL without a database name."""
    if os.environ.get("CLOSEOUT_TEST_PG"):
        yield os.environ["CLOSEOUT_TEST_PG"]
        return
    if not _docker_ok():
        pytest.skip("docker not available")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(PG_IMAGE, username="closeout", password="closeout", dbname="postgres",
                           driver="psycopg") as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        yield f"postgresql+psycopg://closeout:closeout@{host}:{port}"


@pytest.fixture(scope="session")
def pg_template(pg_server: str) -> str:
    """A migrated template database; each test copies it."""
    from sqlalchemy import create_engine, text

    from closeout import db

    admin = create_engine(f"{pg_server}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS closeout_template"))
        c.execute(text("CREATE DATABASE closeout_template"))
    db.migrate(f"{pg_server}/closeout_template")
    db.engine(f"{pg_server}/closeout_template").dispose()
    db._engine_for.cache_clear()
    admin.dispose()
    return "closeout_template"


@pytest.fixture
def database(pg_server: str, pg_template: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A fresh database for this test, copied from the template. Sets DATABASE_URL."""
    from sqlalchemy import create_engine, text

    from closeout import db
    from closeout.settings import get_settings

    name = "t_" + uuid.uuid4().hex[:12]
    admin = create_engine(f"{pg_server}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE {pg_template}'))
    url = f"{pg_server}/{name}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    db._engine_for.cache_clear()
    try:
        yield url
    finally:
        db.engine(url).dispose()
        db._engine_for.cache_clear()
        get_settings.cache_clear()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def seeded(database: str) -> str:
    """A fresh database with the demo seed and a clock at scale 1."""
    from closeout import cli

    cli.reset_data(scale=1.0)
    return database


# --- Mailpit ----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mailpit() -> Iterator[dict[str, object]]:
    if not _docker_ok():
        pytest.skip("docker not available")
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    c = DockerContainer(MAILPIT_IMAGE).with_exposed_ports(1025, 8025).with_env("MP_SMTP_RELAY_ALL", "false")
    with c:
        wait_for_logs(c, "accessible via", timeout=30)
        host = c.get_container_host_ip()
        yield {"host": host, "smtp_port": int(c.get_exposed_port(1025)),
               "api": f"http://{host}:{c.get_exposed_port(8025)}"}


# --- Temporal ---------------------------------------------------------------------------


def temporal_cli_path() -> str | None:
    return shutil.which("temporal")
