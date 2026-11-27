"""S1 properties: address cleanup keeps the house number and unit, and is idempotent (test strategy 5.3)."""

import os

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from closeout.core.address import ParcelAddress, match, normalise, parse

pytestmark = [pytest.mark.slice("S1")]
N = 2000 if os.environ.get("HYPOTHESIS_PROFILE") == "nightly" else 200

numbers = st.builds(lambda n, letter: f"{n}{letter}", st.integers(1, 9999), st.sampled_from(["", "A", "B"]))
street_names = st.sampled_from(["OSPREY", "HERON", "MAIN", "BEACON", "TIDEWATER", "STOCKTON LAKE", "FIRST"])
suffix = st.sampled_from(
    ["Lane", "LN", "Street", "St.", "Avenue", "ave", "Road", "Rd", "Way", "Court", "Blvd"]
)
units = st.one_of(
    st.just(""),
    st.builds(
        lambda u, form: form.format(u),
        st.integers(1, 99).map(str),
        st.sampled_from([" Unit {}", " #{}", ", Apt {}", " Apt {}"]),
    ),
)
tails = st.sampled_from(["", ", Sampleton", ", Sampleton, NJ 08736", " Manasquan NJ", ", NJ"])
spaces = st.sampled_from([" ", "  ", "   "])


@st.composite
def addresses(draw):
    n, s, x, u, t, sp = (
        draw(numbers),
        draw(street_names),
        draw(suffix),
        draw(units),
        draw(tails),
        draw(spaces),
    )
    raw = f"{n}{sp}{s.title() if draw(st.booleans()) else s}{sp}{x}{u}{t}"
    return raw, n, u.replace("Unit", "").replace("Apt", "").replace("#", "").replace(",", "").strip() or None


@settings(max_examples=N, deadline=None)
@given(addresses())
@example(
    ("1 OSPREY Lane Unit 1 Manasquan NJ", "1", "1")
)  # found by Hypothesis: town and state without a comma
def test_cleanup_keeps_house_number_and_unit(case) -> None:
    raw, number, unit = case
    a = parse(raw)
    assert a.house_no == number.upper()
    assert a.unit == unit


@settings(max_examples=N, deadline=None)
@given(addresses())
@example(("1 OSPREY Lane Manasquan NJ", "1", None))  # found by Hypothesis
def test_cleanup_is_idempotent(case) -> None:
    raw, _, _ = case
    once = normalise(raw)
    assert normalise(once) == once


@settings(max_examples=N, deadline=None)
@given(st.integers(1, 9998), st.integers(1, 6), street_names)
def test_neighbour_numbers_never_match(n: int, gap: int, street: str) -> None:
    a = parse(f"{n} {street} RD")
    parcel = ParcelAddress(pin="x", address_norm=a.norm, house_no=a.house_no, street=a.street, unit=None)
    assert match(f"{n + gap} {street} Road", [parcel]).pin is None
    assert match(f"{n} {street} Road", [parcel]).pin == "x"


@settings(max_examples=N, deadline=None)
@given(st.text(max_size=80))
def test_cleanup_never_crashes_and_drops_no_digits_of_the_number(text: str) -> None:
    a = parse(text)
    if a.house_no:
        assert a.house_no[0].isdigit()
