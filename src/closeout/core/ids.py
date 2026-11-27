"""Deterministic IDs (design 3.6, 3.9)."""

from __future__ import annotations

import uuid

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://closeout.test/ids")


def send_command_id(target: str, round_: int, step: str) -> uuid.UUID:
    """The command ID for one message send: uuid5(NAMESPACE, f"{target}:{round}:{step}")."""
    return uuid.uuid5(NAMESPACE, f"{target}:{round_}:{step}")


def message_id(command_id: uuid.UUID) -> str:
    """The email Message-ID for a send command."""
    return f"<{uuid.uuid5(NAMESPACE, 'msg:' + str(command_id))}@closeout.test>"


def event_id(event_key: str) -> uuid.UUID:
    """An event's ID from its key, so a retried step can't add it twice."""
    if not event_key:
        raise ValueError("event key must not be empty")
    return uuid.uuid5(NAMESPACE, event_key)
