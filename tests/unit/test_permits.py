"""S5: permit match rule table (design 3.2, R-COR-1, R-COR-2). Expected values written out independently."""

from datetime import date, timedelta

import pytest

from closeout.core.permits import (
    STATEMENT_LINE,
    Permit,
    PermitMatch,
    counts,
    headline,
    match,
    rule,
    sentence,
    trade_words,
)

pytestmark = [pytest.mark.slice("S5"), pytest.mark.req("R-COR-1")]
FINISH = date(2025, 6, 1)
TRADES = ["building", "plumbing", "electrical", "fire"]
WORK = [
    "addition",
    "deck",
    "kitchen",
    "bathroom",
    "windows",
    "roof",
    "hvac",
    "plumbing",
    "electrical",
    "other",
]
# work type -> {permit type -> trades that count}; "*" = any trades including none.
TABLE = {
    "addition": {"Addition": "*", "New": "*"},
    "deck": {"Addition": {"building"}, "Alteration": {"building"}},
    "kitchen": {"Alteration": {"building", "plumbing", "electrical"}},
    "bathroom": {"Alteration": {"building", "plumbing", "electrical"}},
    "windows": {"Alteration": {"building"}},
    "roof": {"Alteration": {"building"}},
    "hvac": {"Alteration": {"fire", "plumbing", "electrical"}},
    "plumbing": {"Alteration": {"plumbing"}},
    "electrical": {"Alteration": {"electrical"}},
    "other": {},
}


def P(pk="1", t="Alteration", trades=("building",), issued=FINISH - timedelta(days=30), final=None):
    return Permit(pk=pk, permit_type=t, trades=list(trades), issued_on=issued, final_on=final)


@pytest.mark.parametrize("work", WORK)
@pytest.mark.parametrize("ptype", ["Alteration", "Addition", "New", "Demolition"])
@pytest.mark.parametrize("trade", TRADES)
def test_every_work_type_times_trade(work: str, ptype: str, trade: str) -> None:
    allowed = TABLE[work].get(ptype)
    want = allowed == "*" or (allowed is not None and trade in allowed)
    assert counts(P(t=ptype, trades=[trade]), work) is want


@pytest.mark.parametrize("work", WORK)
def test_one_counting_permit_is_on_file(work: str) -> None:
    ptype, allowed = next(iter(TABLE[work].items()), (None, None))
    if ptype is None:
        assert match(work, FINISH, [P()]).result == "no_check"
        return
    trade = "building" if allowed == "*" else sorted(allowed)[0]
    assert match(work, FINISH, [P(t=ptype, trades=[trade])]) == PermitMatch(result="on_file", pks=["1"])


def test_the_window_is_365_days_before_to_14_days_after() -> None:
    for days, ok in ((-365, True), (-366, False), (14, True), (15, False), (0, True)):
        p = P(issued=FINISH + timedelta(days=days))
        assert (match("deck", FINISH, [p]).result == "on_file") is ok, days


def test_results_in_order() -> None:
    assert match("deck", FINISH, [P("a"), P("b")]) == PermitMatch(result="possible", pks=["a", "b"])
    assert match("deck", FINISH, [P("a", trades=[])]) == PermitMatch(result="no_details", pks=["a"])
    assert match("deck", FINISH, [P("a", trades=[]), P("b")]).result == "on_file"
    assert match("deck", FINISH, [P("a", trades=["plumbing"])]) == PermitMatch(result="none", pks=[])
    assert match("deck", FINISH, []).result == "none"
    assert match("deck", None, [P()]).result == "none"
    assert match("deck", FINISH, [P("a", t="Demolition", trades=[])]).result == "none"
    assert match("deck", FINISH, [P("a", issued=None)]).result == "none"


def test_possible_permits_are_in_date_order() -> None:
    m = match(
        "kitchen",
        FINISH,
        [
            P("z", trades=["plumbing"], issued=FINISH - timedelta(days=5)),
            P("a", trades=["electrical"], issued=FINISH - timedelta(days=50)),
        ],
    )
    assert m.pks == ["a", "z"]


@pytest.mark.req("R-COR-2")
@pytest.mark.parametrize(
    ("work", "like", "detached", "line"),
    [
        ("roof", True, True, True),
        ("windows", True, True, True),
        ("roof", True, False, False),
        ("roof", False, True, False),
        ("roof", None, None, False),
        ("deck", True, True, False),
    ],
)
def test_roof_and_window_statement_only_when_both_stated_and_no_permit(work, like, detached, line) -> None:
    assert match(work, FINISH, [], like, detached).statement_line is line
    assert match(work, FINISH, [P()], like, detached).statement_line is False  # a permit counts: no line


@pytest.mark.req("R-COR-2")
def test_structural_roof_work_stated_not_same_size_gets_no_line() -> None:
    m = match("roof", FINISH, [P(trades=["plumbing"])], False, True)
    assert (m.result, m.statement_line) == ("none", False)
    assert "usually don't need a permit" in STATEMENT_LINE and "contractor stated" in STATEMENT_LINE


def test_words_shown() -> None:
    p = P(trades=["plumbing"], issued=date(2025, 3, 12))
    assert sentence(PermitMatch(result="on_file", pks=["1"]), p) == (
        "A town permit with plumbing work was issued for this address on 12 Mar 2025."
    )
    assert trade_words(["fire", "building", "plumbing"]) == "building, plumbing and fire"
    assert trade_words(["electrical", "building"]) == "building and electrical"
    assert trade_words([]) == ""
    assert sentence(
        PermitMatch(result="on_file", pks=["1"]), P(trades=[], issued=date(2025, 3, 12))
    ).startswith("A town permit was issued")
    for r, h in [
        ("on_file", "Town permit on file"),
        ("possible", "Possible permits"),
        ("no_details", "Permit with no work details"),
        ("none", "No matching permit found"),
        ("no_check", "No permit check for this kind of work"),
    ]:
        assert headline(r) == h
    assert "doesn't show what work" in sentence(PermitMatch(result="no_details", pks=[]), None)
    assert sentence(PermitMatch(result="none", pks=[]), None) == "No matching permit found in state data."
    assert "pick" in sentence(PermitMatch(result="possible", pks=[]), None)
    assert rule("garage") is None
