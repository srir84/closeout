"""R-COR-3: banned phrases never appear. Every template and sentence the product can show is scanned."""

from pathlib import Path

import pytest

from closeout.core import permits
from closeout.core.wording import banned, check_text

pytestmark = [pytest.mark.req("R-COR-3")]
SRC = Path(__file__).parents[2] / "src" / "closeout"


@pytest.mark.slice("S5")
@pytest.mark.parametrize(
    "text",
    [
        "This is an OPEN  permit",
        "unpermitted work",
        "Un-permitted deck",
        "illegal",
        "a code Violation",
        "violations found",
    ],
)
def test_check_text_finds_banned_phrases(text: str) -> None:
    assert check_text(text)


@pytest.mark.slice("S5")
def test_clean_text_passes() -> None:
    assert check_text("Final approval not in state data. No matching permit found.") == []
    assert banned() == ("open permit", "unpermitted", "un-permitted", "illegal", "violation")


@pytest.mark.slice("S5")
@pytest.mark.parametrize("path", sorted((SRC / "app" / "templates").rglob("*")), ids=lambda p: p.name)
def test_templates_have_no_banned_phrases(path: Path) -> None:
    assert check_text(path.read_text()) == []


@pytest.mark.slice("S5")
def test_permit_sentences_have_no_banned_phrases() -> None:
    texts = [permits.STATEMENT_LINE, permits.FINAL_MISSING] + [
        permits.headline(r) for r in ("on_file", "possible", "no_details", "none", "no_check")
    ]
    assert all(check_text(t) == [] for t in texts)
