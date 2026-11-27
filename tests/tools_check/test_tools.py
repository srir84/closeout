"""The guard tools behave (S0): trace, mutation gate, PR gate, publish check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools import gate, mutation_gate, publish_check, trace

pytestmark = [pytest.mark.slice("S0")]
ROOT = Path(__file__).parents[2]


def test_trace_reads_the_spec_and_the_status_table() -> None:
    pairs = trace.spec_pairs((ROOT / "docs/01-SPEC.md").read_text())
    assert len({p[0] for p in pairs}) == 47
    assert len(pairs) == 58
    assert ("R-OUT-7", "S0") in pairs and ("R-OUT-7", "S6") in pairs
    table = "| S0 | 4.5 | 3 | done | #1 |\n| S1 | 3 | | not started | |"
    assert trace.done_slices(table) == ["S0"]


def test_trace_requires_a_passing_tagged_test(tmp_path: Path) -> None:
    f = tmp_path / "r.jsonl"
    f.write_text(
        json.dumps({"reqs": ["R-OUT-7"], "slices": ["S0"], "outcome": "passed"})
        + "\n"
        + json.dumps({"reqs": ["R-SAFE-4"], "slices": ["S0"], "outcome": "failed"})
        + "\n"
    )
    passed, failed = trace.passing_pairs(f)
    assert ("R-OUT-7", "S0") in passed and ("R-SAFE-4", "S0") in failed


def test_mutation_gate_fails_an_empty_run_and_a_weak_score() -> None:
    assert mutation_gate.gate({"total": 0}, {}, set())[0] is False
    ok, score, _ = mutation_gate.gate({"total": 10, "killed": 8}, {}, set())
    assert not ok and score == 80.0
    res = {"a": "killed", "b": "survived", "c": "timeout"}
    ok, score, notes = mutation_gate.gate({"total": 3}, res, {"b"})
    assert ok and score == 100.0 and "timeout: c" in notes
    assert mutation_gate.gate({"total": 1, "segfault": 1}, {}, set())[0] is False


def test_pr_gate_flags_protected_paths_and_suspicious_changes() -> None:
    pats = gate.codeowner_patterns((ROOT / ".github/CODEOWNERS").read_text())
    files = [
        {
            "filename": "tests/unit/test_x.py",
            "status": "modified",
            "patch": "@@\n-    assert x == 1\n+    pytest.skip('later')",
        },
        {"filename": "src/closeout/app/main.py", "status": "modified", "patch": "+x = 1  # noqa"},
        {"filename": "docs/01-SPEC.md", "status": "modified", "patch": ""},
        {"filename": "src/closeout/core/permits.py", "status": "removed", "patch": ""},
    ]
    protected, suspicious = gate.review(files, pats)
    assert any("tests/unit/test_x.py" in p for p in protected)
    assert any("01-SPEC" in p for p in protected)
    assert not any("app/main.py" in p for p in protected)
    labels = " ".join(suspicious)
    assert (
        "skip" in labels and "assert deleted" in labels and "noqa" in labels and "moved out of core" in labels
    )


def test_publish_check_finds_planted_names(tmp_path: Path) -> None:
    p = ROOT / "test-results" / "planted.txt"
    p.parent.mkdir(exist_ok=True)
    p.write_text("contact zorblax at zorblax@" + "realmail.com, see /" + "Users/someone/x")
    try:
        hits = publish_check.scan([p], ["zorblax"])
        assert any("private name" in h for h in hits)
        assert any("home-folder" in h for h in hits)
        assert any("realmail.com" in h for h in hits)
    finally:
        p.unlink()
