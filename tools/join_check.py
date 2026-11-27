"""Independent check of the permit-to-parcel join (design 3.18, R-DATA-3).

Pure Python, no project imports. Reads only the seed files. For each permit: linked (exactly one
parcel with the same block and lot), several, or none. Its counts on the snapshot are fixed in the
spec: 2,712 linked, 82 several, 120 none.

  python3 tools/join_check.py [--rows]     # --rows prints one line per permit: pk,status
"""

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "data" / "seed"


def key(block, lot):
    return ((block or "").strip().upper(), (lot or "").strip().upper())


def statuses(parcels_csv=SEED / "parcels_1329.csv", permits_json=SEED / "permits_1329.json"):
    idx = defaultdict(int)
    with open(parcels_csv, newline="") as f:
        for row in csv.DictReader(f):
            idx[key(row["block"], row["lot"])] += 1
    out = {}
    for p in json.loads(Path(permits_json).read_text()):
        n = idx.get(key(p.get("block"), p.get("lot")), 0)
        out[str(p["pk"])] = "linked" if n == 1 else ("several" if n > 1 else "none")
    return out


def main():
    st = statuses()
    if "--rows" in sys.argv:
        for pk in sorted(st, key=int):
            print(f"{pk},{st[pk]}")
        return
    c = Counter(st.values())
    print(f"permits {len(st)}: linked {c['linked']}, several {c['several']}, none {c['none']}")


if __name__ == "__main__":
    main()
