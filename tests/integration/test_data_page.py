"""S1: the /data page shows the real numbers and credits its sources (R-DATA-5)."""

import re

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.slice("S1")]


@pytest.fixture
def client(seeded: str) -> TestClient:
    from closeout.app.main import create_app

    return TestClient(create_app(), base_url="http://127.0.0.1:8000")


def num(html: str, el_id: str) -> int:
    m = re.search(rf'id="{el_id}">([\d,]+)<', html)
    assert m, el_id
    return int(m.group(1).replace(",", ""))


@pytest.mark.req("R-DATA-2")
@pytest.mark.req("R-DATA-3")
def test_data_page_numbers(client: TestClient) -> None:
    html = client.get("/data").text
    assert (num(html, "n-parcels"), num(html, "n-parcels-address"), num(html, "n-permits")) == (
        3982,
        3362,
        2914,
    )
    assert (num(html, "n-linked"), num(html, "n-several"), num(html, "n-none")) == (2712, 82, 120)
    assert num(html, "n-no-trades") == 280
    assert "Real public data" in html


@pytest.mark.req("R-DATA-5")
def test_credits_on_the_data_page_and_every_footer(client: TestClient) -> None:
    html = client.get("/data").text
    assert "Department of Community Affairs" in html and "NJOGIS" in html and "Public domain" in html
    for path in ("/", "/data/feed"):
        page = client.get(path).text
        assert "NJ Department of Community Affairs" in page and "NJOGIS" in page


@pytest.mark.req("R-DATA-5")
def test_readme_credits_the_sources() -> None:
    from pathlib import Path

    text = (Path(__file__).parents[2] / "README.md").read_text()
    assert "Department of Community Affairs" in text and "NJOGIS" in text


def test_feed_page_shows_no_people(client: TestClient) -> None:
    html = client.get("/data/feed").text
    assert "Final approval not in state data" in html
    assert "@" not in re.sub(r"<[^>]+>", "", html)
