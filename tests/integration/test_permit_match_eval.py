"""S5: the false-match eval (evals 4.3) and Sampleton's known answers run in CI."""

import pytest
from evals import run_permit_match

pytestmark = [pytest.mark.slice("S5"), pytest.mark.req("R-COR-1")]


def test_no_false_matches_on_200_other_trade_jobs() -> None:
    cases = run_permit_match.false_match_cases()
    assert len(cases) == 200
    from closeout.core.permits import match

    assert [w for w, f, ps in cases if match(w, f, ps).result in ("on_file", "possible")] == []


def test_sampleton_known_answers() -> None:
    known = run_permit_match.sampleton_cases()
    assert len(known) == 23
    assert [(n, w, g) for n, w, g in known if w != g] == []
