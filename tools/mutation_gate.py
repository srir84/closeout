"""Mutation gate (test strategy 5.4).

Score = caught / (all mutants - mutants excluded by name in tools/mutation_equivalent.txt).
Survived, untested and suspicious mutants count as not caught. Timeouts count as caught but are listed.
An empty or crashed run fails. Default threshold 85%.

  uv run python tools/mutation_gate.py                 # run mutmut on src/closeout/core and gate it
  uv run python tools/mutation_gate.py --stats FILE    # gate an existing stats file
  uv run python tools/mutation_gate.py --prove-weak    # S0 proof: a weak test suite must fail the gate
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXCLUDED = ROOT / "tools" / "mutation_equivalent.txt"
THRESHOLD = 85.0


def excluded_names() -> set[str]:
    if not EXCLUDED.exists():
        return set()
    return {ln.split("#")[0].strip() for ln in EXCLUDED.read_text().splitlines() if ln.split("#")[0].strip()}


def gate(
    stats: dict[str, int], results: dict[str, str], excluded: set[str], threshold: float = THRESHOLD
) -> tuple[bool, float, list[str]]:
    notes: list[str] = []
    total = int(stats.get("total", 0))
    if total == 0:
        return False, 0.0, ["empty mutation run: no mutants were tested"]
    if stats.get("check_was_interrupted_by_user") or stats.get("segfault"):
        return False, 0.0, ["mutation run was interrupted or crashed"]
    counted = {name: st for name, st in results.items() if name not in excluded} if results else None
    if counted is not None:
        caught = sum(1 for st in counted.values() if st in ("killed", "timeout"))
        denom = len(counted)
        for name, st in sorted(counted.items()):
            if st not in ("killed",):
                notes.append(f"{st}: {name}")
    else:
        caught = int(stats.get("killed", 0)) + int(stats.get("timeout", 0))
        denom = total
    if denom == 0:
        return False, 0.0, ["every mutant was excluded"]
    score = 100.0 * caught / denom
    return score >= threshold, score, notes


def read_results(mutants_dir: Path) -> dict[str, str]:
    out = subprocess.run(
        ["uv", "run", "mutmut", "results", "--all", "true"],
        cwd=mutants_dir.parent,
        capture_output=True,
        text=True,
    )
    res = {}
    for line in out.stdout.splitlines():
        if ":" in line:
            name, st = line.strip().rsplit(":", 1)
            res[name.strip()] = st.strip()
    return res


def run_mutmut(cwd: Path) -> tuple[dict[str, int], dict[str, str]]:
    shutil.rmtree(cwd / "mutants", ignore_errors=True)
    subprocess.run(["uv", "run", "mutmut", "run"], cwd=cwd, capture_output=True, text=True)
    subprocess.run(["uv", "run", "mutmut", "export-cicd-stats"], cwd=cwd, capture_output=True, text=True)
    stats_file = cwd / "mutants" / "mutmut-cicd-stats.json"
    stats = json.loads(stats_file.read_text()) if stats_file.exists() else {}
    copied = cwd / "mutants" / "src" / "closeout" / "core"
    if not copied.exists() or "mutmut" not in "".join(p.read_text() for p in copied.glob("*.py")):
        print("FAIL: mutants were not applied to the copied code the tests import")
        return {}, {}
    return stats, read_results(cwd / "mutants")


WEAK_TEST = """
from datetime import UTC, datetime, timedelta
from closeout.core import clock, ids

def test_runs_but_checks_little():
    now = datetime(2026, 3, 2, tzinfo=UTC)
    c = clock.Clock(epoch_real=now, epoch_virtual=now, scale=2)
    c.virtual(now); c.real(now); c.real_wait(timedelta(1), timedelta(0)); c.real_until(now, now)
    ids.event_id("k"); ids.message_id(ids.send_command_id("t", 1, "s"))
    assert True
"""


def prove_weak() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "repo"
        shutil.copytree(ROOT, t, ignore=shutil.ignore_patterns(".git", ".venv", "mutants", "node_modules"))
        for p in (t / "tests" / "unit").glob("test_*.py"):
            p.unlink()
        (t / "tests" / "unit" / "test_weak.py").write_text(WEAK_TEST)
        stats, results = run_mutmut(t)
        ok, score, notes = gate(stats, results, set())
        print(f"weak suite: {stats.get('total', 0)} mutants; first notes: {notes[:2]}")
        print(f"weak suite: score {score:.1f}% -> gate {'passed (BAD)' if ok else 'failed (good)'}")
        return 1 if ok else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--prove-weak", action="store_true")
    a = ap.parse_args()
    if a.prove_weak:
        return prove_weak()
    if a.stats:
        stats, results = json.loads(Path(a.stats).read_text()), {}
    else:
        stats, results = run_mutmut(ROOT)
    ok, score, notes = gate(stats, results, excluded_names(), a.threshold)
    for n in notes:
        print("  " + n)
    print(f"mutation score {score:.1f}% (threshold {a.threshold:.0f}%): {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
