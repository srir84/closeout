"""S7: the email templates never change by accident (snapshots), and only the two links are ever filled in."""

from datetime import date
from decimal import Decimal

import pytest

from closeout.core.emails import ANSWER_SLOT, JOB_STEPS, UNSUB_SLOT, JobMessageFacts, fill, job_message
from closeout.core.wording import check_text

pytestmark = [pytest.mark.slice("S7")]
FACTS = JobMessageFacts(
    contractor_name="Harbor & Pine Builders",
    work_type="kitchen",
    address="14 Osprey Ln",
    finish_date=date(2025, 3, 28),
    total=Decimal("48250.00"),
)


@pytest.mark.req("R-OUT-1")
@pytest.mark.parametrize("step", JOB_STEPS)
def test_each_template_exactly(step: str, snapshot) -> None:
    d = job_message(step, FACTS)
    assert f"Subject: {d.subject}\n\n{d.body_template}" == snapshot


@pytest.mark.req("R-COR-3")
@pytest.mark.parametrize("step", JOB_STEPS)
@pytest.mark.parametrize("work", ["kitchen", "roof", "hvac", "windows", "other", "addition"])
def test_templates_have_no_banned_words_and_both_slots(step: str, work: str) -> None:
    d = job_message(step, FACTS.model_copy(update={"work_type": work}))
    assert check_text(d.subject + d.body_template) == []
    assert d.body_template.count(ANSWER_SLOT) == 1 and d.body_template.count(UNSUB_SLOT) == 1


def test_fill_changes_only_the_two_slots() -> None:
    d = job_message("first", FACTS)
    out = fill(d.body_template, "http://x/a/T", "http://x/u/U")
    assert out.replace("http://x/a/T", ANSWER_SLOT).replace("http://x/u/U", UNSUB_SLOT) == d.body_template
    with pytest.raises(ValueError):
        fill("no slots", "a", "b")
    with pytest.raises(ValueError):
        job_message("reminder_9", FACTS)


def test_missing_values_are_said_plainly() -> None:
    d = job_message("first", FACTS.model_copy(update={"finish_date": None, "total": None}))
    assert "a date not stated" in d.body_template and "not stated" in d.body_template
