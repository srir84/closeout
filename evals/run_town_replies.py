"""Town reply eval (evals 4.2): wrong results shown must be 0. Both reads must agree on result and date.

uv run python -m evals.run_town_replies [--first10] [--live]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from closeout.core.town import decide
from closeout.gateway.core import Gateway, ReplayMiss
from closeout.settings import Settings
from evals.scoring import TODAY
from evals.sets import cases

REPORTS = Path(__file__).parent / "reports"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--first10", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    gw = Gateway(Settings(closeout_mode="full" if a.live else "replay", ollama_url="http://127.0.0.1:11434"))
    cs = cases("town_replies")
    if a.first10:
        cs = [c for c in cs if c.meta.get("first10")]
    rows, missing = [], []
    for c in cs:
        try:
            ra = gw.read(c.path.read_bytes(), c.mime, "A", "town_reply")
            rb = gw.read(c.path.read_bytes(), c.mime, "B", "town_reply")
        except ReplayMiss:
            missing.append(c.id)
            continue
        exp = c.expected
        got = decide(ra.output, rb.output, exp["permit_number"], TODAY)
        truth_date = date.fromisoformat(exp["result_date"]) if exp.get("result_date") else None
        shown = got.result != "unclear"
        wrong = shown and (got.result != exp["result"] or got.result_date != truth_date)
        rows.append(
            {"id": c.id, "truth": exp["result"], "shown": got.result, "reason": got.reason, "wrong": wrong}
        )
    n = len(rows)
    wrong = [r for r in rows if r["wrong"]]
    shown = sum(r["shown"] != "unclear" for r in rows)
    lines = [
        f"# Town reply eval ({gw.s.closeout_mode} mode)",
        "",
        f"Run {date.today().isoformat()}. {n} replies.",
        "",
        f"**Wrong results shown: {len(wrong)} of {n}.** Shown: {shown}. Sent to ops as unclear: {n - shown}. "
        f"Missing: {len(missing)}.",
        "",
        "| Reply | Truth | Shown | Why |",
        "|---|---|---|---|",
    ]
    lines += [f"| {r['id']} | {r['truth']} | {r['shown']} | {r['reason']} |" for r in rows]
    report = "\n".join(lines) + "\n"
    print(report)
    if not a.no_write:
        REPORTS.mkdir(exist_ok=True)
        stem = f"{date.today().isoformat()}-town-replies{'-first10' if a.first10 else ''}{'-live' if a.live else ''}"
        (REPORTS / f"{stem}.md").write_text(report)
        (REPORTS / f"{stem}.json").write_text(
            json.dumps({"n": n, "wrong": len(wrong), "shown": shown, "missing": missing}, indent=1) + "\n"
        )
    return 0 if not wrong and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
