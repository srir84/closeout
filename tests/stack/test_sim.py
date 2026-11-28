"""S7b/S8: `make sim` plays every persona row in the spec and checks each end state exactly (E6)."""

import re
import subprocess

import pytest

from tests.stack.stack import ROOT

pytestmark = [pytest.mark.stack]


@pytest.fixture(scope="module")
def sim_output() -> str:
    r = subprocess.run(["make", "sim"], cwd=ROOT, capture_output=True, text=True, timeout=1200)
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


@pytest.mark.slice("S7b")
def test_all_checks_passed(sim_output: str) -> None:
    m = re.search(r"(\d+) of (\d+) checks passed", sim_output)
    assert m and m.group(1) == m.group(2), sim_output[-2000:]
