"""S4 browser test (needs `make up`): upload a CSV and 3 invoices -> 2 ready, 1 needs a look (R-IMP-1, R-OPS-1)."""

import csv
import io
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.stack, pytest.mark.slice("S4")]
APP = "http://127.0.0.1:8000"
DEMO = Path(__file__).parents[2] / "data" / "demo"


def sign_in(page: Page, who: str) -> None:
    page.goto(APP + "/")
    page.locator("form.person", has_text=who).get_by_role("button").click()


@pytest.mark.req("R-IMP-1")
@pytest.mark.req("R-OPS-1")
def test_upload_csv_and_three_invoices(page: Page, tmp_path: Path) -> None:
    rows = list(csv.DictReader(io.StringIO((DEMO / "harbor_pine_jobs.csv").read_text())))
    import uuid

    tag = uuid.uuid4().hex[:6]
    pick = [rows[3], rows[4], rows[5]]
    for r in pick:
        r["job_ref"] = f"{r['job_ref']}-{tag}"
    pick[2]["total"] = "1.00"  # the invoice says otherwise: needs a look
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(pick)
    (tmp_path / "jobs.csv").write_text(buf.getvalue())

    sign_in(page, "Mike Brennan")
    page.goto(APP + "/c/import")
    page.set_input_files("input[name=csv_file]", str(tmp_path / "jobs.csv"))
    page.set_input_files("input[name=invoices]", [str(DEMO / "invoices" / r["invoice_file"]) for r in pick])
    page.click("#upload")
    expect(page.locator("#batch-status")).to_have_text("done", timeout=60_000)
    table = page.locator("#batch-jobs")
    expect(table.locator("tr", has_text=pick[0]["job_ref"])).to_contain_text("ready", timeout=90_000)
    expect(table.locator("tr", has_text=pick[1]["job_ref"])).to_contain_text("ready", timeout=90_000)
    expect(table.locator("tr", has_text=pick[2]["job_ref"])).to_contain_text("needs a look", timeout=90_000)

    # The person fixes the asked fact on the review screen and the job becomes ready.
    table.locator("a", has_text=pick[2]["job_ref"]).click()
    page.click("#review-link")
    expect(page.locator("#fact-total_amount")).to_contain_text("Needs checking")
    page.locator("#fact-total_amount input[name=value]").fill(
        pick[2]["total"].replace("1.00", "") or "15350.00"
    )
    page.locator("#fact-total_amount input[name=value]").fill("15350.00")
    page.locator("#fact-total_amount button").click()
    expect(page.locator("#msg")).to_have_text("Done", timeout=20_000)
    page.click("text=Back to the job")
    expect(page.locator("#job-status")).to_have_text(re.compile("ready"))


def test_refused_files_are_explained(page: Page, tmp_path: Path) -> None:
    (tmp_path / "jobs.csv").write_text("job_ref,homeowner_name\nA,B\n")
    (tmp_path / "notes.txt").write_text("not an invoice")
    sign_in(page, "Mike Brennan")
    page.goto(APP + "/c/import")
    page.set_input_files("input[name=csv_file]", str(tmp_path / "jobs.csv"))
    page.set_input_files("input[name=invoices]", [str(tmp_path / "notes.txt")])
    page.click("#upload")
    expect(page.get_by_text("notes.txt: Only PNG, JPG or PDF files can be uploaded.")).to_be_visible(
        timeout=30_000
    )
    expect(page.get_by_text("Some required columns are missing.")).to_be_visible()
