"""S14: the README has what R-RUN-5 asks for (a doc check in CI)."""

from pathlib import Path

import pytest

README = (Path(__file__).parents[2] / "README.md").read_text()
pytestmark = [pytest.mark.slice("S14"), pytest.mark.req("R-RUN-5")]


@pytest.mark.parametrize(
    "heading",
    [
        "## Quickstart",
        "## What you'll see",
        "## If something goes wrong",
        "## Credits",
        "## Notice",
        "## Known demo limits",
    ],
)
def test_the_sections_are_there(heading: str) -> None:
    assert f"\n{heading}\n" in README


def test_it_says_people_are_made_up_and_not_tied_to_a_company() -> None:
    assert "Every person, home and company in it is made up" in README
    assert "not tied to any real business" in README


def test_the_quickstart_needs_only_docker_and_make() -> None:
    quick = README.split("## Quickstart", 1)[1].split("\n## ", 1)[0]
    assert "Docker" in quick and "make demo" in quick and "uv " not in quick


def test_the_hosted_warning_and_the_data_credits() -> None:
    assert "Hosted mode warning" in README
    assert "data.nj.gov" in README and "NJOGIS" in README
