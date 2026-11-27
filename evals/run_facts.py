"""Reading evals (evals 4.1 E1/E2, 4.2): read each document twice, compare, and score kept facts.

  uv run python -m evals.run_facts regression development     # replay mode (CI)
  uv run python -m evals.run_facts --live sealed              # live models on the owner's Mac
Writes evals/reports/<date>-facts-<set>.md and .json. Exit 1 if a target is missed or a case is missing.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from closeout.core.clean import invoice_fields
from closeout.core.compare import decide_reads
from closeout.gateway.core import Gateway, ReplayMiss
from closeout.settings import Settings
from evals.scoring import TODAY, read_right, score_case
from evals.sets import cases

REPORTS = Path(__file__).parent / "reports"


def upper_bound(failures: int, n: int) -> float:
    """95% upper bound on a rate. With 0 failures this is about 3/n (the rule of three)."""
    if n == 0:
        return 1.0
    if failures == 0:
        return 1 - 0.05 ** (1 / n)
    p = failures / n
    return min(1.0, p + 1.96 * math.sqrt(p * (1 - p) / n) + 1 / (2 * n))


def run_set(name: str, gw: Gateway, light: bool = False) -> dict[str, Any]:
    rows, missing = [], []
    acc_a: Counter[str] = Counter()
    acc_b: Counter[str] = Counter()
    for c in cases(name):
        data = c.path.read_bytes()
        try:
            ra = gw.read(data, c.mime, "A")
            rb = None if light else gw.read(data, c.mime, "B")
        except ReplayMiss as e:
            missing.append({"id": c.id, "error": str(e)[:160]})
            continue
        for f in invoice_fields():
            acc_a[f] += read_right(f, ra.output, c.expected[f])
            if rb is not None:
                acc_b[f] += read_right(f, rb.output, c.expected[f])
        decisions = decide_reads(ra.output, rb.output if rb else None, TODAY, light=light)
        s = score_case(decisions, c.expected)
        rows.append(
            {
                "id": c.id,
                **s,
                "valid_a": ra.valid,
                "valid_b": rb.valid if rb else None,
                "ms_a": ra.ms,
                "ms_b": rb.ms if rb else None,
            }
        )
    n = len(rows)
    fields = len(invoice_fields())
    docs_wrong = sum(1 for r in rows if r["wrong_kept"])
    facts_wrong = sum(len(r["wrong_kept"]) for r in rows)
    kept = sum(r["kept"] for r in rows)
    asked = sum(len(r["asked"]) for r in rows)
    a_right = sum(acc_a.values())
    return {
        "set": name,
        "mode": gw.s.closeout_mode,
        "light": light,
        "documents": n,
        "missing": missing,
        "e1_documents_with_wrong_kept_fact": docs_wrong,
        "e1_wrong_kept_facts": facts_wrong,
        "e1_upper_bound_95": upper_bound(docs_wrong, n),
        "kept_facts": kept,
        "asked_facts": asked,
        "e2_asked_per_job": asked / n if n else 0.0,
        "share_kept": kept / (n * fields) if n else 0.0,
        "reader_a_field_accuracy": a_right / (n * fields) if n else 0.0,
        "reader_a_by_field": {f: acc_a[f] / n for f in invoice_fields()} if n else {},
        "reader_b_field_accuracy": sum(acc_b.values()) / (n * fields) if n and not light else None,
        "worst": sorted(rows, key=lambda r: (-len(r["wrong_kept"]), -len(r["asked"])))[:5],
        "rows": rows,
    }


def report_md(r: dict[str, Any]) -> str:
    n = r["documents"]
    lines = [
        f"# Reading eval: {r['set']} set ({r['mode']} mode{', light' if r['light'] else ''})",
        "",
        f"Run {date.today().isoformat()}. Counted per document (n = {n}) and per fact ({n * 9} facts).",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| E1 documents with a wrong kept fact | {r['e1_documents_with_wrong_kept_fact']} of {n} "
        f"(95% upper bound {r['e1_upper_bound_95']:.1%}) |",
        f"| E1 wrong kept facts | {r['e1_wrong_kept_facts']} of {r['kept_facts']} kept |",
        f"| E2 asked facts per job | {r['e2_asked_per_job']:.2f} |",
        f"| Share of facts kept without asking | {r['share_kept']:.1%} |",
        f"| Reader A facts right | {r['reader_a_field_accuracy']:.1%} |",
    ]
    if r["reader_b_field_accuracy"] is not None:
        lines.append(f"| Reader B facts right | {r['reader_b_field_accuracy']:.1%} |")
    lines.append(f"| Missing cases | {len(r['missing'])} |")
    lines += ["", "Reader A by field:", "", "| Field | Right |", "|---|---|"]
    lines += [f"| {f} | {v:.0%} |" for f, v in r["reader_a_by_field"].items()]
    lines += ["", "Worst 5:", ""]
    lines += [
        f"- {w['id']}: wrong kept {w['wrong_kept'] or 'none'}, asked {w['asked'] or 'none'}"
        for w in r["worst"]
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sets", nargs="+")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--light", action="store_true")
    ap.add_argument("--min-reader-a", type=float, default=0.97)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    s = Settings(
        closeout_mode=("light" if a.light else "full") if a.live else "replay",
        ollama_url="http://127.0.0.1:11434",
    )
    gw = Gateway(s)
    ok = True
    for name in a.sets:
        r = run_set(name, gw, light=a.light)
        print(report_md(r))
        if not a.no_write and name != "sealed":
            REPORTS.mkdir(exist_ok=True)
            stem = f"{date.today().isoformat()}-facts-{name}{'-live' if a.live else ''}"
            (REPORTS / f"{stem}.md").write_text(report_md(r))
            (REPORTS / f"{stem}.json").write_text(
                json.dumps({k: v for k, v in r.items() if k != "rows"}, indent=1, default=str) + "\n"
            )
        if name == "sealed":
            # Counts only: no document from the sealed set is shown or saved in the repo.
            print(
                f"sealed: {r['documents']} documents, {r['e1_documents_with_wrong_kept_fact']} with a wrong kept fact"
            )
        if r["missing"] or r["e1_documents_with_wrong_kept_fact"] and not a.light:
            ok = False
        if name == "regression" and r["reader_a_field_accuracy"] < a.min_reader_a:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
