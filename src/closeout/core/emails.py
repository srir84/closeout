"""Email templates (design 3.6, 3.11). Pure. All outgoing text comes from these fixed templates.

Each body has two link slots, {{ANSWER_LINK}} and {{UNSUB_LINK}}, filled by the sender with fresh links.
The approval page shows exactly this text, and the approval signs its hash.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

ANSWER_SLOT = "{{ANSWER_LINK}}"
UNSUB_SLOT = "{{UNSUB_LINK}}"
JOB_STEPS = ("first", "reminder_1", "reminder_2")


class JobMessageFacts(BaseModel):
    contractor_name: str
    work_type: str
    address: str
    finish_date: date | None
    total: Decimal | None


class Draft(BaseModel):
    step: str
    subject: str
    body_template: str


def work_words(work_type: str) -> str:
    return {"hvac": "heating and cooling", "windows": "window", "other": "home repair"}.get(
        work_type, work_type
    )


def money(v: Decimal | None) -> str:
    return f"${v:,.2f}" if v is not None else "not stated"


def when(d: date | None) -> str:
    return d.strftime("%-d %B %Y") if d else "a date not stated"


def job_message(step: str, f: JobMessageFacts) -> Draft:
    if step not in JOB_STEPS:
        raise ValueError(f"unknown step {step}")
    what = f"{work_words(f.work_type)} work at {f.address}"
    subject = {
        "first": f"Please confirm: {what}",
        "reminder_1": f"Reminder: please confirm {what}",
        "reminder_2": f"Last reminder: please confirm {what}",
    }[step]
    opening = {
        "first": f"{f.contractor_name} has asked us to confirm a job they finished.",
        "reminder_1": f"A few days ago we asked you to confirm a job from {f.contractor_name}. We haven't heard back.",
        "reminder_2": f"This is our last reminder about a job from {f.contractor_name}. After this we won't write again.",
    }[step]
    body = "\n".join(
        [
            "Hello,",
            "",
            opening,
            "",
            f"  Work: {work_words(f.work_type).capitalize()}",
            f"  Address: {f.address}",
            f"  Finished: {when(f.finish_date)}",
            f"  Total on the invoice: {money(f.total)}",
            "",
            "Was this your job? You can confirm it, correct a detail, decline, or tell us it isn't your home.",
            "Opening the link changes nothing until you press a button:",
            ANSWER_SLOT,
            "",
            "You choose whether the price can be shown to anyone else.",
            "",
            "This message was sent by Closeout (a demo with made-up people) on the contractor's behalf.",
            "Don't want these emails? Unsubscribe with one click: " + UNSUB_SLOT,
        ]
    )
    return Draft(step=step, subject=subject, body_template=body)


def fill(body_template: str, answer_url: str, unsub_url: str) -> str:
    """The sender's only change to an approved body: the two link slots."""
    if body_template.count(ANSWER_SLOT) != 1 or body_template.count(UNSUB_SLOT) != 1:
        raise ValueError("template must have exactly one of each link slot")
    return body_template.replace(ANSWER_SLOT, answer_url).replace(UNSUB_SLOT, unsub_url)
