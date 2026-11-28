"""Town reply rule (R-TOWN-2): shown only when both reads agree on result and date for our permit."""

from datetime import date

import pytest

from closeout.core.town import decide, permit_key

pytestmark = [pytest.mark.slice("S11"), pytest.mark.req("R-TOWN-2")]
TODAY = date(2026, 11, 27)
F = {"permit_number": "22-0147", "result": "final_on_file", "result_date": "2022-12-16"}
N = {"permit_number": "22-0147", "result": "no_record", "result_date": ""}


@pytest.mark.parametrize(
    ("a", "b", "result", "reason"),
    [
        (F, F, "final_on_file", "agree"),
        (N, N, "no_record", "agree"),
        (F, dict(F, result_date="2022-12-17"), "unclear", "disagree"),
        (F, N, "unclear", "disagree"),
        (F, dict(F, permit_number="22-0174"), "unclear", "wrong_permit"),
        (F, dict(F, result_date=""), "unclear", "no_date"),
        (F, dict(F, result="unclear"), "unclear", "read_unclear"),
        (F, None, "unclear", "invalid"),
        (F, dict(F, result="maybe"), "unclear", "invalid"),
        (dict(F, permit_number="Permit #22 0147"), F, "final_on_file", "agree"),
    ],
)
def test_rules(a, b, result: str, reason: str) -> None:
    got = decide(a, b, "22-0147", TODAY)
    assert (got.result, got.reason) == (result, reason)
    if result == "final_on_file":
        assert got.result_date == date(2022, 12, 16)
    else:
        assert got.result_date is None


def test_permit_key() -> None:
    assert permit_key("22-0147") == permit_key(" 22 0147 ") == "220147"
    assert permit_key(None) == ""


def test_a_request_for_more_information_is_always_unclear() -> None:
    more = dict(N, asks_for_more_information=True)
    assert decide(more, N, "22-0147", TODAY).reason == "more_info"
    assert decide(N, more, "22-0147", TODAY).result == "unclear"
    assert decide(dict(F, asks_for_more_information=False), F, "22-0147", TODAY).result == "final_on_file"


def test_an_answer_about_a_different_permit_is_unclear() -> None:
    other = dict(N, result_permit_number="22-0174")
    assert decide(other, N, "22-0147", TODAY).reason == "wrong_permit"
    same = dict(F, result_permit_number="22-0147")
    assert decide(same, same, "22-0147", TODAY).result == "final_on_file"


@pytest.mark.parametrize(
    ("field", "same"),
    [
        ("22-0147", True),
        ("Permit #22 0147", True),
        ("22-0147 (furnace and AC replacement)", True),
        ("22-0147, 153 Tidewater Road", True),
        ("OPRA Request W-2026-0859 - Construction Permit No. 22-0147, 81 Bayberry Circle", True),
        ("220147", True),
        ("22-0174", False),
        ("22-0147 and 22-0148", False),
        ("", False),
    ],
)
def test_same_permit_ignores_words_around_the_number(field: str, same: bool) -> None:
    from closeout.core.town import same_permit

    assert same_permit(field, "22-0147") is same


def test_work_still_outstanding_is_unclear() -> None:
    partial = dict(F, work_still_outstanding=True)
    assert decide(partial, partial, "22-0147", TODAY).result == "unclear"


def test_open_work_with_no_record_is_still_shown() -> None:
    nr = dict(N, work_still_outstanding=True)
    assert decide(nr, nr, "22-0147", TODAY).result == "no_record"
