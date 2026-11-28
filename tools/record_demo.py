"""Record the demo video (S14): one take through the main story, with a caption on each step.

Needs a freshly reset stack (`make reset`) and Playwright's Chromium. Writes docs/demo/closeout-demo.mp4 and a
poster image. Converting to MP4 uses the ffmpeg that ships with the imageio-ffmpeg package:

    make reset && uv run --with imageio-ffmpeg python tools/record_demo.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
from tests.stack import flows  # noqa: E402

APP = "http://127.0.0.1:8000"
MAIL = "http://127.0.0.1:8025"
DEMO = ROOT / "data" / "demo"
OUT = ROOT / "docs" / "demo"
SIZE: Any = {"width": 1280, "height": 720}

CAPTION_JS = """(t) => {
  let el = document.getElementById('__demo_caption');
  if (!el) {
    el = document.createElement('div'); el.id = '__demo_caption';
    el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;padding:14px 28px;' +
      'background:rgba(15,30,40,.92);color:#fff;font:600 21px/1.35 system-ui,sans-serif;text-align:center';
    document.body.appendChild(el);
  }
  el.textContent = t;
}"""


class Story:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.caption_text = ""

    def caption(self, text: str, hold: float = 3.2) -> None:
        self.caption_text = text
        self.page.evaluate(CAPTION_JS, text)
        self.page.wait_for_timeout(int(hold * 1000))

    def go(self, url: str, text: str | None = None, hold: float = 3.2) -> None:
        self.page.goto(url)
        self.page.evaluate(CAPTION_JS, text or self.caption_text)
        if text:
            self.page.wait_for_timeout(int(hold * 1000))

    def sign_in(self, who: str, text: str) -> None:
        self.go(APP + "/", "Sign-in is 'pick a person'. Every person, home and company here is made up.", 2.5)
        self.page.locator("form.person", has_text=who).get_by_role("button").click()
        self.caption(text)

    def scroll(self, y: int, hold: float = 1.6) -> None:
        self.page.mouse.wheel(0, y)
        self.page.wait_for_timeout(int(hold * 1000))


def run(story: Story) -> None:
    p = story.page
    story.go(
        APP + "/",
        "Closeout turns finished home repairs into verified records a homeowner can show at sale.",
        4,
    )

    # 1. The contractor imports a job.
    lines = (DEMO / "harbor_pine_jobs.csv").read_text().splitlines()
    tmp = Path(tempfile.mkdtemp()) / "jobs.csv"
    tmp.write_text("\n".join([lines[0], lines[1]]) + "\n")
    story.sign_in("Mike Brennan", "Mike, a contractor, uploads his finished jobs: a CSV plus the invoices.")
    p.click("#import-link")
    p.set_input_files("input[name=csv_file]", str(tmp))
    p.set_input_files("input[name=invoices]", str(DEMO / "invoices" / "HP-25-001.png"))
    story.caption(
        "Each invoice is read twice by AI. Anything the two reads disagree on goes to a person.", 2.5
    )
    p.click("#upload")
    expect(p.locator("#batch-jobs")).to_contain_text("ready", timeout=90_000)
    story.caption("Both reads agreed with Mike's file, and the address matched one property.", 2.5)
    p.click("text=HP-25-001")
    story.caption("The job: facts from both reads, the property, and a town permit that matches the work.", 4)
    story.scroll(400)
    story.caption("Every fact shows where it came from.", 2.5)
    p.click("#approve-link")
    story.caption(
        "Nothing is emailed until Mike approves these exact words: a first email and two reminders.", 4
    )
    story.scroll(500)
    p.click("#approve")
    expect(p.locator("#msg")).to_contain_text("Approved")
    story.caption("Approved. Each message carries its own signed approval; the sender checks it again.", 3)
    job_url = p.url.split("?")[0]

    # 2. The homeowner answers from the email.
    msg = flows.wait_emails("dana.whitfield@sampleton.example", 1)[0]
    story.go(
        f"{MAIL}/view/{msg['ID']}", "Emails go only to a local test mailbox. Here is the one Dana got.", 4
    )
    p.set_viewport_size({"width": 430, "height": 720})
    story.go(
        flows.answer_link(msg),
        "Dana opens the link on her phone. Nothing changes until she presses a button.",
        3.5,
    )
    story.scroll(300, 1)
    p.click("#confirm")
    story.caption("Dana confirmed the job.", 2.5)
    p.set_viewport_size(SIZE)

    # 3. The marks.
    story.go(job_url, "Back with Mike: the job now has three marks, each backed by proof.", 3.5)
    p.locator("#marks a[data-mark=permit_on_file]").click()
    story.caption("Each mark links to its proof. This one is the town permit.", 3)
    p.click("text=See the full run")
    story.caption(
        "The run page shows every step, wait and decision, with no email text, addresses or prices.", 4
    )
    story.scroll(500)

    # 4. Ask the town (while the undo window runs out).
    story.sign_in("Dana Whitfield", "Dana's home: verified work, permits and her sale report.")
    story.scroll(400)
    p.click("text=Ask the town")
    story.caption("Old permits with no final approval in the state data: Dana can ask the town clerk.", 3.5)
    p.locator("button.ask[data-permit='22-0147']").click()
    story.caption("Again, nothing is sent until she approves the request and its one reminder.", 3)
    p.click("#approve-town")
    clerk_msg = [m for m in flows.wait_emails("clerk@sampleton.test", 1) if "22-0147" in m["Subject"]][0]
    link = re.search(r"http://127.0.0.1:8000/t/\S+", clerk_msg["Text"]).group(0)  # type: ignore[union-attr]
    town_url = p.url.split("?")[0]
    story.go(link, "The clerk replies by uploading the town's letter through the link.", 3)
    p.set_input_files("input[name=reply]", str(ROOT / "evals/sets/town_replies/tr01.jpg"))
    p.click("#upload-reply")
    story.caption("The letter is read twice. A result is shown only if both reads agree on it.", 3)
    story.go(town_url)
    expect(p.locator("#town-result")).to_have_text("Final approval on file", timeout=90_000)
    story.caption("Final approval on file, with the town as the source.", 3)

    # 5. The sale report and a share link.
    story.go(
        APP + "/h/report", "The sale report: only confirmed work, with its marks and the permit history.", 4
    )
    story.scroll(400)
    story.go(APP + "/h/share", "Dana can share it with a buyer, a buyer's agent or an inspector.", 2.5)
    p.select_option("#role", "buyers_agent")
    p.click("#make-link")
    url = p.locator("#share-url").input_value()
    story.caption("The link expires and can be revoked. Prices stay hidden unless she allows them.", 3)
    story.go(url, "What the buyer's agent sees: the work and its proof, no prices, no contact details.", 4)

    # 6. Ops and the data.
    story.sign_in(
        "Priya Natarajan", "Ops sees everything the system won't guess, with the reason and the proof."
    )
    story.go(
        APP + "/ops/privacy",
        "A privacy request removes a person's details from every store, mailbox and log.",
        3.5,
    )
    story.go(
        APP + "/data", "Real public data: every Manasquan, NJ construction permit, linked to its parcel.", 4
    )
    story.caption("Closeout. Runs on your computer with one command: make demo.", 4)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    vids = Path(tempfile.mkdtemp())
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=SIZE, record_video_dir=str(vids), record_video_size=SIZE)
        page = ctx.new_page()
        t0 = time.monotonic()
        try:
            run(Story(page))
        finally:
            page.screenshot(path=str(vids / "last.png"))
            ctx.close()
            browser.close()
        print(f"recorded {time.monotonic() - t0:.0f} s")
    webm = next(vids.glob("*.webm"))
    import imageio_ffmpeg  # pyright: ignore[reportMissingImports]

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    mp4 = OUT / "closeout-demo.mp4"
    subprocess.run(
        [
            ff,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(webm),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "28",
            "-preset",
            "slow",
            "-movflags",
            "+faststart",
            str(mp4),
        ],
        check=True,
    )
    subprocess.run(
        [
            ff,
            "-y",
            "-loglevel",
            "error",
            "-ss",
            "8",
            "-i",
            str(mp4),
            "-frames:v",
            "1",
            str(OUT / "closeout-demo-poster.png"),
        ],
        check=True,
    )
    shutil.copy(webm, vids / "raw.webm")
    print(f"wrote {mp4} ({mp4.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
