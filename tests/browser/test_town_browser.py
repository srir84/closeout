"""S11 browser test (needs the stack): the owner asks the town, approves, the clerk uploads, the result shows."""

import pytest
from playwright.sync_api import Page, expect

from tests.stack import flows, stack

pytestmark = [pytest.mark.stack, pytest.mark.slice("S11")]
APP = "http://127.0.0.1:8000"


@pytest.mark.req("R-TOWN-1")
@pytest.mark.req("R-TOWN-2")
def test_ask_the_town_end_to_end(page: Page, browser) -> None:
    stack.make("reset")
    stack.wait_app()
    page.goto(APP + "/")
    page.locator("form.person", has_text="Dana Whitfield").get_by_role("button").click()
    page.goto(APP + "/h/town")
    expect(page.locator("#eligible button.ask")).to_have_count(3)
    page.locator("button.ask[data-permit='22-0147']").click()
    expect(page.locator(".message")).to_have_count(2)
    page.click("#approve-town")
    expect(page.locator("#msg")).to_contain_text("on its way")
    msg = [m for m in flows.wait_emails("clerk@sampleton.test", 1) if "22-0147" in m["Subject"]][0]
    link = __import__("re").search(r"http://127.0.0.1:8000/t/\S+", msg["Text"]).group(0)
    clerk = browser.new_context().new_page()
    clerk.goto(link)
    from tests.stack.flows import DEMO

    clerk.set_input_files("input[name=reply]", str(DEMO.parent.parent / "evals/sets/town_replies/tr01.jpg"))
    clerk.click("#upload-reply")
    expect(clerk.locator("#reply-done")).to_have_text("Thank you")
    page.reload()
    expect(page.locator("#town-result")).to_have_text("Final approval on file", timeout=60_000)
    page.goto(APP + "/h/report")
    expect(page.locator("#report-permits")).to_contain_text("final approval on file")
    expect(page.locator("#report-permits")).to_contain_text("source: Borough of Sampleton")
