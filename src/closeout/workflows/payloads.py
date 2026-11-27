"""Payloads that cross into Temporal: workflow inputs, update arguments, signals, activity results.

Rule 7: these carry IDs, codes, dates and amounts only. Never names, emails, addresses,
typed text or email bodies. tests/arch/test_payloads.py checks every model here.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

CODE = re.compile(r"^[a-z0-9_.:-]{1,64}$")


class ClockIn(BaseModel):
    epoch_real: datetime
    epoch_virtual: datetime
    scale: float


class CommandRef(BaseModel):
    """What an update carries: the command's ID, kind and IDs only (design 3.5)."""

    command_id: UUID
    kind: str
    saved_at: datetime
    account_id: UUID | None = None
    link_id: UUID | None = None
    ids: dict[str, UUID] = Field(default_factory=dict)
    codes: dict[str, str] = Field(default_factory=dict)
    CODE_FIELDS: ClassVar[tuple[str, ...]] = ("codes",)

    @field_validator("codes")
    @classmethod
    def _codes_only(cls, v: dict[str, str]) -> dict[str, str]:
        for k, val in v.items():
            if not CODE.match(k) or not CODE.match(val):
                raise ValueError("codes must be short lower-case identifiers, never typed text")
        return v


class CommandResult(BaseModel):
    command_id: UUID
    ok: bool
    code: str
    state: str | None = None


class PingIn(BaseModel):
    ping_id: UUID
    clock: ClockIn
    wait_virtual_seconds: float


class PingOut(BaseModel):
    ping_id: UUID
    presses: int
    finished_state: str
