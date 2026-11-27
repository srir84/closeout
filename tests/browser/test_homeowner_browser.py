"""S7 browser test (needs `make reset` stack): the homeowner's phone flow for all four answers (R-OUT-3)."""

import pytest
from playwright.sync_api import Page, expect

from closeout.core.wording import check_text
from tests.stack import flows, stack

pytestmark = [pytest.mark.stack, pytest.mark.slice("S7")]
PERSONAS = {
    "HP-25-001": "dana.whitfield",
    "HP-25-002": "marcus.ortega",
    "HP-25-007": "victor.hale",
    "HP-25-008": "nina.petrova",
}


@pytest.fixture(scope="module")
def jobs():
    stack.make("reset")
    stack.wait_app()
    return flows.import_demo(rows=[1, 2, 7, 8])


@pytest.mark.req("R-OUT-3")
@pytest.mark.req("R-COR-3")
@pytest.mark.parametrize(
    ("ref", "button", "state"),
    [
        ("HP-25-001", "#confirm", "confirmed"),
        ("HP-25-007", "#decline", "stopped"),
        ("HP-25-008", "#not-mine", "stopped"),
        ("HP-25-002", "#correct", "answered"),
    ],
)
def test_the_homeowner_answers_on_a_phone(page: Page, jobs, ref: str, button: str, state: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    assert flows.approve(jobs[ref]).startswith("Approved")
    msg = flows.wait_emails(f"{PERSONAS[ref]}@sampleton.example", 1)[0]
    assert check_text(msg["Text"] + msg["Subject"]) == []
    page.goto(flows.answer_link(msg))
    expect(page.get_by_role("heading", name="Was this your job?")).to_be_visible()
    assert check_text(page.content()) == []
    if button == "#correct":
        page.click("#correct-box summary")
        page.fill("input[name=finish_date]", "2025-05-19")
    page.click(button)
    expect(page.locator("#thanks")).to_be_visible()
    assert check_text(page.content()) == []
    assert flows.job_status(jobs[ref]).replace(" ", "_") == state


@pytest.mark.req("R-OUT-5")
def test_one_click_unsubscribe_in_the_browser(page: Page, jobs) -> None:
    msg = flows.wait_emails("dana.whitfield@sampleton.example", 1)[0]
    page.goto(flows.unsub_link(msg))
    page.click("#unsubscribe")
    expect(page.locator("#unsubscribed")).to_be_visible()
    expect(page.get_by_text("may still arrive")).to_be_visible()
