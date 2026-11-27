"""Permit false-match eval (evals 4.3): 0 marks on 200 made-up jobs placed on parcels whose only permits
are for a different trade. Also the true-match rate on Sampleton, where the answers are known.

Reads only the seed files (no database). Seeded, so every run checks the same 200 jobs.
  uv run python -m evals.run_permit_match
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from closeout.core.permitdata import clean_permit
from closeout.core.permits import Permit, match, rule

SEED = Path(__file__).parents[1] / "data" / "seed"
TODAY = date(2026, 11, 27)
WORK = ["deck", "kitchen", "bathroom", "windows", "roof", "hvac", "plumbing", "electrical"]


def permits_by_parcel(permits_file: Path) -> dict[tuple[str, str], list[Permit]]:
    out: dict[tuple[str, str], list[Permit]] = defaultdict(list)
    for row in json.loads(permits_file.read_text()):
        p = clean_permit(row, TODAY)
        out[(p.block, p.lot)].append(
            Permit(
                pk=p.pk,
                permit_type=p.permit_type,
                trades=p.trades,
                issued_on=p.issued_on,
                final_on=p.final_on,
            )
        )
    return out


def false_match_cases(n: int = 200, seed: int = 4300) -> list[tuple[str, date, list[Permit]]]:
    rng = random.Random(seed)
    parcels = permits_by_parcel(SEED / "permits_1329.json")
    cases = []
    keys = sorted(parcels)
    rng.shuffle(keys)
    for k in keys:
        ps = parcels[k]
        if not all(p.trades and p.permit_type == "Alteration" and p.issued_on for p in ps):
            continue
        present = set().union(*(set(p.trades) for p in ps))
        options = [w for w in WORK if not (rule(w)[1] & present)]  # type: ignore[index,operator]
        if not options:
            continue
        anchor = rng.choice(ps)
        assert anchor.issued_on is not None
        cases.append((rng.choice(options), anchor.issued_on + timedelta(days=rng.randint(0, 60)), ps))
        if len(cases) == n:
            break
    return cases


def sampleton_cases() -> list[tuple[str, str, str]]:
    world = json.loads((SEED / "sampleton" / "world.json").read_text())
    permits = {p["pk"]: p for p in json.loads((SEED / "sampleton" / "permits_9999.json").read_text())}
    by_parcel = permits_by_parcel(SEED / "sampleton" / "permits_9999.json")
    out = []
    for job in world["jobs"]:
        p = permits[job["permit_pk"]]
        got = match(
            job["work"], date.fromisoformat(job["finish"]), by_parcel[(p["block"].upper(), p["lot"].upper())]
        )
        out.append((job["code"], "no_details" if job["code"] == "H11" else "on_file", got.result))
    for mc in world["match_cases"]:
        block, lot = mc["pin"].split("_")[1:3]
        got = match(
            mc["work_type"],
            date.fromisoformat(mc["finish"]),
            by_parcel[(block, lot)],
            mc["stated_like_for_like"],
            mc["stated_detached_1_2_family"],
        )
        want = {
            "possible": "possible",
            "on_file": "on_file",
            "no_details": "no_details",
            "no_check": "no_check",
        }.get(mc["expected"], "none")
        out.append((mc["address"], want, got.result))
    return out


def main() -> int:
    cases = false_match_cases()
    marks = [(w, f) for w, f, ps in cases if match(w, f, ps).result in ("on_file", "possible")]
    known = sampleton_cases()
    right = sum(1 for _, want, got in known if want == got)
    print(
        f"Permit false matches: {len(marks)} of {len(cases)} made-up jobs on parcels with only other-trade permits"
    )
    print(f"Sampleton known answers: {right} of {len(known)} right")
    for name, want, got in known:
        if want != got:
            print(f"  {name}: expected {want}, got {got}")
    return 0 if not marks and len(cases) == 200 and right == len(known) else 1


if __name__ == "__main__":
    sys.exit(main())
