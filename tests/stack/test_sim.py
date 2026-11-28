"""S7b-S14: `make demo-run` plays every persona row in the spec and checks each end state exactly (E6, R-RUN-1)."""

import json
import re
import subprocess

import pytest

from tests.stack.stack import ROOT

pytestmark = [pytest.mark.stack]


@pytest.fixture(scope="module")
def sim_output() -> str:
    r = subprocess.run(["make", "demo-run"], cwd=ROOT, capture_output=True, text=True, timeout=1200)
    return r.stdout + r.stderr


def line(out: str, start: str) -> str:
    m = re.search(rf"^\s+(PASS|FAIL)\s+{re.escape(start)}.*$", out, re.M)
    assert m, f"no result line for {start}"
    return m.group(0)


@pytest.mark.slice("S7b")
@pytest.mark.req("R-RUN-3")
@pytest.mark.parametrize("code", [f"H{i} " for i in range(1, 12)])
def test_persona_rows_h1_to_h11(sim_output: str, code: str) -> None:
    assert line(sim_output, code).strip().startswith("PASS"), line(sim_output, code)


@pytest.mark.slice("S8")
@pytest.mark.req("R-REC-3")
def test_persona_h12_undo(sim_output: str) -> None:
    assert line(sim_output, "H12 ").strip().startswith("PASS")


@pytest.mark.slice("S7b")
@pytest.mark.req("R-OPS-3")
@pytest.mark.parametrize(
    "check",
    [
        "every delivered email",
        "no email went to a suppressed address",
        "every job's status equals",
        "no page or email contains a banned phrase",
    ],
)
def test_cross_checks(sim_output: str, check: str) -> None:
    assert line(sim_output, check).strip().startswith("PASS")


@pytest.mark.slice("S9")
@pytest.mark.req("R-IMP-6")
def test_persona_k1_contractor_asked_by_a_homeowner(sim_output: str) -> None:
    assert line(sim_output, "K1 ").strip().startswith("PASS"), line(sim_output, "K1 ")


@pytest.mark.slice("S11")
@pytest.mark.req("R-TOWN-2")
@pytest.mark.parametrize("code", ["T1 ", "T2 ", "T3 "])
def test_persona_rows_t1_to_t3(sim_output: str, code: str) -> None:
    assert line(sim_output, code).strip().startswith("PASS"), line(sim_output, code)


@pytest.mark.slice("S14")
@pytest.mark.req("R-RUN-4")
def test_the_end_state_summary_is_the_same_every_time(sim_output: str) -> None:
    """Reset then demo gives exactly this summary, on any machine (no times or IDs in it)."""
    m = re.search(r"end-state summary: (\[.*\])", sim_output)
    assert m, sim_output[-2000:]
    want = json.loads((ROOT / "data" / "demo" / "expected_summary.json").read_text())
    assert json.loads(m.group(1)) == want


@pytest.mark.slice("S7b")
@pytest.mark.slice("S14")
@pytest.mark.req("R-RUN-1")
def test_all_checks_passed(sim_output: str) -> None:
    m = re.search(r"(\d+) of (\d+) checks passed", sim_output)
    assert m and m.group(1) == m.group(2), sim_output[-2000:]
