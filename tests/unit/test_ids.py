import json
import uuid
from pathlib import Path

import pytest

from closeout.core import ids

pytestmark = [pytest.mark.slice("S0")]
VECTORS = json.loads((Path(__file__).parents[1] / "vectors" / "ids.json").read_text())


def test_namespace_is_fixed() -> None:
    assert str(ids.NAMESPACE) == VECTORS["namespace"]


@pytest.mark.parametrize("v", VECTORS["send_commands"])
def test_send_command_and_message_ids(v: dict[str, str]) -> None:
    cid = ids.send_command_id(v["target"], int(v["round"]), v["step"])
    assert str(cid) == v["command_id"]
    assert ids.message_id(cid) == v["message_id"]


def test_event_id_is_stable_and_needs_a_key() -> None:
    assert ids.event_id("cmd:x:job.answered") == ids.event_id("cmd:x:job.answered")
    assert ids.event_id("a") != ids.event_id("b")
    with pytest.raises(ValueError, match="^event key must not be empty$"):
        ids.event_id("")
    assert isinstance(ids.event_id("k"), uuid.UUID)
