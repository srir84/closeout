"""S7b: the fast clock (design 3.3, R-RUN-3): /clock reads the one clock row; the banner shows while it's fast."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from closeout import clockdb, db

pytestmark = [pytest.mark.slice("S7b"), pytest.mark.req("R-RUN-3")]


@pytest.fixture
def client(seeded) -> TestClient:
    from closeout.app.main import create_app

    return TestClient(create_app(), base_url="http://127.0.0.1:8000")


def test_clock_endpoint_follows_the_clock_row(client: TestClient) -> None:
    monday = datetime(2026, 3, 2, 9, tzinfo=UTC)
    with db.tx() as c:
        clockdb.reset(c, scale=17280, epoch_virtual=monday)
    got = client.get("/clock").json()
    v = datetime.fromisoformat(got["virtual_now"])
    assert got["scale"] == 17280
    assert monday <= v < monday + timedelta(days=2)  # 1 real second = 4.8 virtual hours


def test_fast_clock_banner_only_when_fast(client: TestClient) -> None:
    with db.tx() as c:
        clockdb.reset(c, scale=17280)
    page = client.get("/").text
    assert "Fast clock: 1 day = 5.0 s" in page
    with db.tx() as c:
        clockdb.reset(c, scale=1)
    assert "Fast clock" not in client.get("/").text


def test_restart_speed_banner(client: TestClient) -> None:
    with db.tx() as c:
        clockdb.reset(c, scale=1440)
    assert "1 day = 60.0 s" in client.get("/").text
