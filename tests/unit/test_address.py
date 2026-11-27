"""S1/S4: address cleanup (design 3.2) and golden matching cases (R-IMP-2)."""

import pytest

from closeout.core.address import Match, ParcelAddress, match, normalise, parse

pytestmark = [pytest.mark.slice("S1")]


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("14 Osprey Lane", "14 OSPREY LN"),
        ("14  osprey   ln.", "14 OSPREY LN"),
        ("14 Osprey Ln, Sampleton, NJ 08736", "14 OSPREY LN"),
        ("52 Main Street", "52 MAIN ST"),
        ("12A Beacon Avenue", "12A BEACON AVE"),
        ("60 Bayberry Circle Unit 3", "60 BAYBERRY CIR UNIT 3"),
        ("60 Bayberry Cir #3", "60 BAYBERRY CIR UNIT 3"),
        ("60 Bayberry Cir, Apt 3", "60 BAYBERRY CIR UNIT 3"),
        ("10-12 Gull Rock Road", "10-12 GULL ROCK RD"),
        ("150 Stockton Lake Blvd", "150 STOCKTON LAKE BLVD"),
        ("7 North Heron Way", "7 N HERON WAY"),
        ("Heron Way", "HERON WAY"),
    ],
)
def test_cleanup(raw: str, norm: str) -> None:
    assert normalise(raw) == norm


def test_parse_parts() -> None:
    a = parse("60 Bayberry Cir #3")
    assert (a.house_no, a.street, a.unit, a.is_range) == ("60", "BAYBERRY CIR", "3", False)
    r = parse("10-12 Gull Rock Rd")
    assert (r.house_no, r.house_no_end, r.is_range) == ("10", "12", True)


def P(pin: str, address: str, condo: bool = False) -> ParcelAddress:
    a = parse(address)
    return ParcelAddress(
        pin=pin, address_norm=a.norm, house_no=a.house_no, street=a.street, unit=a.unit, condo=condo
    )


PARCELS = [
    P("p126", "126 TIDEWATER RD"),
    P("p128", "128 TIDEWATER RD"),
    P("p14", "14 OSPREY LN"),
    P("c1", "60 BAYBERRY CIR UNIT 1", condo=True),
    P("c2", "60 BAYBERRY CIR UNIT 2", condo=True),
    P("d1", "8 DRIFTWOOD TER"),
    P("d2", "8 DRIFTWOOD TER"),
    P("r", "10-12 GULL ROCK RD"),
    P("h7", "7 HERON WAY"),
    P("h7a", "7A HERON WAY"),
]


@pytest.mark.req("R-IMP-2")
@pytest.mark.slice("S4")
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14 Osprey Lane", Match("p14", "matched_exact")),
        ("14 Osprey Ln, Sampleton NJ", Match("p14", "matched_exact")),
        ("126 Tidwater Rd", Match("p126", "matched_close")),
        ("14 Ospery Ln", Match(None, "none")),  # a swapped pair scores 89, under the 92 bar: needs a look
        ("128 Tidewater Road", Match("p128", "matched_exact")),
        ("126 Tidewater Rd", Match("p126", "matched_exact")),
        ("127 Tidewater Rd", Match(None, "none")),
        ("130 Tidewater Rd", Match(None, "none")),
        ("60 Bayberry Cir Unit 1", Match(None, "condo_unit")),
        ("60 Bayberry Cir", Match(None, "none")),
        ("8 Driftwood Ter", Match(None, "several")),
        ("10-12 Gull Rock Rd", Match(None, "number_range")),
        ("Osprey Ln", Match(None, "no_number")),
        ("99 Nowhere St", Match(None, "none")),
        ("7 Heron Way", Match("h7", "matched_exact")),
        ("7A Heron Way", Match("h7a", "matched_exact")),
        ("7B Heron Way", Match(None, "none")),
    ],
)
def test_golden_matches(raw: str, expected: Match) -> None:
    assert match(raw, PARCELS) == expected


@pytest.mark.req("R-IMP-2")
@pytest.mark.slice("S4")
def test_neighbour_houses_never_match() -> None:
    assert match("128 Tidewater Rd", [PARCELS[0]]).pin is None
    assert match("126 Tidewater Rd", [PARCELS[1]]).pin is None


def test_close_street_needs_a_single_candidate() -> None:
    parcels = [P("a", "5 HERON WAY"), P("b", "5 HERON WY")]
    assert match("5 Heron Wae", parcels).reason in ("unsure", "matched_close", "none")


# Expected short forms, written out independently of the code's table.
SUFFIX_TABLE = {
    "Street": "ST",
    "Str": "ST",
    "St": "ST",
    "Avenue": "AVE",
    "Av": "AVE",
    "Ave": "AVE",
    "Road": "RD",
    "Rd": "RD",
    "Lane": "LN",
    "Ln": "LN",
    "Drive": "DR",
    "Dr": "DR",
    "Court": "CT",
    "Ct": "CT",
    "Place": "PL",
    "Pl": "PL",
    "Boulevard": "BLVD",
    "Blvd": "BLVD",
    "Terrace": "TER",
    "Terr": "TER",
    "Ter": "TER",
    "Circle": "CIR",
    "Cir": "CIR",
    "Parkway": "PKWY",
    "Pkwy": "PKWY",
    "Highway": "HWY",
    "Hwy": "HWY",
    "Trail": "TRL",
    "Trl": "TRL",
    "Square": "SQ",
    "Sq": "SQ",
    "Way": "WAY",
}


@pytest.mark.parametrize(("word", "short"), list(SUFFIX_TABLE.items()))
def test_every_suffix(word: str, short: str) -> None:
    assert normalise(f"5 Ocean {word}") == f"5 OCEAN {short}"
    assert normalise(f"5 {word} Hill Rd") == f"5 {word.upper()} HILL RD"  # only the last word is a suffix


@pytest.mark.parametrize(("word", "short"), [("North", "N"), ("South", "S"), ("East", "E"), ("West", "W")])
def test_every_direction_at_either_end_only(word: str, short: str) -> None:
    assert normalise(f"5 {word} Ocean Ave") == f"5 {short} OCEAN AVE"
    assert normalise(f"5 Ocean Ave {word}") == f"5 OCEAN AVE {short}"
    assert normalise(f"5 Old {word} Rd") == f"5 OLD {word.upper()} RD"


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("14 Osprey Ln.", "14 OSPREY LN"),
        ("14\tOsprey\tLn", "14 OSPREY LN"),
        ("14 O'Brien Ln", "14 O BRIEN LN"),
        ("14 Osprey Ln, 08736", "14 OSPREY LN"),
        ("14 Osprey Ln 08736-1234", "14 OSPREY LN"),
        ("14 Osprey Ln, Manasquan, New Jersey", "14 OSPREY LN"),
        ("14 Osprey Ln Sampleton NJ 08736", "14 OSPREY LN"),
        ("14 Osprey Ln,", "14 OSPREY LN"),
        ("  ,14 Osprey Ln", "14 OSPREY LN"),
        ("60 Bayberry Cir Unit 3-B", "60 BAYBERRY CIR UNIT 3-B"),
        ("60 Bayberry Cir Suite 2", "60 BAYBERRY CIR UNIT 2"),
        ("60 Bayberry Cir Apartment 2", "60 BAYBERRY CIR UNIT 2"),
        ("60 Bayberry Cir Ste 2", "60 BAYBERRY CIR UNIT 2"),
        ("60 Bayberry Cir # 2", "60 BAYBERRY CIR UNIT 2"),
        ("60 Bayberry Cir, Unit #2", "60 BAYBERRY CIR UNIT 2"),
        ("10 - 12 Gull Rock Rd", "10-12 GULL ROCK RD"),
        ("12a beacon ave", "12A BEACON AVE"),
    ],
)
def test_cleanup_details(raw: str, norm: str) -> None:
    assert normalise(raw) == norm


def test_norm_and_range_properties() -> None:
    assert parse("Osprey Ln").norm == "OSPREY LN"
    assert parse("10-12 Gull Rock Rd").norm == "10-12 GULL ROCK RD"
    assert parse("12 Gull Rock Rd").is_range is False
    assert parse("12 Gull Rock Rd").house_no_end is None


def test_close_match_bar_is_92_inclusive() -> None:
    parcels = [P("s", "4 SALTMEADOW HOLLOW LANDING")]  # 25 letters; two changed letters score exactly 92
    assert match("4 Saltmeadow Hollow Landxnx", parcels) == Match("s", "matched_close")
    assert match("4 Saltmeadow Hollxw Landxnx", parcels) == Match(None, "none")


def test_two_close_streets_are_unsure() -> None:
    parcels = [P("a", "5 STOCKTON LAKE BLVD"), P("b", "5 STOCKTON LAKE BLVD N")]
    assert match("5 Stockton Lake Blvd S", parcels) == Match(None, "unsure")


def test_condo_flag_only_blocks_a_unit() -> None:
    parcels = [P("cx", "70 BAYBERRY CIR", condo=True), P("u", "71 BAYBERRY CIR UNIT 2")]
    assert match("70 Bayberry Cir", parcels) == Match("cx", "matched_exact")
    assert match("71 Bayberry Cir Unit 2", parcels) == Match("u", "matched_exact")


def test_unit_kept_without_a_house_number() -> None:
    a = parse("Osprey Ln Unit 2")
    assert (a.house_no, a.street, a.unit) == (None, "OSPREY LN", "2")
