"""S0 tool proof: saved histories replay, and an incompatible change fails the replay (design 3.8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from closeout.registry import workflows
from closeout.temporal import DATA_CONVERTER

pytestmark = [pytest.mark.slice("S0")]
HISTORIES = Path(__file__).parent / "histories"


def saved_histories() -> list[Path]:
    return sorted(p for p in HISTORIES.glob("*.json"))


def load(p: Path) -> WorkflowHistory:
    return WorkflowHistory.from_json(p.stem, json.loads(p.read_text()))


@pytest.mark.parametrize("path", saved_histories(), ids=lambda p: p.stem)
async def test_saved_history_replays(path: Path) -> None:
    await Replayer(workflows=workflows(), data_converter=DATA_CONVERTER).replay_workflow(load(path))


async def test_an_incompatible_change_fails_the_replay() -> None:
    from tests.replay.incompatible import PingChanged

    path = HISTORIES / "ping_pressed.json"
    with pytest.raises(Exception) as e:
        await Replayer(workflows=[PingChanged], data_converter=DATA_CONVERTER).replay_workflow(load(path))
    assert "nondetermin" in (str(e.value) + type(e.value).__name__).lower()
