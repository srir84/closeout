"""Requirement trace (test strategy 5.4).

Reads the spec's Slice column and the plan's status table. For every slice marked `done`
(or passed with --assume-done / --upto), every requirement listing that slice must have at
least one test tagged with that (req, slice) pair that ran and passed in this run.

Test results come from build/trace/*.jsonl: one file per pytest session (written by conftest.py).
`make verify` and CI clear the folder first, so only this run's results count.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = ROOT / "docs" / "01-SPEC.md"
PLAN = ROOT / "docs" / "06-IMPLEMENTATION-PLAN.md"
RESULTS = ROOT / "build" / "trace"
ORDER = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S7b", "S8", "S9", "S10", "S11", "S12", "S13", "S14"]


def spec_pairs(text: str) -> list[tuple[str, str]]:
    pairs = []
    for line in text.splitlines():
        m = re.match(r"^\|\s*(R-[A-Z]+-\d+)\s*\|", line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        for s in re.split(r"[,\s]+", cells[-1]):
            if s in ORDER:
                pairs.append((m.group(1), s))
    return pairs


def done_slices(text: str) -> list[str]:
    done = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0] in ORDER and cells[3].lower() == "done":
            done.append(cells[0])
    return done


def passing_pairs(results: Path) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    passed, failed = set(), set()
    files = sorted(results.glob("*.jsonl")) if results.is_dir() else ([results] if results.exists() else [])
    lines = [ln for f in files for ln in f.read_text().splitlines() if ln.strip()]
    for line in lines:
        r = json.loads(line)
        for req in r["reqs"]:
            for s in r["slices"]:
                (passed if r["outcome"] == "passed" else failed).add((req, s))
    return passed, failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assume-done", default="", help="comma-separated slices to treat as done")
    ap.add_argument("--upto", default="", help="treat every slice up to and including this one as done")
    ap.add_argument("--results", default=str(RESULTS))
    a = ap.parse_args()
    pairs = spec_pairs(SPEC.read_text())
    done = set(done_slices(PLAN.read_text()))
    done |= {s for s in a.assume_done.split(",") if s}
    if a.upto:
        done |= set(ORDER[: ORDER.index(a.upto) + 1])
    passed, failed = passing_pairs(Path(a.results))
    required = [p for p in pairs if p[1] in done]
    missing = [p for p in required if p not in passed or p in failed]
    ok = len(required) - len(missing)
    names = sorted(done, key=ORDER.index)
    label = f"{names[0]}–{names[-1]}" if len(names) > 1 else (names[0] if names else "none")
    print(
        f"{label} done: {ok} of {len(required)} required requirement–slice pairs passing "
        f"({len(pairs)} pairs in the spec)"
    )
    for req, s in missing:
        why = "failed" if (req, s) in failed else "no passing test"
        print(f"  MISSING {req} @ {s}: {why}")
    later = sorted({s for _, s in pairs if s not in done}, key=ORDER.index)
    if later:
        print("  not yet: " + ", ".join(later))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
