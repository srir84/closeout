"""Database access: SQLAlchemy Core over psycopg 3, Alembic migrations."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text

from closeout.settings import get_settings

MIGRATIONS = Path(__file__).parent / "migrations"


def normalise_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _json_serializer(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


@lru_cache
def _engine_for(url: str) -> Engine:
    return create_engine(
        normalise_url(url),
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        json_serializer=_json_serializer,
    )


def engine(url: str | None = None) -> Engine:
    return _engine_for(url or get_settings().database_url)


@contextmanager
def tx(url: str | None = None) -> Iterator[Connection]:
    """One transaction: commits on success, rolls back on error."""
    with engine(url).begin() as conn:
        yield conn


def migrate(url: str | None = None) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option(
        "sqlalchemy.url", normalise_url(url or get_settings().database_url).replace("%", "%%")
    )
    command.upgrade(cfg, "head")


def one(conn: Connection, sql: str, **params: Any) -> dict[str, Any] | None:
    row = conn.execute(text(sql), params).mappings().first()
    return dict(row) if row is not None else None


def all_rows(conn: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def scalar(conn: Connection, sql: str, **params: Any) -> Any:
    return conn.execute(text(sql), params).scalar()


def run(conn: Connection, sql: str, **params: Any) -> int:
    return conn.execute(text(sql), params).rowcount
