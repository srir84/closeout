"""S8: the first full end-to-end browser test (needs the stack): contractor -> homeowner -> marks -> undo.

Run with --video=on to record the slice demo.
"""

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.stack import flows, stack

pytestmark = [pytest.mark.stack, pytest.mark.slice("S8")]
APP = "http://127.0.0.1:8000"
DEMO = Path(__file__).parents[2] / "data" / "demo"


@pytest.fixture(scope="module", autouse=True)
def fresh():
    stack.make("reset")
    stack.wait_app()


def sign_in(page: Page, who: str) -> None:
    page.goto(APP + "/")
    page.locator("form.person", has_text=who).get_by_role("button").click()


@pytest.mark.req("R-REC-2")
@pytest.mark.req("R-REC-3")
@pytest.mark.req("R-OBS-2")
def test_contractor_to_homeowner_to_marks(page: Page, tmp_path: Path) -> None:
    lines = (DEMO / "harbor_pine_jobs.csv").read_text().splitlines()
    (tmp_path / "jobs.csv").write_text("\n".join([lines[0], lines[1]]) + "\n")
    sign_in(page, "Mike Brennan")
    page.click("#import-link")
    page.set_input_files("input[name=csv_file]", str(tmp_path / "jobs.csv"))
    page.set_input_files("input[name=invoices]", str(DEMO / "invoices" / "HP-25-001.png"))
    page.click("#upload")
    expect(page.locator("#batch-jobs")).to_contain_text("ready", timeout=60_000)
    page.click("text=HP-25-001")
    expect(page.locator("#permit-result")).to_have_text("Town permit on file")
    page.click("#approve-link")
    expect(page.locator(".message")).to_have_count(3)
    page.click("#approve")
    expect(page.locator("#msg")).to_contain_text("Approved")
    job_url = page.url.split("?")[0]

    msg = flows.wait_emails("dana.whitfield@sampleton.example", 1)[0]
    phone = page.context.new_page()
    phone.set_viewport_size({"width": 390, "height": 844})
    phone.goto(flows.answer_link(msg))
    phone.click("#confirm")
    expect(phone.locator("#thanks")).to_be_visible()

    page.goto(job_url)
    expect(page.locator("#job-status")).to_contain_text("confirmed")
    expect(page.locator("#marks .mark")).to_have_count(3)
    page.locator("#marks a[data-mark=permit_on_file]").click()
    expect(page.locator("#proof-title")).to_have_text("Town permit on file")
    page.click("text=See the full run")
    expect(page.locator("#steps")).to_contain_text("Homeowner confirmed")
    assert "dana.whitfield" not in page.content()

    # Undo within the window: the marks go and the job is back to ready for a new round.
    page.goto(job_url)
    page.click("#undo")
    expect(page.locator("#msg")).to_contain_text("Undone")
    expect(page.locator("#job-status")).to_contain_text("ready")
    expect(page.locator("#marks .mark")).to_have_count(0)
    # The old round's link now says it was replaced.
    phone.goto(flows.answer_link(msg))
    expect(phone.locator("#link-state")).to_contain_text("replaced")
    assert re.search(r"round", page.content())
