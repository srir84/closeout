"""S1: Sampleton is seeded, repeatable, labelled, and shares no address with Manasquan (R-DATA-6)."""

import hashlib
from pathlib import Path

import pytest
from data import make_sampleton

pytestmark = [pytest.mark.slice("S1"), pytest.mark.req("R-DATA-6")]


def digests(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


def test_same_files_on_rerun(tmp_path: Path) -> None:
    make_sampleton.write(tmp_path / "a")
    make_sampleton.write(tmp_path / "b")
    assert digests(tmp_path / "a") == digests(tmp_path / "b")
    assert digests(tmp_path / "a") == digests(make_sampleton.OUT), "committed Sampleton files are out of date"


def test_no_sampleton_address_or_street_in_manasquan() -> None:
    parcels, _, _ = make_sampleton.build()
    real = make_sampleton.manasquan_addresses()
    assert len(real) > 3000
    assert not {str(p["address"]) for p in parcels} & real
    assert not set(make_sampleton.STREETS) & make_sampleton.manasquan_streets()


def test_every_made_up_email_is_a_made_up_domain() -> None:
    _, _, world = make_sampleton.build()
    for p in world["people"]:
        assert p["email"].endswith((".example", ".test")), p["email"]


def test_persona_table_matches_the_spec() -> None:
    spec = (Path(__file__).parents[2] / "docs" / "01-SPEC.md").read_text()
    _, _, world = make_sampleton.build()
    for job in world["jobs"]:
        row = next(line for line in spec.splitlines() if line.startswith(f"| {job['code']} |"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[3].strip("`").split()[0].strip("`") == job["ends"], job["code"]
        assert (
            int(cells[4]) == job["marks"]
            and int(cells[5]) == job["emails"]
            and int(cells[6]) == job["reviews"]
        )


def test_sampleton_pages_are_labelled(seeded: str) -> None:
    from fastapi.testclient import TestClient

    from closeout.app.main import create_app

    c = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    page = c.get("/").text
    assert "made up" in page.lower() or "made-up" in page.lower()
