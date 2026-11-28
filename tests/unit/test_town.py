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
