"""S10 browser test (needs the stack): the owner shares the report, a buyer opens it, the owner revokes it."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.stack, pytest.mark.slice("S10")]
APP = "http://127.0.0.1:8000"


@pytest.mark.req("R-REP-2")
@pytest.mark.req("R-REP-3")
@pytest.mark.req("R-SAFE-3")
def test_share_open_and_revoke(page: Page, browser) -> None:
    page.goto(APP + "/")
    page.locator("form.person", has_text="Dana Whitfield").get_by_role("button").click()
    expect(page.get_by_role("heading", name="14 OSPREY LN")).to_be_visible()
    page.click("#share-link")
    page.select_option("#role", "buyer")
    page.click("#make-link")
    url = page.locator("#share-url").input_value()
    other = browser.new_context().new_page()
    other.goto(url)
    expect(other.locator("#viewer")).to_have_text("Buyer")
    expect(other.get_by_text("Permit history")).to_be_visible()
    assert "dana" not in other.content().lower()
    page.goto(APP + "/h/home")
    page.locator("#shares button", has_text="Revoke").first.click()
    expect(page.locator("#msg")).to_have_text("Link revoked.")
    other.goto(url)
    expect(other.locator("#link-state")).to_contain_text("isn't valid")
