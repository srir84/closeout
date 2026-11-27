"""What gets through after our safety checks?

Two checks from the design:
 1. Contractor list: the contractor's name/license/email come from our own (made-up) contractor list once the
    read matches an entry. Values printed on the document are never used directly.
 2. Second read: a second model reads the same invoice. Fields where the two reads disagree go to the owner
    as a question instead of onto the record.

Counts per model pair: wrong facts that would reach the record, and questions we'd have to ask the owner.
Usage: python3 crosscheck.py <modelA> <modelB> [<modelA> <modelB> ...]
"""
import difflib
import json
import re
import sys
from pathlib import Path

from make_docs import CONTRACTORS
from score import FIELDS, parse, same

HERE = Path(__file__).parent
CONTRACTOR_FIELDS = ["contractor_name", "contractor_license", "contractor_email"]


def load(model):
    d = HERE / "results" / "raw" / model.replace(":", "_").replace("/", "_").replace("+", "__")
    return {p.stem: parse(json.loads(p.read_text()).get("text")) or {} for p in d.glob("*.json")}


def registry_match(read):
    """Match on license (allow one wrong character) or on a close company name. None if unsure."""
    lic = re.sub(r"[^A-Z0-9]", "", str(read.get("contractor_license", "")).upper())
    name = str(read.get("contractor_name", "")).lower()
    hits = []
    for n, l, e in CONTRACTORS:
        lic_close = bool(lic) and (l in lic or sum(a != b for a, b in zip(lic[-len(l):], l)) <= 1)
        name_close = difflib.SequenceMatcher(None, name, n.lower()).ratio() >= 0.85
        if lic_close and name_close:
            return {"contractor_name": n, "contractor_license": l, "contractor_email": e}
        if lic_close or name_close:
            hits.append((n, l, e))
    if len(hits) == 1:
        n, l, e = hits[0]
        return {"contractor_name": n, "contractor_license": l, "contractor_email": e}
    return None


def run(a, b, truth):
    A, B = load(a), load(b)
    slipped, asked, accepted, fields_total = [], 0, 0, 0
    asked_but_right = 0
    for doc in truth:
        ra, rb = A.get(doc["id"]), B.get(doc["id"])
        if ra is None or rb is None:
            continue
        exp = doc["expected"]
        reg = registry_match(ra)
        for f in FIELDS:
            fields_total += 1
            if f in CONTRACTOR_FIELDS:
                if reg is None:
                    asked += 1
                    asked_but_right += same(f, ra.get(f), exp[f])
                    continue
                val = reg[f]
            else:
                if not same(f, ra.get(f), rb.get(f)):  # the two reads disagree -> ask the owner
                    asked += 1
                    asked_but_right += same(f, ra.get(f), exp[f])
                    continue
                val = ra.get(f)
            accepted += 1
            if not same(f, val, exp[f]):
                slipped.append(f"{doc['id']} {f}: recorded {val!r}, truth {exp[f]!r}")
    n_docs = sum(1 for d in truth if d["id"] in A and d["id"] in B)
    return {"pair": f"{a} + {b}", "docs": n_docs, "fields": fields_total, "auto_accepted": accepted,
            "wrong_facts_recorded": len(slipped), "owner_questions": asked,
            "questions_per_invoice": round(asked / n_docs, 2) if n_docs else None,
            "needless_questions": asked_but_right, "slipped": slipped}


def main():
    truth = json.loads((HERE / "truth.json").read_text())
    args = sys.argv[1:]
    rows = [run(args[i], args[i + 1], truth) for i in range(0, len(args) - 1, 2)]
    out = ["# After the safety checks", "",
           "Contractor details come from our contractor list. Other fields are kept only when two reads agree;",
           "otherwise the owner is asked. \"Wrong facts recorded\" is what would reach a home record unchecked.", "",
           "| Reads | Invoices | Wrong facts recorded | Owner questions | Questions per invoice | Questions where the first read was right |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['pair']} | {r['docs']} | {r['wrong_facts_recorded']} of {r['fields']} | {r['owner_questions']} | "
                   f"{r['questions_per_invoice']} | {r['needless_questions']} |")
    out += ["", "## Wrong facts that got through", ""]
    for r in rows:
        out.append(f"**{r['pair']}**: " + ("; ".join(r["slipped"]) or "none"))
        out.append("")
    (HERE / "results" / "crosscheck.md").write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
