"""Make Sampleton, the made-up town (code 9999) where every made-up person lives (R-DATA-6).

Seeded (9999), so every run writes the same files:
  data/seed/sampleton/parcels_9999.csv   parcels, same columns as Manasquan's
  data/seed/sampleton/permits_9999.json  permits, same shape as the state feed's rows
  data/seed/sampleton/world.json         people, contractors, accounts, homes, grants, the town,
                                         and the demo jobs with each persona's expected end state

No street name here exists in Manasquan (checked when it runs). All emails are .example or .test.

  uv run python data/make_sampleton.py
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "seed" / "sampleton"
TOWN = "9999"
SEED = 9999
STREETS = [
    "OSPREY LN",
    "HERON WAY",
    "TIDEWATER RD",
    "SANDBAR CT",
    "BEACON AVE",
    "LANTERN PL",
    "DRIFTWOOD TER",
    "GULL ROCK RD",
    "SALTMEADOW DR",
    "BAYBERRY CIR",
]
PARCEL_COLUMNS = [
    "pin",
    "town",
    "block",
    "lot",
    "qualifier",
    "address",
    "prop_class",
    "year_built",
    "lat",
    "lon",
]

CONTRACTORS = [
    # key, name, license, trades, contact person, email
    (
        "harbor",
        "Harbor & Pine Builders",
        "13VH01234567",
        ["building", "plumbing", "electrical"],
        "Mike Brennan",
        "office@harborpine.example",
    ),
    ("inlet", "Inlet Roofing Co.", "13VH00077302", ["building"], "Pat Kim", "estimates@inletroof.example"),
    (
        "bayside",
        "Bayside Plumbing & Heating",
        "13VH00551920",
        ["plumbing", "fire"],
        "Luis Romero",
        "service@baysideph.example",
    ),
    (
        "tidewater",
        "Tidewater Electric LLC",
        "13VH00938114",
        ["electrical"],
        "Ann Okoye",
        "jobs@tidewater-elec.example",
    ),
    (
        "dunegrass",
        "Dune Grass Decks",
        "13VH00410267",
        ["building"],
        "Eli Novak",
        "build@dunegrassdecks.example",
    ),
]

# Persona jobs for Harbor & Pine (spec: "Simulated people and their expected end states").
# permit: (permit type, trades, issued, final) on the job's parcel; "none_trades" = a permit with no fee data.
PERSONAS = [
    dict(
        code="H1",
        name="Dana Whitfield",
        email="dana.whitfield@sampleton.example",
        address="14 OSPREY LN",
        work="kitchen",
        finish="2025-03-28",
        total="48250.00",
        warranty=5,
        permit=("Alteration", ["building", "plumbing", "electrical"], "2025-01-15", "2025-04-10"),
        ends="recorded",
        marks=3,
        emails=1,
        reviews=0,
    ),
    dict(
        code="H2",
        name="Marcus Ortega",
        email="marcus.ortega@sampleton.example",
        address="22 OSPREY LN",
        work="bathroom",
        finish="2025-05-16",
        total="21400.00",
        warranty=2,
        permit=("Alteration", ["plumbing"], "2025-04-02", None),
        ends="recorded",
        marks=3,
        emails=1,
        reviews=0,
        correct={"finish_date": "2025-05-19"},
        contractor_bot="accept",
    ),
    dict(
        code="H3",
        name="Grace Liu",
        email="grace.liu@sampleton.example",
        address="7 HERON WAY",
        work="deck",
        finish="2025-06-20",
        total="18900.00",
        warranty=3,
        permit=("Alteration", ["building"], "2025-05-01", "2025-07-02"),
        ends="needs_a_look",
        marks=0,
        emails=1,
        reviews=1,
        correct={"total": "17900.00"},
        contractor_bot="reject",
    ),
    dict(
        code="H4",
        name="Rosa Delgado",
        email="rosa.delgado@sampleton.example",
        address="31 HERON WAY",
        work="windows",
        finish="2025-04-11",
        total="12600.00",
        warranty=10,
        permit=("Alteration", ["building"], "2025-03-03", None),
        ends="recorded",
        marks=3,
        emails=2,
        reviews=0,
        answer_after="reminder_1",
    ),
    dict(
        code="H5",
        name="Samuel Okafor",
        email="samuel.okafor@sampleton.example",
        address="126 TIDEWATER RD",
        work="roof",
        finish="2025-07-25",
        total="23800.00",
        warranty=10,
        permit=("Alteration", ["building"], "2025-06-30", "2025-08-01"),
        ends="recorded",
        marks=3,
        emails=3,
        reviews=0,
        answer_after="reminder_2",
    ),
    dict(
        code="H6",
        name="Helen Park",
        email="helen.park@sampleton.example",
        address="128 TIDEWATER RD",
        work="hvac",
        finish="2025-02-14",
        total="15350.00",
        warranty=5,
        permit=("Alteration", ["fire", "electrical"], "2025-01-20", "2025-02-28"),
        ends="recorded",
        marks=3,
        emails=3,
        reviews=0,
        answer_after="day_20",
    ),
    dict(
        code="H7",
        name="Victor Hale",
        email="victor.hale@sampleton.example",
        address="5 SANDBAR CT",
        work="addition",
        finish="2025-09-12",
        total="96500.00",
        warranty=5,
        permit=("Addition", ["building", "electrical"], "2025-03-10", None),
        ends="stopped",
        marks=0,
        emails=1,
        reviews=0,
        answer="decline",
    ),
    dict(
        code="H8",
        name="Nina Petrova",
        email="nina.petrova@sampleton.example",
        address="9 SANDBAR CT",
        work="plumbing",
        finish="2025-08-08",
        total="4200.00",
        warranty=1,
        permit=("Alteration", ["plumbing"], "2025-07-28", None),
        ends="stopped",
        marks=0,
        emails=1,
        reviews=1,
        answer="not_mine",
    ),
    dict(
        code="H9",
        name="Owen Fitzgerald",
        email="owen.fitzgerald@sampleton.example",
        address="40 BEACON AVE",
        work="electrical",
        finish="2025-10-03",
        total="6800.00",
        warranty=2,
        permit=("Alteration", ["electrical"], "2025-09-15", None),
        ends="no_answer",
        marks=0,
        emails=1,
        reviews=0,
        answer="unsubscribe",
    ),
    dict(
        code="H10",
        name="Leah Cohen",
        email="leah.cohen@sampleton.example",
        address="44 BEACON AVE",
        work="kitchen",
        finish="2025-11-07",
        total="52900.00",
        warranty=5,
        permit=("Alteration", ["building", "plumbing"], "2025-08-20", None),
        ends="no_answer",
        marks=0,
        emails=3,
        reviews=0,
        answer="never",
    ),
    dict(
        code="H11",
        name="Ivan Morales",
        email="ivan.morales@sampleton.example",
        address="3 LANTERN PL",
        work="bathroom",
        finish="2025-06-06",
        total="19750.00",
        warranty=2,
        permit=("Alteration", [], "2025-04-22", None),
        ends="recorded",
        marks=2,
        emails=1,
        reviews=0,
    ),
    dict(
        code="H12",
        name="Ruth Adams",
        email="ruth.adams@sampleton.example",
        address="12 LANTERN PL",
        work="deck",
        finish="2025-05-30",
        total="14300.00",
        warranty=3,
        permit=("Alteration", ["building"], "2025-04-28", "2025-06-12"),
        ends="recorded",
        marks=3,
        emails=2,
        reviews=0,
        undo=True,
    ),
]

# Old permits on Dana's home with no final approval in state data: the town-request personas.
TOWN_PERSONAS = [
    dict(
        code="T1",
        permit_no="22-0147",
        type="Alteration",
        trades=["building"],
        issued="2022-05-09",
        clerk="final_on_file",
        reply_bday=4,
        ends="replied",
        result="final_on_file",
        emails=1,
    ),
    dict(
        code="T2",
        permit_no="23-0388",
        type="Alteration",
        trades=["plumbing"],
        issued="2023-08-14",
        clerk="no_record",
        reply_bday="after_reminder",
        ends="replied",
        result="no_record",
        emails=2,
    ),
    dict(
        code="T3",
        permit_no="21-0912",
        type="Alteration",
        trades=["electrical"],
        issued="2021-10-04",
        clerk="never",
        ends="no_reply",
        result=None,
        emails=2,
    ),
]

# A homeowner-started job (S9): Dana uploads Bayside's invoice and asks them to confirm.
K1 = dict(
    code="K1",
    contractor="bayside",
    work="plumbing",
    finish="2024-02-20",
    total="3150.00",
    warranty=1,
    permit=("Alteration", ["plumbing"], "2024-02-01", "2024-03-05"),
    ends="recorded",
    marks=3,
    emails=1,
)

# Extra parcels with known permit-match answers (S5) and address cases (S4).
MATCH_CASES = [
    # address, work type, finish, permits [(type, trades, issued)], expected result, stated (like-for-like, detached)
    (
        "2 DRIFTWOOD TER",
        "kitchen",
        "2025-04-04",
        [("Alteration", ["plumbing"], "2025-01-10"), ("Alteration", ["electrical"], "2025-02-11")],
        "possible",
        None,
    ),
    (
        "4 DRIFTWOOD TER",
        "plumbing",
        "2025-05-05",
        [("Alteration", ["electrical"], "2025-03-01")],
        "none_in_window_trade",
        None,
    ),
    ("6 DRIFTWOOD TER", "deck", "2025-03-03", [], "none", None),
    ("16 GULL ROCK RD", "roof", "2025-06-01", [], "none", (True, True)),
    ("18 GULL ROCK RD", "windows", "2025-06-01", [], "none", (True, False)),
    (
        "20 GULL ROCK RD",
        "roof",
        "2025-06-01",
        [("Alteration", ["building"], "2023-01-05")],
        "none",
        (False, True),
    ),
    (
        "22 GULL ROCK RD",
        "bathroom",
        "2025-07-07",
        [("Alteration", ["plumbing"], "2025-07-18")],
        "on_file",
        None,
    ),
    ("24 GULL ROCK RD", "bathroom", "2025-07-07", [("Alteration", ["plumbing"], "2025-07-25")], "none", None),
    ("30 SALTMEADOW DR", "electrical", "2025-02-02", [("Alteration", [], "2024-12-01")], "no_details", None),
    ("32 SALTMEADOW DR", "addition", "2025-09-09", [("New", [], "2025-01-15")], "on_file", None),
    (
        "34 SALTMEADOW DR",
        "other",
        "2025-09-09",
        [("Alteration", ["building"], "2025-05-15")],
        "no_check",
        None,
    ),
]
SPECIAL_ADDRESSES = [
    # condo building (units, condo qualifiers), two parcels sharing an address, a number range
    ("60 BAYBERRY CIR UNIT 1", "C0001"),
    ("60 BAYBERRY CIR UNIT 2", "C0002"),
    ("60 BAYBERRY CIR UNIT 3", "C0003"),
    ("8 DRIFTWOOD TER", ""),
    ("8 DRIFTWOOD TER", ""),
    ("10-12 GULL ROCK RD", ""),
]


def ymd(s: str) -> date:
    return date.fromisoformat(s)


class World:
    def __init__(self) -> None:
        self.rng = random.Random(SEED)
        self.parcels: list[dict[str, object]] = []
        self.permits: list[dict[str, str]] = []
        self.block = 100
        self.next_pk = 999900000001
        self.next_permit = 1

    def parcel(self, address: str, qualifier: str = "", prop_class: str = "2") -> str:
        self.block += 1
        lot = str(self.rng.randint(1, 40))
        pin = f"{TOWN}_{self.block}_{lot}" + (f"_{qualifier}" if qualifier else "")
        lat = round(40.2050 + self.rng.uniform(-0.01, 0.01), 6)
        lon = round(-74.0900 + self.rng.uniform(-0.01, 0.01), 6)
        self.parcels.append(
            {
                "pin": pin,
                "town": TOWN,
                "block": str(self.block),
                "lot": lot,
                "qualifier": qualifier,
                "address": address,
                "prop_class": prop_class,
                "year_built": self.rng.choice([1948, 1955, 1962, 1971, 1986, 1994, 2003]),
                "lat": lat,
                "lon": lon,
            }
        )
        return pin

    def by_pin(self, pin: str) -> dict[str, object]:
        return next(p for p in self.parcels if p["pin"] == pin)

    def permit(
        self,
        pin: str,
        ptype: str,
        trades: list[str],
        issued: str,
        final: str | None = None,
        permit_no: str | None = None,
        cost: str | None = None,
    ) -> str:
        p = self.by_pin(pin)
        pk = str(self.next_pk)
        self.next_pk += 1
        fee = {
            t: str(self.rng.choice([45, 60, 85, 120, 160])) if t in trades else "0"
            for t in ("building", "plumbing", "electrical", "fire")
        }
        issued_d = ymd(issued)
        no = permit_no or f"{issued_d.year % 100:02d}-{self.next_permit:04d}"
        self.next_permit += 1
        processed = (ymd(final) if final else issued_d) + timedelta(days=self.rng.randint(20, 45))
        row = {
            "block": str(p["block"]),
            "lot": str(p["lot"]),
            "buildfee": fee["building"],
            "plumbfee": fee["plumbing"],
            "electfee": fee["electrical"],
            "firefee": fee["fire"],
            "certdate": f"{final}T00:00:00.000" if final else None,
            "certtypedesc": "Certificate of Approval" if final else None,
            "constcost": cost or str(self.rng.choice([2500, 8000, 15000, 24000, 41000])),
            "muniname": "SAMPLETON",
            "county": "SAMPLE",
            "permitdate": f"{issued}T00:00:00.000",
            "permitno": no,
            "permittypedesc": ptype,
            "pk": pk,
            "processdate": f"{processed.isoformat()}T00:00:00.000",
            "squarefeet": "0",
            "treasurycode": TOWN,
            "usegroup": "R-5",
            "usegroupdesc": "International Residential Code",
            "sourcedesc": "Sample town (made up)",
        }
        self.permits.append({k: v for k, v in row.items() if v is not None})
        return pk


def build() -> tuple[list[dict[str, object]], list[dict[str, str]], dict[str, object]]:
    w = World()
    jobs = []
    home_pins: dict[str, str] = {}
    for per in PERSONAS:
        pin = w.parcel(str(per["address"]))
        home_pins[str(per["code"])] = pin
        ptype, trades, issued, final = per["permit"]  # type: ignore[misc]
        pk = w.permit(pin, ptype, trades, issued, final)
        jobs.append(
            {k: v for k, v in per.items() if k != "permit"}
            | {"pin": pin, "permit_pk": pk, "contractor": "harbor"}
        )
    dana_pin = home_pins["H1"]
    town_requests = []
    for tp in TOWN_PERSONAS:
        pk = w.permit(
            dana_pin,
            str(tp["type"]),
            list(tp["trades"]),
            str(tp["issued"]),
            None,  # type: ignore[arg-type]
            permit_no=str(tp["permit_no"]),
        )
        town_requests.append(tp | {"pin": dana_pin, "permit_pk": pk})
    k1_type, k1_trades, k1_issued, k1_final = K1["permit"]  # type: ignore[misc]
    k1_pk = w.permit(dana_pin, k1_type, k1_trades, k1_issued, k1_final)
    match_cases = []
    for addr, work, finish, permits, expected, stated in MATCH_CASES:
        pin = w.parcel(addr)
        pks = [w.permit(pin, t, tr, iss) for t, tr, iss in permits]
        match_cases.append(
            {
                "address": addr,
                "pin": pin,
                "work_type": work,
                "finish": finish,
                "permit_pks": pks,
                "expected": expected,
                "stated_like_for_like": stated[0] if stated else None,
                "stated_detached_1_2_family": stated[1] if stated else None,
            }
        )
    for addr, q in SPECIAL_ADDRESSES:
        w.parcel(addr, qualifier=q, prop_class="2" if not q else "2")
    # Filler: homes with ordinary permit history, so the town looks lived in.
    for street in STREETS:
        for _ in range(2):
            n = w.rng.randrange(50, 300, 2)
            pin = w.parcel(f"{n} {street}")
            year = w.rng.choice([2019, 2020, 2021, 2022, 2023, 2024])
            w.permit(
                pin,
                w.rng.choice(["Alteration", "Alteration", "Addition"]),
                w.rng.sample(["building", "plumbing", "electrical"], k=w.rng.randint(1, 2)),
                f"{year}-{w.rng.randint(1, 12):02d}-{w.rng.randint(1, 28):02d}",
                f"{year + 1}-0{w.rng.randint(1, 6)}-1{w.rng.randint(0, 8)}",
            )

    people = []
    contractors = []
    for key, name, lic, trades, contact, email in CONTRACTORS:
        people.append({"key": f"contractor:{key}", "name": contact, "email": email})
        contractors.append(
            {"key": key, "name": name, "license": lic, "trades": trades, "person": f"contractor:{key}"}
        )
    for per in PERSONAS:
        people.append({"key": f"homeowner:{per['code']}", "name": per["name"], "email": per["email"]})
    people.append({"key": "clerk", "name": "Sampleton Borough Clerk", "email": "clerk@sampleton.test"})
    people.append({"key": "ops:priya", "name": "Priya Natarajan", "email": "priya@closeout.test"})
    people.append({"key": "agent:tom", "name": "Tom Reyes", "email": "tom.reyes@harborrealty.example"})
    accounts = [
        {
            "key": "mike",
            "kind": "contractor",
            "person": "contractor:harbor",
            "contractor": "harbor",
            "order": 1,
        },
        {"key": "dana", "kind": "homeowner", "person": "homeowner:H1", "order": 2},
        {"key": "priya", "kind": "ops", "person": "ops:priya", "order": 3},
        {"key": "pat", "kind": "contractor", "person": "contractor:inlet", "contractor": "inlet", "order": 4},
        {"key": "marcus", "kind": "homeowner", "person": "homeowner:H2", "order": 5},
    ]
    homes = [{"pin": dana_pin, "owner": "dana"}, {"pin": home_pins["H2"], "owner": "marcus"}]
    # Homes nobody has claimed yet, each with a letter code (simulated mail). Codes are made up.
    for code_n, (per_code) in enumerate(("H4", "H5")):
        homes.append(
            {"pin": home_pins[per_code], "owner": None, "claim_code": f"SAMPLE-{4721 + code_n * 1111}"}
        )
    grants = [
        {"account": "mike", "actions": ["send_email"], "daily_limit": 200},
        {"account": "pat", "actions": ["send_email"], "daily_limit": 200},
        {"account": "dana", "actions": ["send_email", "share_report"], "daily_limit": 50},
        {"account": "marcus", "actions": ["send_email", "share_report"], "daily_limit": 50},
    ]
    world = {
        "town": {"code": TOWN, "name": "Sampleton", "clerk": "clerk"},
        "people": people,
        "contractors": contractors,
        "accounts": accounts,
        "homes": homes,
        "grants": grants,
        "jobs": jobs,
        "town_requests": town_requests,
        "k1": {k: v for k, v in K1.items() if k != "permit"}
        | {"pin": dana_pin, "permit_pk": k1_pk, "homeowner": "dana"},
        "match_cases": match_cases,
    }
    w.parcels.sort(key=lambda p: str(p["pin"]))
    return w.parcels, w.permits, world


def manasquan_addresses() -> set[str]:
    path = HERE / "seed" / "parcels_1329.csv"
    if not path.exists():
        return set()
    with open(path, newline="") as f:
        return {r["address"].strip().upper() for r in csv.DictReader(f) if r["address"].strip()}


def manasquan_streets() -> set[str]:
    out = set()
    for a in manasquan_addresses():
        parts = a.split(" ", 1)
        if len(parts) == 2 and parts[0][:1].isdigit():
            out.add(parts[1])
    return out


def write(out: Path = OUT) -> None:
    parcels, permits, world = build()
    clash = {str(p["address"]) for p in parcels} & manasquan_addresses()
    assert not clash, f"Sampleton addresses clash with Manasquan: {sorted(clash)[:5]}"
    street_clash = set(STREETS) & manasquan_streets()
    assert not street_clash, f"Sampleton streets exist in Manasquan: {street_clash}"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "parcels_9999.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=PARCEL_COLUMNS, lineterminator="\n")
        wr.writeheader()
        wr.writerows(parcels)
    (out / "permits_9999.json").write_text(
        "[\n" + ",\n".join(json.dumps(p, sort_keys=True) for p in permits) + "\n]\n"
    )
    (out / "world.json").write_text(json.dumps(world, indent=1, sort_keys=True) + "\n")
    print(
        f"Sampleton: {len(parcels)} parcels, {len(permits)} permits, {len(world['people'])} people, "  # type: ignore[arg-type]
        f"{len(world['jobs'])} persona jobs"
    )  # type: ignore[arg-type]


if __name__ == "__main__":
    write()
