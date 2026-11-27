"""S0 tool proof: a browser test signs in and clicks through the skeleton (needs `make up`)."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.stack, pytest.mark.slice("S0")]
APP = "http://127.0.0.1:8000"


def test_sign_in_and_click_through_the_skeleton(page: Page) -> None:
    page.goto(APP + "/")
    expect(page.get_by_role("heading", name="Pick a demo person")).to_be_visible()
    page.locator("form.person").first.get_by_role("button").click()
    page.goto(APP + "/skeleton")
    page.fill("input[name=wait_seconds]", "864000")
    page.click("#start")
    expect(page.locator("#ping-status")).to_have_text("RUNNING")
    page.click("#press")
    expect(page.locator("#msg")).to_have_text("Done")
    expect(page.get_by_text("Made-up people")).to_be_visible()
