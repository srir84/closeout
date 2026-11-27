#!/usr/bin/env python3
"""Independent generator of made-up NJ home-repair contractor invoices (held-out test set).

Usage:
    python3 heldout_gen.py --set development --count 100 --seed 101 --out DIR
    python3 heldout_gen.py --set sealed      --count 200 --seed 202 --out DIR

Writes DIR/<id>.png|.jpg|.pdf plus DIR/truth.json. Everything is fictional; all emails end in
.example and all phone numbers are in the 555-01xx fictional range. Output is deterministic for a
given (--set, --count, --seed) with the same Pillow/numpy build.

Fonts (all SIL OFL 1.1, license texts in heldout_fonts/*-OFL.txt): Lato, PT Serif, Courier Prime,
IBM Plex Sans (variable).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
FONT_DIR = HERE / "heldout_fonts"

FIXED_PDF_TIME = time.gmtime(int(datetime(2026, 11, 1, tzinfo=UTC).timestamp()))
PAGE_W, PAGE_H = 1700, 2200  # 8.5 x 11 in at 200 dpi
INK = (28, 28, 34)
MUTED = (88, 90, 100)
LIGHT = (200, 202, 208)
NBSP = "\u00a0"

# --------------------------------------------------------------------------- content tables
CONTRACTORS = {
    "harbor": dict(
        name="Harbor & Pine Builders",
        lic="13VH01234567",
        email="office@harborpine.example",
        office="410 Commerce Way, Unit 3",
        town="Sampleton, NJ 08736",
        phone="732-555-0114",
        color=(34, 87, 66),
        prefix="HP",
        tagline="Kitchens · Baths · Additions · Windows",
        signer=("Dana Whitcomb", "Project Manager"),
    ),
    "inlet": dict(
        name="Inlet Roofing Co.",
        lic="13VH00077302",
        email="estimates@inletroof.example",
        office="22 Foundry St",
        town="Sampleton, NJ 08736",
        phone="732-555-0127",
        color=(30, 62, 118),
        prefix="IRC",
        tagline="Residential roofing since 1998",
        signer=("Ray Albrecht", "Owner"),
    ),
    "bayside": dict(
        name="Bayside Plumbing & Heating",
        lic="13VH00551920",
        email="service@baysideph.example",
        office="7 Wharf Industrial Ct",
        town="Sampleton, NJ 08736",
        phone="732-555-0139",
        color=(0, 104, 132),
        prefix="BPH",
        tagline="Plumbing · Heating · Cooling",
        signer=("Toni Marchetti", "Service Manager"),
    ),
    "tidewater": dict(
        name="Tidewater Electric LLC",
        lic="13VH00938114",
        email="jobs@tidewater-elec.example",
        office="1600 Mill Pond Plaza, Suite 2",
        town="Sampleton, NJ 08736",
        phone="732-555-0151",
        color=(168, 84, 18),
        prefix="TE",
        tagline="Licensed residential electrical contractor",
        signer=("Jules Okonkwo", "Office Manager"),
    ),
    "dune": dict(
        name="Dune Grass Decks",
        lic="13VH00410267",
        email="build@dunegrassdecks.example",
        office="95 Boatyard Rd",
        town="Sampleton, NJ 08736",
        phone="732-555-0166",
        color=(128, 100, 44),
        prefix="DGD",
        tagline="Custom decks, porches & railings",
        signer=("Morgan Ellery", "Estimator"),
    ),
}

WORK_TYPES = [
    "deck",
    "roof",
    "kitchen",
    "bathroom",
    "addition",
    "windows",
    "hvac",
    "plumbing",
    "electrical",
    "other",
]

# (min, max) contract total in dollars
TOTAL_RANGE = {
    "deck": (8_500, 46_000),
    "roof": (7_200, 38_000),
    "kitchen": (24_000, 140_000),
    "bathroom": (11_000, 58_000),
    "addition": (62_000, 140_000),
    "windows": (4_200, 31_000),
    "hvac": (5_400, 22_000),
    "plumbing": (1_200, 16_000),
    "electrical": (1_200, 18_500),
    "other": (2_100, 26_000),
}

# headline -> list of line items. Items are generic enough to belong clearly to the category.
JOBS = {
    "deck": [
        (
            "Construct new {a} x {b} ft composite deck at rear of house",
            [
                "Remove and dispose of existing deck",
                "Concrete footings, framing and ledger flashing",
                "Composite decking boards, fascia and hidden fasteners",
                "Aluminum railing system",
                "Deck stairs with landing pad",
                "Deck permit and township inspections",
            ],
        ),
        (
            "Replace existing wood deck with new pressure-treated deck and stairs",
            [
                "Demolition and haul-away of old deck",
                "Pressure-treated joists, beams and posts",
                "5/4 pressure-treated deck boards",
                "Wood railing with balusters",
                "New deck stairs",
                "Permit and inspections",
            ],
        ),
        (
            "Build raised {a} x {b} ft deck with built-in benches",
            [
                "Helical pier footings",
                "Framing and joist tape",
                "Composite deck boards and picture frame border",
                "Built-in bench seating",
                "Vinyl railing system",
                "Under-deck lattice skirting",
            ],
        ),
    ],
    "roof": [
        (
            "Tear off and replace asphalt shingle roof ({n} squares)",
            [
                "Tear-off and disposal of existing shingles",
                "Ice and water shield and synthetic underlayment",
                "Architectural shingles installed",
                "Ridge vent and new drip edge",
                "Step and chimney flashing",
                "Roofing permit",
            ],
        ),
        (
            "Remove two layers of shingles and install new architectural shingle roof",
            [
                "Remove two layers of shingles",
                "Replace damaged roof decking (plywood sheets)",
                "Underlayment and starter strip",
                "Architectural shingles and hip/ridge caps",
                "Pipe boots and roof vents",
                "Dumpster and cleanup",
            ],
        ),
        (
            "Replace low-slope porch roof with EPDM membrane and re-shingle main roof",
            [
                "Remove existing porch roofing",
                "EPDM rubber membrane system on porch roof",
                "Re-shingle main roof",
                "New aluminum drip edge and flashing",
                "Roofing permit and cleanup",
            ],
        ),
    ],
    "kitchen": [
        (
            "Complete kitchen remodel: new cabinets, quartz countertops and tile backsplash",
            [
                "Demolition of existing kitchen cabinets and counters",
                "Semi-custom cabinets supplied and installed",
                "Quartz countertops with undermount sink cutout",
                "Ceramic tile backsplash",
                "Luxury vinyl plank kitchen flooring",
                "Appliance installation",
                "Drywall repair and painting",
            ],
        ),
        (
            "Kitchen renovation with new island, flooring and appliance installation",
            [
                "Kitchen demolition and disposal",
                "Kitchen island with seating overhang",
                "Cabinet refacing and new hardware",
                "Granite countertops",
                "Hardwood flooring in kitchen",
                "Appliance installation",
                "Paint and trim",
            ],
        ),
    ],
    "bathroom": [
        (
            "Full remodel of second-floor hall bathroom",
            [
                "Demolition of existing bathroom finishes",
                "Tub-to-shower conversion with glass door",
                "Vanity, toilet and faucet installed",
                "Porcelain floor tile",
                "Bathroom exhaust fan replacement",
                "Paint and trim",
            ],
        ),
        (
            "Primary bathroom renovation with walk-in tile shower",
            [
                "Bathroom demolition",
                "Walk-in tile shower with niche and bench",
                "Double vanity with quartz top",
                "Heated tile floor",
                "Frameless glass shower enclosure",
                "Paint, trim and accessories",
            ],
        ),
    ],
    "addition": [
        (
            "Build {a} x {b} ft one-story family room addition",
            [
                "Architectural drawings and building permits",
                "Excavation and concrete foundation",
                "Framing, sheathing and house wrap",
                "Addition roof framing and shingles",
                "Windows and exterior door for addition",
                "Insulation and drywall",
                "Interior trim, flooring and paint",
            ],
        ),
        (
            "Second-floor bedroom addition over existing garage",
            [
                "Engineering and permits",
                "Structural reinforcement of garage",
                "Framing and sheathing for addition",
                "Addition roof tie-in",
                "Windows for new bedroom",
                "Insulation, drywall and closet",
                "Flooring and paint",
            ],
        ),
    ],
    "windows": [
        (
            "Replace {n} windows with vinyl double-hung replacement windows",
            [
                "Remove and dispose of existing windows",
                "Vinyl double-hung replacement windows",
                "Exterior aluminum trim capping",
                "Interior casing and sill repair",
                "Low-expansion foam sealing",
            ],
        ),
        (
            "Install {n} energy-efficient casement windows and one sliding patio door",
            [
                "Removal of old windows and patio door",
                "Fiberglass casement windows",
                "Sliding patio door",
                "Window flashing and sealing",
                "Interior trim and paint touch-up",
            ],
        ),
    ],
    "hvac": [
        (
            "Install new 3-ton heat pump system with air handler",
            [
                "Remove and dispose of old heating and cooling equipment",
                "High-efficiency heat pump condenser",
                "Air handler with matching coil",
                "Refrigerant line set and condensate pump",
                "Programmable thermostat, startup and commissioning",
                "Mechanical permit",
            ],
        ),
        (
            "Replace gas furnace and central air conditioner",
            [
                "Remove old furnace and condenser",
                "96% AFUE gas furnace",
                "16 SEER central air conditioner",
                "Ductwork modifications at plenum",
                "Thermostat and system startup",
                "Mechanical permit",
            ],
        ),
        (
            "Install ductless mini-split heating and cooling system (3 zones)",
            [
                "Outdoor multi-zone heat pump unit",
                "Three wall-mounted indoor heads",
                "Line sets and line-hide covers",
                "Condensate drains",
                "Startup and commissioning",
                "Mechanical permit",
            ],
        ),
    ],
    "plumbing": [
        (
            "Replace 50-gallon gas water heater",
            [
                "Drain and remove old water heater",
                "50-gallon gas water heater",
                "Expansion tank and gas shutoff valve",
                "Venting and supply connections",
                "Plumbing permit and inspection",
            ],
        ),
        (
            "Repipe house water supply lines with PEX",
            [
                "Remove old galvanized supply piping",
                "PEX supply lines and manifold",
                "New shutoff valves at fixtures",
                "Wall openings and patching",
                "Plumbing permit and inspection",
            ],
        ),
        (
            "Replace main sewer line from house to curb",
            [
                "Camera inspection of sewer line",
                "Excavation and removal of old clay pipe",
                "New PVC sewer line and cleanout",
                "Backfill and grade restoration",
                "Plumbing permit and inspection",
            ],
        ),
        (
            "Install sump pump with battery backup system",
            [
                "Excavate sump pit in basement floor",
                "Submersible sump pump",
                "Battery backup pump",
                "Discharge piping to exterior",
                "Concrete patch and cleanup",
            ],
        ),
    ],
    "electrical": [
        (
            "Upgrade electrical service to 200-amp panel",
            [
                "Remove old 100-amp fuse panel",
                "200-amp main breaker panel",
                "New meter socket and service entrance cable",
                "Grounding and bonding",
                "Utility coordination and electrical permit",
            ],
        ),
        (
            "Install Level 2 EV charger in garage",
            [
                "Dedicated 60-amp circuit from panel",
                "Wall-mounted Level 2 charger installed",
                "Conduit and wiring",
                "Electrical permit and inspection",
            ],
        ),
        (
            "Install generator transfer switch and power inlet",
            [
                "Manual transfer switch",
                "Exterior power inlet box",
                "Wiring and conduit",
                "Electrical permit and inspection",
            ],
        ),
        (
            "Add recessed lighting and new circuits in living room and hallway",
            [
                "Twelve LED recessed lights",
                "Two new 20-amp circuits",
                "Dimmer switches",
                "Drywall patching",
                "Electrical permit and inspection",
            ],
        ),
    ],
    "other": [
        (
            "Replace vinyl siding on all elevations",
            [
                "Remove and dispose of existing siding",
                "House wrap and flashing tape",
                "Insulated vinyl siding installed",
                "Aluminum soffit and fascia wrap",
                "Dumpster and cleanup",
            ],
        ),
        (
            "Finish basement recreation room (framing, drywall, paint and carpet)",
            [
                "Wall framing and insulation",
                "Drywall hung, taped and finished",
                "Drop ceiling",
                "Interior paint",
                "Carpet and pad installed",
            ],
        ),
        (
            "Interior painting of first floor and stairwell",
            [
                "Surface preparation and patching",
                "Walls - two coats",
                "Ceilings - one coat",
                "Trim and doors",
                "Stairwell walls and railing",
            ],
        ),
        (
            "Install hardwood flooring in living and dining rooms",
            [
                "Remove existing carpet and pad",
                "Subfloor leveling",
                "Oak hardwood flooring installed",
                "Sand and finish",
                "Baseboard and shoe molding",
            ],
        ),
    ],
}

CHANGE_ORDERS = {
    "deck": "add privacy screen wall",
    "roof": "replace additional rotted roof decking",
    "kitchen": "add pantry cabinet and under-cabinet lighting",
    "bathroom": "add linen closet and niche",
    "addition": "upgrade to cathedral ceiling",
    "windows": "add two basement windows",
    "hvac": "add duct sealing in attic",
    "plumbing": "replace main shutoff valve",
    "electrical": "add exterior GFCI outlets",
    "other": "extend work to front porch area",
}

STREETS = [
    "Osprey Ln",
    "Heron Way",
    "Tidewater Rd",
    "Sandbar Ct",
    "Beacon Ave",
    "Lantern Pl",
    "Driftwood Ter",
    "Gull Rock Rd",
    "Saltmeadow Dr",
    "Bayberry Cir",
]
JOB_TOWN = "Sampleton, NJ 08736"

BILL_STREETS = ["Crestview Ave", "Old Mill Rd", "Linden St", "Parkside Dr", "Hawthorne Ct", "Orchard Ln"]
BILL_TOWNS = [
    "Fernhollow, NY 10990",
    "Lakemont Ridge, PA 18940",
    "Cedarview, NJ 07999",
    "Marlow Point, CT 06999",
]

# long street names used only inside line items (other_address trap)
LONG_ADDR_ITEMS = [
    "Haul-away and disposal of debris at recycling center, {n} North Saltmeadow Industrial Parkway",
    "Custom trim fabricated at our shop, {n} Old Lighthouse Ferry Landing Road",
    "Pickup of special-order materials from supplier yard at {n} Upper Bayberry Commerce Boulevard",
    "Temporary storage of homeowner furniture at {n} East Gull Rock Crossing Business Center Drive",
    "Return of unused materials to distributor at {n} South Heron Landing Professional Plaza Road",
    "Dumpster staged at owner-approved lot, {n} West Driftwood Harbor Marketplace Avenue",
]

FIRST = [
    "Priya",
    "Marcus",
    "Elena",
    "Kwame",
    "Sofia",
    "Hiroshi",
    "Fatima",
    "Liam",
    "Aaliyah",
    "Mateo",
    "Mei",
    "Darnell",
    "Ingrid",
    "Rahul",
    "Chloe",
    "Tomasz",
    "Amara",
    "Diego",
    "Noor",
    "Ethan",
    "Yuki",
    "Olusegun",
    "Grace",
    "Andrei",
    "Leilani",
    "Samir",
    "Hannah",
    "Jamal",
    "Lucia",
    "Owen",
    "Anjali",
    "Kofi",
    "Beatriz",
    "Connor",
    "Zainab",
    "Wei",
    "Nadia",
    "Gabriel",
    "Thandiwe",
    "Rosa",
]
LAST = [
    "Raman",
    "Okafor",
    "Castellano",
    "Brennan",
    "Nakamura",
    "Haddad",
    "Kowalski",
    "Mensah",
    "Lindqvist",
    "Delgado",
    "Chen",
    "Patel",
    "Moreau",
    "O'Sullivan",
    "Novak",
    "Abernathy",
    "Ferreira",
    "Yilmaz",
    "Adeyemi",
    "Takahashi",
    "Gallagher",
    "Quispe",
    "Rosenberg",
    "Nguyen",
    "Halvorsen",
    "Bianchi",
    "Reyes-Morgan",
    "Szabo",
    "Kim",
    "Oyelaran",
    "Fitzgerald",
    "Achterberg",
    "Mahmoud",
    "Petrov",
]

TRAPS = [
    "deposit",
    "sales_tax",
    "billing_address",
    "change_order",
    "no_warranty",
    "invoice_date",
    "other_address",
    "none",
]
LAYOUTS = ["classic", "band", "sidebar", "receipt", "letter", "form"]
PDF_LAYOUTS = ["classic", "band", "sidebar", "letter", "form"]

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
NUM_WORDS = {1: "one", 2: "two", 3: "three", 5: "five", 10: "ten"}


# --------------------------------------------------------------------------- formatting helpers
def fmt_date(d: date, style: str) -> str:
    if style == "long":
        return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    if style == "abbr":
        return f"{MONTHS[d.month - 1][:3]} {d.day}, {d.year}"
    if style == "iso":
        return d.isoformat()
    if style == "dmy":
        return f"{d.day} {MONTHS[d.month - 1]} {d.year}"
    if style == "us":  # only used when unambiguous (day > 12); otherwise fall back to abbreviated month
        return f"{d.month:02d}/{d.day:02d}/{d.year}" if d.day > 12 else fmt_date(d, "abbr")
    raise ValueError(style)


def fmt_money(cents: int, always_cents: bool = True) -> str:
    neg = cents < 0
    cents = abs(cents)
    dollars, c = divmod(cents, 100)
    s = f"${dollars:,}"
    if c or always_cents:
        s += f".{c:02d}"
    return "-" + s if neg else s


def mix(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


# --------------------------------------------------------------------------- fonts
FAMILIES = {
    "lato": ("Lato-Regular.ttf", "Lato-Bold.ttf", None),
    "ptserif": ("PT_Serif-Web-Regular.ttf", "PT_Serif-Web-Bold.ttf", None),
    "courier": ("CourierPrime-Regular.ttf", "CourierPrime-Bold.ttf", None),
    "plex": ("IBMPlexSans-VF.ttf", "IBMPlexSans-VF.ttf", ([400, 100], [700, 100])),
}
_font_cache: dict = {}


def load_font(family: str, bold: bool, size: int) -> ImageFont.FreeTypeFont:
    key = (family, bold, size)
    if key not in _font_cache:
        reg, bld, axes = FAMILIES[family]
        f = ImageFont.truetype(str(FONT_DIR / (bld if bold else reg)), size)
        if axes:
            f.set_variation_by_axes(axes[1] if bold else axes[0])
        _font_cache[key] = f
    return _font_cache[key]


class Fonts:
    def __init__(self, body: str, head: str, scale: float):
        self.body_family, self.head_family, self.scale = body, head, scale

    def __call__(self, size: float, bold: bool = False, head: bool = False):
        return load_font(
            self.head_family if head else self.body_family, bold, max(14, int(round(size * self.scale)))
        )


# --------------------------------------------------------------------------- block layout engine
class NullDraw:
    def __getattr__(self, name):
        return lambda *a, **k: None


def wrap(text: str, font, width: float) -> list[str]:
    out = []
    for para in text.split("\n"):
        words, cur = para.split(" "), ""
        for w in words:
            t = (cur + " " + w).strip()
            if font.getlength(t.replace(NBSP, " ")) <= width or not cur:
                cur = t
            else:
                out.append(cur)
                cur = w
        out.append(cur)
    return [line.replace(NBSP, " ") for line in out]


def lh(font, k=1.32):
    return int(round(font.size * k))


class Block:
    def render(self, d, x, y, w) -> int:
        raise NotImplementedError

    def height(self, w):
        return self.render(NullDraw(), 0, 0, w)


class Spacer(Block):
    def __init__(self, h):
        self.h = h

    def render(self, d, x, y, w):
        return self.h


class Text(Block):
    def __init__(self, text, font, fill=INK, align="left", k=1.32):
        self.text, self.font, self.fill, self.align, self.k = text, font, fill, align, k

    def render(self, d, x, y, w):
        h = 0
        for line in wrap(self.text, self.font, w):
            tw = self.font.getlength(line)
            tx = x if self.align == "left" else (x + w - tw if self.align == "right" else x + (w - tw) / 2)
            d.text((tx, y + h), line, font=self.font, fill=self.fill)
            h += lh(self.font, self.k)
        return h


class Rule(Block):
    def __init__(self, color=LIGHT, thick=2, pad=12, dashed=False, double=False):
        self.color, self.thick, self.pad, self.dashed, self.double = color, thick, pad, dashed, double

    def render(self, d, x, y, w):
        yy = y + self.pad
        rows = [yy, yy + self.thick + 5] if self.double else [yy]
        for r in rows:
            if self.dashed:
                for xx in range(int(x), int(x + w), 22):
                    d.rectangle([xx, r, min(xx + 12, x + w), r + self.thick - 1], fill=self.color)
            else:
                d.rectangle([x, r, x + w, r + self.thick - 1], fill=self.color)
        return 2 * self.pad + self.thick + (self.thick + 5 if self.double else 0)


class Stack(Block):
    def __init__(self, blocks, gap=0):
        self.blocks, self.gap = blocks, gap

    def render(self, d, x, y, w):
        h = 0
        for i, b in enumerate(self.blocks):
            h += b.render(d, x, y + h, w)
            if i < len(self.blocks) - 1:
                h += self.gap
        return h


class Columns(Block):
    def __init__(self, cols, gap=40):
        self.cols, self.gap = cols, gap  # cols: [(frac, Block)]

    def render(self, d, x, y, w):
        avail = w - self.gap * (len(self.cols) - 1)
        cx, hmax = x, 0
        for frac, b in self.cols:
            cw = avail * frac
            hmax = max(hmax, b.render(d, cx, y, cw))
            cx += cw + self.gap
        return hmax


class Boxed(Block):
    def __init__(self, inner, pad=22, outline=None, fill=None, width=2, frac=1.0, align="left"):
        self.inner, self.pad, self.outline, self.fill, self.width = inner, pad, outline, fill, width
        self.frac, self.align = frac, align

    def render(self, d, x, y, w):
        bw = w * self.frac
        bx = x if self.align == "left" else x + w - bw
        ih = self.inner.height(bw - 2 * self.pad)
        h = ih + 2 * self.pad
        if self.fill or self.outline:
            d.rectangle(
                [bx, y, bx + bw, y + h],
                fill=self.fill,
                outline=self.outline,
                width=self.width if self.outline else 0,
            )
        self.inner.render(d, bx + self.pad, y + self.pad, bw - 2 * self.pad)
        return h


class KV(Block):
    """Label/value rows. label_w is a fraction of width; value wraps."""

    def __init__(self, rows, lfont, vfont, label_w=0.42, lfill=MUTED, vfill=INK, gap=6):
        self.rows, self.lfont, self.vfont, self.label_w = rows, lfont, vfont, label_w
        self.lfill, self.vfill, self.gap = lfill, vfill, gap

    def render(self, d, x, y, w):
        h = 0
        lw = w * self.label_w
        for label, value in self.rows:
            ll = wrap(label, self.lfont, lw - 10)
            vl = wrap(value, self.vfont, w - lw)
            for i, s in enumerate(ll):
                d.text((x, y + h + i * lh(self.vfont)), s, font=self.lfont, fill=self.lfill)
            for i, s in enumerate(vl):
                d.text((x + lw, y + h + i * lh(self.vfont)), s, font=self.vfont, fill=self.vfill)
            h += max(len(ll), len(vl)) * lh(self.vfont) + self.gap
        return h


def draw_leaders(d, x_a, x_b, y, font):
    dot = font.getlength(".")
    step = max(dot * 2.4, 18)
    xx = x_a
    while xx + dot < x_b:
        d.text((xx, y), ".", font=font, fill=(150, 152, 160))
        xx += step


class Table(Block):
    def __init__(
        self,
        headers,
        rows,
        fracs,
        aligns,
        font,
        hfont,
        header_fill=None,
        header_fg=INK,
        zebra=None,
        grid=False,
        rule_color=LIGHT,
        padx=14,
        pady=10,
        show_header=True,
        leaders=False,
    ):
        self.headers, self.rows, self.fracs, self.aligns = headers, rows, fracs, aligns
        self.font, self.hfont, self.header_fill, self.header_fg = font, hfont, header_fill, header_fg
        self.zebra, self.grid, self.rule_color, self.padx, self.pady = zebra, grid, rule_color, padx, pady
        self.show_header, self.leaders = show_header, leaders

    def _row(self, d, x, y, w, cells, font, fg, fill):
        xs = [x]
        for f in self.fracs:
            xs.append(xs[-1] + w * f)
        wrapped = [wrap(c, font, xs[i + 1] - xs[i] - 2 * self.padx) for i, c in enumerate(cells)]
        n = max(len(c) for c in wrapped)
        h = n * lh(font) + 2 * self.pady
        if fill:
            d.rectangle([x, y, x + w, y + h], fill=fill)
        for i, lines in enumerate(wrapped):
            # with leaders, right-hand values sit on the last line of the (possibly wrapped) description
            off = (len(wrapped[0]) - 1) if (self.leaders and i > 0) else 0
            for j, s in enumerate(lines):
                cw = xs[i + 1] - xs[i]
                tw = font.getlength(s)
                tx = xs[i] + self.padx if self.aligns[i] == "left" else xs[i] + cw - self.padx - tw
                d.text((tx, y + self.pady + (j + off) * lh(font)), s, font=font, fill=fg)
        if self.leaders and len(cells) == 2 and cells[1]:
            ly = y + self.pady + (len(wrapped[0]) - 1) * lh(font)
            x_a = xs[0] + self.padx + font.getlength(wrapped[0][-1]) + 10
            x_b = xs[2] - self.padx - font.getlength(wrapped[1][0]) - 10
            draw_leaders(d, x_a, x_b, ly, font)
        if self.grid:
            d.rectangle([x, y, x + w, y + h], outline=self.rule_color, width=2)
            for xx in xs[1:-1]:
                d.line([xx, y, xx, y + h], fill=self.rule_color, width=2)
        else:
            d.line([x, y + h, x + w, y + h], fill=self.rule_color, width=2)
        return h

    def render(self, d, x, y, w):
        h = 0
        if self.show_header:
            h += self._row(d, x, y, w, self.headers, self.hfont, self.header_fg, self.header_fill)
        for i, r in enumerate(self.rows):
            fill = self.zebra if (self.zebra and i % 2 == 1) else None
            h += self._row(d, x, y + h, w, r, self.font, INK, fill)
        return h


class Totals(Block):
    """rows: (label, value, style) style in {'', 'bold', 'big'}; drawn in right-hand area."""

    def __init__(
        self,
        rows,
        font,
        bfont,
        bigfont,
        frac=0.55,
        big_fill=None,
        rule_color=LIGHT,
        align="right",
        leaders=False,
    ):
        self.rows, self.font, self.bfont, self.bigfont, self.leaders = rows, font, bfont, bigfont, leaders
        self.frac, self.big_fill, self.rule_color, self.align = frac, big_fill, rule_color, align

    def render(self, d, x, y, w):
        aw = w * self.frac
        ax = x + w - aw if self.align == "right" else x
        h = 0
        for label, value, style in self.rows:
            f = {"": self.font, "bold": self.bfont, "big": self.bigfont}[style]
            labels = wrap(label, f, aw - 36 - f.getlength(value))
            rh = max(lh(f, 1.45), len(labels) * lh(f) + 10)
            if style == "big":
                h += 6
                if self.big_fill:
                    d.rectangle([ax, y + h - 4, ax + aw, y + h + rh], fill=self.big_fill)
                else:
                    d.line([ax, y + h - 4, ax + aw, y + h - 4], fill=INK, width=3)
            for j, s in enumerate(labels):
                d.text((ax + 12, y + h + 4 + j * lh(f)), s, font=f, fill=INK)
            vy = y + h + 4 + ((len(labels) - 1) * lh(f) if self.leaders else 0)
            d.text((ax + aw - 12 - f.getlength(value), vy), value, font=f, fill=INK)
            if self.leaders:
                draw_leaders(
                    d, ax + 12 + f.getlength(labels[-1]) + 10, ax + aw - 12 - f.getlength(value) - 10, vy, f
                )
            h += rh + 2
        return h


class FormRow(Block):
    """Row of bordered form cells: [(frac, label, value)]."""

    def __init__(self, cells, lfont, vfont, color=INK, label_color=MUTED):
        self.cells, self.lfont, self.vfont, self.color, self.label_color = (
            cells,
            lfont,
            vfont,
            color,
            label_color,
        )

    def render(self, d, x, y, w):
        pad = 14
        hs = []
        for frac, label, value in self.cells:
            cw = w * frac
            n = len(wrap(value, self.vfont, cw - 2 * pad))
            hs.append(pad + lh(self.lfont) + n * lh(self.vfont) + pad)
        h = max(hs)
        cx = x
        for frac, label, value in self.cells:
            cw = w * frac
            d.rectangle([cx, y, cx + cw, y + h], outline=self.color, width=2)
            d.text((cx + pad, y + pad), label, font=self.lfont, fill=self.label_color)
            for j, s in enumerate(wrap(value, self.vfont, cw - 2 * pad)):
                d.text(
                    (cx + pad, y + pad + lh(self.lfont) + j * lh(self.vfont)), s, font=self.vfont, fill=INK
                )
            cx += cw
        return h


@dataclass
class PageSpec:
    w: int
    h: int | None  # None = auto height (receipt)
    x0: int
    x1: int
    y0: int
    bottom: int
    blocks: list
    bg: tuple = (255, 255, 255)
    decorate: Callable | None = None  # (draw, w, h) -> None
    footer: list = field(default_factory=list)  # [(text, font, align, color)]


class Overflow(Exception):
    pass


def render_page(spec: PageSpec) -> Image.Image:
    width = spec.x1 - spec.x0
    stack = Stack(spec.blocks)
    content_h = stack.height(width)
    foot_h = sum(lh(f) for _, f, _, _ in spec.footer) + (20 if spec.footer else 0)
    h = spec.h if spec.h else spec.y0 + content_h + foot_h + spec.bottom
    if spec.y0 + content_h > h - spec.bottom - foot_h:
        raise Overflow
    img = Image.new("RGB", (spec.w, h), spec.bg)
    d = ImageDraw.Draw(img)
    if spec.decorate:
        spec.decorate(d, spec.w, h)
    stack.render(d, spec.x0, spec.y0, width)
    fy = h - spec.bottom - foot_h + 20
    for text, f, align, color in spec.footer:
        Text(text, f, color, align).render(d, spec.x0, fy, width)
        fy += lh(f)
    return img


# --------------------------------------------------------------------------- document model
def rand_date(rng, lo: date, hi: date) -> date:
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def split_cents(rng, total: int, k: int, cents: bool) -> list[int]:
    weights = [rng.uniform(0.5, 3.0) for _ in range(k)]
    s = sum(weights)
    unit = 1 if cents else 100
    parts = [max(unit * 50, int(total * wt / s) // unit * unit) for wt in weights[:-1]]
    parts.append(total - sum(parts))
    if parts[-1] <= unit * 50:
        return split_cents(rng, total, k - 1, cents) if k > 2 else [total]
    return parts


def pick_contractor(rng, wt):
    if wt == "deck":
        return "dune" if rng.random() < 0.65 else "harbor"
    return {"roof": "inlet", "hvac": "bayside", "plumbing": "bayside", "electrical": "tidewater"}.get(
        wt, "harbor"
    )


def build_doc(
    rng: random.Random, doc_id: str, layout: str, trap: str, wt: str, photo: bool, pdf: bool
) -> dict:
    ckey = pick_contractor(rng, wt)
    c = CONTRACTORS[ckey]
    headline_t, items_t = rng.choice(JOBS[wt])
    headline = headline_t.format(
        a=rng.choice([12, 14, 16, 18, 20]),
        b=rng.choice([14, 16, 20, 24]),
        n=rng.choice([6, 8, 9, 11, 12, 14]) if wt == "windows" else rng.choice([18, 22, 26, 31, 34]),
    )

    first, last = rng.choice(FIRST), rng.choice(LAST)
    if rng.random() < 0.15:
        customer = f"{first} and {rng.choice([f for f in FIRST if f != first])} {last}"
    else:
        customer = f"{first} {last}"
    job_line = f"{rng.randint(1, 300)} {rng.choice(STREETS)}"

    completion = rand_date(rng, date(2024, 1, 1), date(2026, 10, 31))
    invoice = completion
    if trap == "invoice_date":
        invoice = min(completion + timedelta(days=rng.randint(6, 70)), date(2026, 11, 20))
        if invoice == completion:
            invoice = completion + timedelta(days=6)
    start = completion - timedelta(days=rng.randint(2, 45)) if rng.random() < 0.4 else None

    cents = rng.random() < 0.45
    lo, hi = TOTAL_RANGE[wt]
    if trap in ("sales_tax", "change_order"):  # tax / change order add up to 15%; keep total <= $140,000
        hi = min(hi, 140_000 / 1.16)
    target = int(math.exp(rng.uniform(math.log(lo), math.log(hi))) * 100)
    if not cents:
        target = target // 100 * 100

    n_items = rng.randint(6, min(8, len(items_t) + 2)) if pdf else rng.randint(3, min(6, len(items_t)))
    descs = rng.sample(items_t, min(n_items, len(items_t)))
    long_item = None
    if trap == "other_address":
        pos = rng.randint(0, len(descs))
        long_item = rng.choice(LONG_ADDR_ITEMS).format(n=rng.randint(100, 4800))
        descs.insert(pos, long_item)
    if pdf:
        while len(descs) < 6:  # 2-page docs need enough rows to spill onto page 2
            extra = rng.choice(
                ["Project supervision", "Final cleanup", "Site protection", "Material delivery"]
            )
            if extra not in descs:
                descs.append(extra)

    total_kind = {}
    if trap == "sales_tax":
        # target is the subtotal; total = subtotal + 6.625% tax
        subtotal = target
        tax = int(math.floor(subtotal * 0.06625 + 0.5))
        total = subtotal + tax
        total_kind = dict(subtotal=subtotal, tax=tax)
        base = subtotal
    elif trap == "change_order":
        base = target
        co = int(base * rng.uniform(0.04, 0.15))
        co = co if cents else co // 100 * 100
        total = base + co
        total_kind = dict(base=base, co=co, co_desc=CHANGE_ORDERS[wt], co_in_table=rng.random() < 0.5)
    else:
        base = total = target
    amounts = split_cents(rng, base, len(descs), cents)
    descs = descs[: len(amounts)] if len(amounts) < len(descs) else descs
    if long_item is not None and long_item not in descs:
        descs[-1] = long_item
    items = list(zip(descs, amounts))
    assert len(descs) == len(amounts) and sum(amounts) == base and min(amounts) > 0, doc_id

    if trap == "deposit":
        frac = rng.choice([0.25, 0.3, 1 / 3, 0.4, 0.5])
        dep = int(total * frac) // 10000 * 10000 or 50000
        total_kind = dict(
            deposit=dep,
            balance=total - dep,
            deposit_date=start or completion - timedelta(days=rng.randint(10, 40)),
        )

    billing = None
    if trap == "billing_address":
        if rng.random() < 0.25:
            billing = (f"PO Box {rng.randint(100, 2999)}", JOB_TOWN)
        else:
            billing = (
                f"{rng.randint(2, 2400)} {rng.choice(BILL_STREETS)}"
                + (f", Apt {rng.randint(1, 9)}{rng.choice('ABC')}" if rng.random() < 0.4 else ""),
                rng.choice(BILL_TOWNS),
            )

    warranty = None
    if trap != "no_warranty":
        warranty = rng.choice(
            {
                "roof": [2, 5, 10],
                "addition": [1, 2, 5],
                "kitchen": [1, 2, 3],
                "bathroom": [1, 2, 3],
                "deck": [2, 3, 5],
            }.get(wt, [1, 2, 3])
        )

    style = dict(
        date_style=rng.choice(["long", "abbr", "iso", "dmy", "us"]),
        always_cents=rng.random() < 0.7,
        lic_label=rng.choice(
            ["NJ HIC Reg. #", "HIC Reg #", "License #", "NJ HIC #", "NJ Home Improvement Contractor Reg. No."]
        ),
        comp_label=rng.choice(["Date completed", "Completion date", "Work completed", "Job completed on"]),
        inv_label=rng.choice(["Invoice date", "Date issued", "Invoice date"]),
        total_label=rng.choice(["Total", "Invoice total", "Contract total", "Total amount"]),
        cust_label=rng.choice(["Customer / Job site", "Owner / Job location", "Customer & project address"]),
        cust_email=(
            f"{first.lower()}.{last.lower().replace(chr(39), '')}@mail.example"
            if rng.random() < 0.3
            else None
        ),
        cust_phone=(f"732-555-01{rng.randint(70, 99)}" if rng.random() < 0.3 else None),
        body_family=("courier" if layout == "receipt" and rng.random() < 0.6 else rng.choice(list(FAMILIES))),
        head_family=rng.choice(["lato", "ptserif", "plex", "courier"]),
        printed=(
            invoice + timedelta(days=rng.randint(1, 20))
            if trap == "invoice_date" and rng.random() < 0.5
            else None
        ),
        warranty_style=rng.randint(0, 3),
        band_right=rng.random() < 0.5,
        table_grid=rng.random() < 0.4,
        split=None,
    )
    if style["body_family"] == "courier" and style["head_family"] == "courier" and layout != "receipt":
        style["head_family"] = "lato"
    if style["printed"] and style["printed"] > date(2026, 11, 25):
        style["printed"] = date(2026, 11, 25)
    if pdf:
        style["split"] = max(2, len(items) - rng.randint(2, 3))

    inv_no = f"{c['prefix']}-{invoice.year}-{rng.randint(1, 9999):04d}"

    return dict(
        id=doc_id,
        layout=layout,
        trap=trap,
        photo=photo,
        pdf=pdf,
        wt=wt,
        ckey=ckey,
        c=c,
        headline=headline,
        customer=customer,
        job_line=job_line,
        billing=billing,
        completion=completion,
        invoice=invoice,
        start=start,
        items=items,
        total=total,
        totals=total_kind,
        warranty=warranty,
        inv_no=inv_no,
        style=style,
        expected=dict(
            contractor_name=c["name"],
            contractor_license=c["lic"],
            contractor_email=c["email"],
            customer_name=customer,
            job_address=job_line,
            work_type=wt,
            completion_date=completion.isoformat(),
            total_amount=round(total / 100, 2),
            warranty_years=warranty,
        ),
    )


# --------------------------------------------------------------------------- shared text pieces
def D(doc, d):
    return fmt_date(d, doc["style"]["date_style"])


def M(doc, cents):
    return fmt_money(cents, doc["style"]["always_cents"] or cents % 100 != 0)


def lic_text(doc):
    return f"{doc['style']['lic_label']} {doc['c']['lic']}"


def meta_rows(doc):
    s = doc["style"]
    rows = [("Invoice #", doc["inv_no"]), (s["inv_label"], D(doc, doc["invoice"]))]
    if doc["start"]:
        rows.append(("Work started", D(doc, doc["start"])))
    rows.append((s["comp_label"], D(doc, doc["completion"])))
    return rows


def customer_blocks(doc):
    """Returns list of (title, lines)."""
    s = doc["style"]
    extra = [x for x in (s["cust_phone"], s["cust_email"]) if x]
    if doc["billing"]:
        return [
            ("Bill to", [doc["customer"], *doc["billing"], *extra]),
            ("Job site", [doc["job_line"], JOB_TOWN]),
        ]
    return [(s["cust_label"], [doc["customer"], doc["job_line"], JOB_TOWN, *extra])]


def item_rows(doc):
    rows = [(desc, M(doc, amt)) for desc, amt in doc["items"]]
    t = doc["totals"]
    if doc["trap"] == "change_order" and t["co_in_table"]:
        rows.append((f"Change Order #1: {t['co_desc']}", M(doc, t["co"])))
    return rows


def totals_rows(doc):
    t, s = doc["totals"], doc["style"]
    trap = doc["trap"]
    if trap == "sales_tax":
        return [
            ("Subtotal", M(doc, t["subtotal"]), ""),
            ("NJ sales tax (6.625%)", M(doc, t["tax"]), ""),
            ("Total (incl. tax)", M(doc, doc["total"]), "big"),
        ]
    if trap == "change_order":
        if t["co_in_table"]:
            return [("Total incl. change order", M(doc, doc["total"]), "big")]
        return [
            ("Original contract", M(doc, t["base"]), ""),
            (f"Change Order #1 ({t['co_desc']})", M(doc, t["co"]), ""),
            ("Revised contract total", M(doc, doc["total"]), "big"),
        ]
    if trap == "deposit":
        return [
            ("Contract total", M(doc, doc["total"]), "bold"),
            (f"Deposit received {D(doc, t['deposit_date'])}", M(doc, -t["deposit"]), ""),
            ("Balance due", M(doc, t["balance"]), "big"),
        ]
    return [(s["total_label"], M(doc, doc["total"]), "big")]


def warranty_text(doc):
    n = doc["warranty"]
    if n is None:
        return None
    yrs = "year" if n == 1 else "years"
    name = doc["c"]["name"]
    return [
        f"Workmanship warranty: {n} {yrs} from the date of completion.",
        f"{name} warrants all workmanship for {NUM_WORDS[n]} ({n}) {yrs}.",
        f"This work is covered by our {n}-year workmanship warranty.",
        f"Warranty: {n} {yrs} on workmanship (labor).",
    ][doc["style"]["warranty_style"]]


def terms_text(doc):
    payee = doc["c"]["name"].rstrip(".") + "."
    base = f"Payment due within 15 days of invoice date. Please make checks payable to {payee}"
    if doc["trap"] == "deposit":
        base = f"Balance due upon receipt. Please make checks payable to {payee}"
    return base


def footer_lines(doc, F, page, pages):
    out = []
    if pages > 1:
        out.append(
            (
                f"Page {page} of {pages}" + (" - continued on next page" if page < pages else ""),
                F(24),
                "center",
                MUTED,
            )
        )
    if doc["style"]["printed"] and page == pages:
        out.append((f"Printed {D(doc, doc['style']['printed'])}", F(22), "right", MUTED))
    return out


def split_items(doc):
    rows = item_rows(doc)
    k = doc["style"]["split"]
    if k is None:
        return [rows]
    return [rows[:k], rows[k:]]


def cont_header(doc, F):
    return Stack(
        [
            Text(
                f"{doc['c']['name']}  |  Invoice {doc['inv_no']}  |  {doc['customer']} (continued)",
                F(26, True),
                MUTED,
            ),
            Rule(LIGHT, 2, 14),
        ]
    )


def items_table(doc, F, rows, color, cont=False, light=False):
    s = doc["style"]
    tint = mix(color, (255, 255, 255), 0.9)
    return Table(
        ["Description" + (" (continued)" if cont else ""), "Amount"],
        rows,
        [0.74, 0.26],
        ["left", "right"],
        F(30),
        F(30, True),
        header_fill=(tint if light else color),
        header_fg=(INK if light else (255, 255, 255)),
        zebra=(None if s["table_grid"] else (248, 248, 250)),
        grid=s["table_grid"],
    )


def notes_blocks(doc, F):
    out = []
    w = warranty_text(doc)
    if w:
        out += [Text("Warranty", F(30, True)), Text(w, F(29)), Spacer(14)]
    out += [Text(terms_text(doc), F(27), MUTED)]
    return out


# --------------------------------------------------------------------------- layouts
def L_classic(doc, F):
    c = doc["c"]
    color = c["color"]
    head = [
        Text(c["name"], F(66, True, True), color, "center"),
        Text(c["tagline"], F(28, False, True), MUTED, "center"),
        Spacer(8),
        Text(f"{c['office']}, {c['town']}   ·   {c['phone']}   ·   {c['email']}", F(27), INK, "center"),
        Text(lic_text(doc), F(27), INK, "center"),
        Rule(color, 3, 16, double=True),
        Spacer(10),
        Columns(
            [
                (0.5, Text("INVOICE", F(60, True, True), color)),
                (0.5, KV(meta_rows(doc), F(28, True), F(28), 0.52)),
            ]
        ),
        Spacer(26),
        Columns(
            [
                (
                    0.5 if len(customer_blocks(doc)) > 1 else 0.7,
                    Stack([Text(t, F(28, True), color), *[Text(l, F(30)) for l in lines]]),
                )
                for t, lines in customer_blocks(doc)
            ]
            + ([(0.3, Spacer(1))] if len(customer_blocks(doc)) == 1 else [])
        ),
        Spacer(26),
        Text("Description of work", F(28, True), color),
        Text(doc["headline"], F(32)),
        Spacer(22),
    ]
    parts = split_items(doc)
    tail = [
        Spacer(16),
        Totals(totals_rows(doc), F(30), F(31, True), F(38, True)),
        Spacer(30),
        *notes_blocks(doc, F),
    ]
    if len(parts) == 1:
        return [
            PageSpec(
                PAGE_W,
                PAGE_H,
                130,
                PAGE_W - 130,
                110,
                110,
                head + [items_table(doc, F, parts[0], color)] + tail,
                footer=footer_lines(doc, F, 1, 1),
            )
        ]
    return [
        PageSpec(
            PAGE_W,
            PAGE_H,
            130,
            PAGE_W - 130,
            110,
            110,
            head + [items_table(doc, F, parts[0], color)],
            footer=footer_lines(doc, F, 1, 2),
        ),
        PageSpec(
            PAGE_W,
            PAGE_H,
            130,
            PAGE_W - 130,
            110,
            110,
            [cont_header(doc, F), Spacer(10), items_table(doc, F, parts[1], color, cont=True)] + tail,
            footer=footer_lines(doc, F, 2, 2),
        ),
    ]


def L_band(doc, F):
    c = doc["c"]
    color = c["color"]
    band_h = 250

    def deco(d, w, h):
        d.rectangle([0, 0, w, band_h], fill=color)
        d.rectangle([0, h - 40, w, h], fill=color)
        nf, tf = F(64, True, True), F(56, True, True)
        d.text((120, 70), c["name"], font=nf, fill=(255, 255, 255))
        d.text(
            (120, 70 + lh(nf) + 4),
            c["tagline"],
            font=F(28, False, True),
            fill=mix(color, (255, 255, 255), 0.75),
        )
        if doc["style"]["band_right"]:
            d.text((w - 120 - tf.getlength("INVOICE"), 80), "INVOICE", font=tf, fill=(255, 255, 255))

    def deco2(d, w, h):
        d.rectangle([0, 0, w, 60], fill=color)
        d.rectangle([0, h - 40, w, h], fill=color)

    tint = mix(color, (255, 255, 255), 0.9)
    custs = customer_blocks(doc)
    cust_stack = Stack(
        [
            Stack([Text(t, F(28, True), color), *[Text(l, F(30)) for l in lines], Spacer(14)])
            for t, lines in custs
        ]
    )
    head = [
        Text(f"{c['office']}, {c['town']}  |  {c['phone']}  |  {c['email']}", F(26), INK),
        Text(lic_text(doc), F(26, True), INK),
        Spacer(24),
        Columns([(0.52, cust_stack), (0.48, Boxed(KV(meta_rows(doc), F(27, True), F(27), 0.5), fill=tint))]),
        Spacer(20),
        Boxed(
            Stack([Text("Project", F(26, True), color), Text(doc["headline"], F(31))]),
            pad=16,
            outline=tint,
            width=3,
        ),
        Spacer(24),
    ]
    tail = [
        Spacer(14),
        Totals(totals_rows(doc), F(30), F(31, True), F(37, True), big_fill=tint),
        Spacer(30),
        *notes_blocks(doc, F),
    ]
    parts = split_items(doc)
    if len(parts) == 1:
        return [
            PageSpec(
                PAGE_W,
                PAGE_H,
                120,
                PAGE_W - 120,
                band_h + 40,
                90,
                head + [items_table(doc, F, parts[0], color)] + tail,
                decorate=deco,
                footer=footer_lines(doc, F, 1, 1),
            )
        ]
    return [
        PageSpec(
            PAGE_W,
            PAGE_H,
            120,
            PAGE_W - 120,
            band_h + 40,
            90,
            head + [items_table(doc, F, parts[0], color)],
            decorate=deco,
            footer=footer_lines(doc, F, 1, 2),
        ),
        PageSpec(
            PAGE_W,
            PAGE_H,
            120,
            PAGE_W - 120,
            110,
            90,
            [cont_header(doc, F), items_table(doc, F, parts[1], color, cont=True)] + tail,
            decorate=deco2,
            footer=footer_lines(doc, F, 2, 2),
        ),
    ]


def L_sidebar(doc, F):
    c = doc["c"]
    color = c["color"]
    sb_w = 500
    tint = mix(color, (255, 255, 255), 0.88)

    def deco(d, w, h):
        d.rectangle([0, 0, sb_w, h], fill=tint)
        d.rectangle([sb_w - 8, 0, sb_w, h], fill=color)

    def sidebar_img(img_draw, h, first):
        x, y, w = 60, 110, sb_w - 120
        blocks = [
            Text(c["name"], F(50, True, True), color),
            Spacer(16),
            Text(c["office"], F(26)),
            Text(c["town"], F(26)),
            Text(c["phone"], F(26)),
            Text(c["email"], F(26)),
            Spacer(16),
            Text(lic_text(doc), F(26, True)),
        ]
        if first:
            blocks += [Spacer(50), Rule(color, 3, 8)]
            for label, value in meta_rows(doc):
                blocks += [Text(label, F(24, True), MUTED), Text(value, F(28)), Spacer(10)]
        Stack(blocks).render(img_draw, x, y, w)

    custs = customer_blocks(doc)
    head = [
        Text("Invoice", F(72, True, True), color),
        Spacer(20),
        *[
            Stack([Text(t.upper(), F(25, True), MUTED), *[Text(l, F(31)) for l in lines], Spacer(18)])
            for t, lines in custs
        ],
        Spacer(10),
        Text("Scope of work", F(25, True), MUTED),
        Text(doc["headline"], F(31)),
        Spacer(24),
    ]
    tail = [
        Spacer(14),
        Totals(totals_rows(doc), F(29), F(30, True), F(35, True), frac=0.8),
        Spacer(28),
        *notes_blocks(doc, F),
    ]
    x0, x1 = sb_w + 70, PAGE_W - 90

    def mk_deco(first):
        def f(d, w, h):
            deco(d, w, h)
            sidebar_img(d, h, first)

        return f

    parts = split_items(doc)
    if len(parts) == 1:
        return [
            PageSpec(
                PAGE_W,
                PAGE_H,
                x0,
                x1,
                110,
                100,
                head + [items_table(doc, F, parts[0], color, light=True)] + tail,
                decorate=mk_deco(True),
                footer=footer_lines(doc, F, 1, 1),
            )
        ]
    return [
        PageSpec(
            PAGE_W,
            PAGE_H,
            x0,
            x1,
            110,
            100,
            head + [items_table(doc, F, parts[0], color, light=True)],
            decorate=mk_deco(True),
            footer=footer_lines(doc, F, 1, 2),
        ),
        PageSpec(
            PAGE_W,
            PAGE_H,
            x0,
            x1,
            110,
            100,
            [
                Text(f"Invoice {doc['inv_no']} (continued)", F(30, True), MUTED),
                Spacer(16),
                items_table(doc, F, parts[1], color, cont=True, light=True),
            ]
            + tail,
            decorate=mk_deco(False),
            footer=footer_lines(doc, F, 2, 2),
        ),
    ]


def L_receipt(doc, F):
    c = doc["c"]
    W = 1150
    dash = lambda: Rule(MUTED, 3, 16, dashed=True)
    rows_kv = meta_rows(doc)
    blocks = [
        Text(c["name"], F(54, True, True), INK, "center"),
        Text(c["office"], F(28), INK, "center"),
        Text(c["town"], F(28), INK, "center"),
        Text(f"Tel {c['phone']}", F(28), INK, "center"),
        Text(c["email"], F(28), INK, "center"),
        Text(lic_text(doc), F(28), INK, "center"),
        dash(),
        Text("INVOICE", F(40, True), INK, "center"),
        Spacer(8),
        KV(rows_kv, F(28), F(28), 0.5, lfill=INK),
        dash(),
    ]
    for t, lines in customer_blocks(doc):
        blocks += [Text(t.upper() + ":", F(28, True)), *[Text(l, F(30)) for l in lines], Spacer(10)]
    blocks += [
        dash(),
        Text("Work: " + doc["headline"], F(30)),
        dash(),
        Table(
            ["", ""],
            item_rows(doc),
            [0.7, 0.3],
            ["left", "right"],
            F(29),
            F(29),
            show_header=False,
            rule_color=(255, 255, 255),
            padx=0,
            pady=6,
            leaders=True,
        ),
        dash(),
        Totals(
            totals_rows(doc),
            F(30),
            F(30, True),
            F(38, True),
            frac=1.0,
            rule_color=(255, 255, 255),
            leaders=True,
        ),
        dash(),
    ]
    w = warranty_text(doc)
    if w:
        blocks += [Text(w, F(28)), Spacer(10)]
    blocks += [
        Text(terms_text(doc), F(26), MUTED),
        Spacer(20),
        Text("*** THANK YOU ***", F(30, True), INK, "center"),
    ]
    return [
        PageSpec(W, None, 70, W - 70, 80, 80, blocks, bg=(252, 251, 246), footer=footer_lines(doc, F, 1, 1))
    ]


def L_letter(doc, F):
    c = doc["c"]
    color = c["color"]
    t, trap = doc["totals"], doc["trap"]
    head = [
        Columns(
            [
                (
                    0.6,
                    Stack(
                        [
                            Text(c["name"], F(60, True, True), color),
                            Text(c["tagline"], F(27, False, True), MUTED),
                        ]
                    ),
                ),
                (
                    0.4,
                    Stack(
                        [
                            Text(c["office"], F(25), INK, "right"),
                            Text(c["town"], F(25), INK, "right"),
                            Text(c["phone"], F(25), INK, "right"),
                            Text(c["email"], F(25), INK, "right"),
                            Text(lic_text(doc), F(25), INK, "right"),
                        ]
                    ),
                ),
            ]
        ),
        Rule(color, 4, 18),
        Spacer(16),
        Text(D(doc, doc["invoice"]), F(31)),
        Text(f"Invoice No. {doc['inv_no']}", F(31)),
        Spacer(26),
    ]
    addr = [doc["customer"], *(doc["billing"] if doc["billing"] else [doc["job_line"], JOB_TOWN])]
    head += [Text(l, F(31)) for l in addr]
    head += [
        Spacer(26),
        Text(f"Re: Final invoice - {doc['headline']}", F(31, True)),
        Text(f"Job site: {doc['job_line']}, {JOB_TOWN}", F(31, True)),
        Spacer(22),
        Text(f"Dear {doc['customer']},", F(31)),
        Spacer(14),
    ]
    started = f"Work began on {D(doc, doc['start'])} and was" if doc["start"] else "The work was"
    p1 = (
        f"Thank you for choosing {c['name']}. This letter is our final invoice for the work at "
        f"{doc['job_line'].replace(' ', NBSP)}. {started} completed on {D(doc, doc['completion'])}. The charges are itemized below."
    )
    head += [Text(p1, F(31), k=1.42), Spacer(22)]
    tot = M(doc, doc["total"])
    if trap == "sales_tax":
        p2 = f"The contract price of {M(doc, t['subtotal'])} plus New Jersey sales tax of {M(doc, t['tax'])} brings the total contract price to {tot}."
    elif trap == "change_order":
        p2 = (
            f"The original contract price was {M(doc, t['base'])}. Change Order #1 ({t['co_desc']}) added "
            f"{M(doc, t['co'])}, for a revised contract total of {tot}."
        )
    elif trap == "deposit":
        p2 = (
            f"The total contract price is {tot}. We received your deposit of {M(doc, t['deposit'])} on "
            f"{D(doc, t['deposit_date'])}, leaving a balance due of {M(doc, t['balance'])}."
        )
    else:
        p2 = f"The total contract price for this work is {tot}."
    w = warranty_text(doc)
    tail = [
        Spacer(14),
        Totals(totals_rows(doc), F(30), F(30, True), F(35, True), frac=0.6),
        Spacer(22),
        Text(p2, F(31), k=1.42),
        Spacer(18),
    ]
    if w:
        tail += [Text(w, F(31), k=1.42), Spacer(18)]
    tail += [
        Text(terms_text(doc), F(29), MUTED, k=1.42),
        Spacer(30),
        Text("Sincerely,", F(31)),
        Spacer(36),
        Text(c["signer"][0], F(31, True)),
        Text(f"{c['signer'][1]}, {c['name']}", F(29)),
    ]
    parts = split_items(doc)
    if len(parts) == 1:
        return [
            PageSpec(
                PAGE_W,
                PAGE_H,
                150,
                PAGE_W - 150,
                110,
                100,
                head + [items_table(doc, F, parts[0], color, light=True)] + tail,
                footer=footer_lines(doc, F, 1, 1),
            )
        ]
    return [
        PageSpec(
            PAGE_W,
            PAGE_H,
            150,
            PAGE_W - 150,
            110,
            100,
            head + [items_table(doc, F, parts[0], color, light=True)],
            footer=footer_lines(doc, F, 1, 2),
        ),
        PageSpec(
            PAGE_W,
            PAGE_H,
            150,
            PAGE_W - 150,
            110,
            100,
            [cont_header(doc, F), items_table(doc, F, parts[1], color, cont=True, light=True)] + tail,
            footer=footer_lines(doc, F, 2, 2),
        ),
    ]


def L_form(doc, F):
    c = doc["c"]
    color = c["color"]
    lf, vf = F(22, True), F(30)
    custs = customer_blocks(doc)
    s = doc["style"]
    head = [
        Columns(
            [
                (
                    0.65,
                    Stack(
                        [
                            Text(c["name"], F(58, True, True), INK),
                            Text(f"{c['office']}, {c['town']}", F(26)),
                            Text(f"{c['phone']}  ·  {c['email']}", F(26)),
                        ]
                    ),
                ),
                (
                    0.35,
                    Boxed(
                        Stack(
                            [
                                Text("INVOICE", F(48, True, True), color, "center"),
                                Text(doc["inv_no"], F(30), INK, "center"),
                            ]
                        ),
                        outline=color,
                        width=4,
                    ),
                ),
            ]
        ),
        Spacer(28),
        FormRow([(0.5, "CONTRACTOR", c["name"]), (0.5, "NJ HIC REGISTRATION", lic_text(doc))], lf, vf, color),
    ]
    if len(custs) == 2:
        head.append(
            FormRow(
                [(0.5, "BILL TO", ", ".join(custs[0][1])), (0.5, "JOB SITE ADDRESS", ", ".join(custs[1][1]))],
                lf,
                vf,
                color,
            )
        )
    else:
        lines = custs[0][1]
        head.append(
            FormRow(
                [(0.4, "CUSTOMER", lines[0]), (0.6, "JOB SITE ADDRESS", f"{lines[1]}, {lines[2]}")],
                lf,
                vf,
                color,
            )
        )
        if len(lines) > 3:
            head.append(FormRow([(1.0, "CUSTOMER CONTACT", "  ·  ".join(lines[3:]))], lf, vf, color))
    date_cells = [(0.34, s["inv_label"].upper(), D(doc, doc["invoice"]))]
    if doc["start"]:
        date_cells.append((0.33, "WORK STARTED", D(doc, doc["start"])))
    date_cells.append((0.33 if doc["start"] else 0.66, s["comp_label"].upper(), D(doc, doc["completion"])))
    head += [
        FormRow(date_cells, lf, vf, color),
        FormRow([(1.0, "DESCRIPTION OF WORK", doc["headline"])], lf, vf, color),
        Spacer(26),
    ]
    tbl = lambda rows, cont=False: Table(
        ["Item / description" + (" (continued)" if cont else ""), "Amount"],
        rows,
        [0.74, 0.26],
        ["left", "right"],
        F(30),
        F(30, True),
        header_fill=mix(color, (255, 255, 255), 0.85),
        grid=True,
        rule_color=color,
    )
    w = warranty_text(doc)
    tail = [
        Spacer(16),
        Boxed(
            Totals(totals_rows(doc), F(30), F(31, True), F(37, True), frac=1.0),
            outline=color,
            width=3,
            frac=0.55,
            align="right",
            pad=10,
        ),
        Spacer(28),
        FormRow([(1.0, "WARRANTY / TERMS", (w + "  " if w else "") + terms_text(doc))], lf, F(28), color),
    ]
    parts = split_items(doc)
    if len(parts) == 1:
        return [
            PageSpec(
                PAGE_W,
                PAGE_H,
                110,
                PAGE_W - 110,
                100,
                100,
                head + [tbl(parts[0])] + tail,
                footer=footer_lines(doc, F, 1, 1),
            )
        ]
    return [
        PageSpec(
            PAGE_W,
            PAGE_H,
            110,
            PAGE_W - 110,
            100,
            100,
            head + [tbl(parts[0])],
            footer=footer_lines(doc, F, 1, 2),
        ),
        PageSpec(
            PAGE_W,
            PAGE_H,
            110,
            PAGE_W - 110,
            100,
            100,
            [cont_header(doc, F), tbl(parts[1], True)] + tail,
            footer=footer_lines(doc, F, 2, 2),
        ),
    ]


LAYOUT_FN = {
    "classic": L_classic,
    "band": L_band,
    "sidebar": L_sidebar,
    "receipt": L_receipt,
    "letter": L_letter,
    "form": L_form,
}


def render_doc(doc) -> list[Image.Image]:
    s = doc["style"]
    for scale in (1.0, 0.94, 0.88, 0.82, 0.76, 0.7):
        F = Fonts(s["body_family"], s["head_family"], scale)
        try:
            return [render_page(p) for p in LAYOUT_FN[doc["layout"]](doc, F)]
        except Overflow:
            continue
    raise RuntimeError(f"{doc['id']}: content does not fit")


# --------------------------------------------------------------------------- phone-photo effect
def perspective_coeffs(dst, src):
    """Coefficients for Image.transform(PERSPECTIVE): maps output points dst -> input points src."""
    A, B = [], []
    for (x, y), (X, Y) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -X * x, -X * y])
        B.append(X)
        A.append([0, 0, 0, x, y, 1, -Y * x, -Y * y])
        B.append(Y)
    return np.linalg.solve(np.array(A, float), np.array(B, float)).tolist()


def photo_effect(img: Image.Image, seed: int) -> Image.Image:
    pr = random.Random(seed)
    nr = np.random.default_rng(seed)
    W, H = img.size
    mx, my = int(W * 0.07), int(H * 0.06)
    CW, CH = W + 2 * mx, H + 2 * my
    bgc = np.array(
        pr.choice([(122, 94, 68), (74, 76, 82), (196, 192, 184), (146, 138, 124), (92, 110, 98)]), float
    )
    bg = np.clip(bgc + nr.normal(0, 6, (CH, CW, 1)) + nr.normal(0, 3, (CH, CW, 3)), 0, 255).astype(np.uint8)
    bg_img = Image.fromarray(bg, "RGB").filter(ImageFilter.GaussianBlur(1.5))
    ang = math.radians(pr.uniform(-1.6, 1.6))
    s = pr.uniform(0.94, 0.98)
    cx, cy = CW / 2 + pr.uniform(-0.01, 0.01) * CW, CH / 2 + pr.uniform(-0.01, 0.01) * CH
    corners = []
    for px, py in [(-W / 2, -H / 2), (W / 2, -H / 2), (W / 2, H / 2), (-W / 2, H / 2)]:
        px, py = px * s, py * s
        rx = cx + px * math.cos(ang) - py * math.sin(ang) + pr.uniform(-0.008, 0.008) * W
        ry = cy + px * math.sin(ang) + py * math.cos(ang) + pr.uniform(-0.007, 0.007) * H
        corners.append((rx, ry))
    coeffs = perspective_coeffs(corners, [(0, 0), (W, 0), (W, H), (0, H)])
    warped = img.transform((CW, CH), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    mask = Image.new("L", (W, H), 255).transform((CW, CH), Image.PERSPECTIVE, coeffs, Image.BILINEAR)
    shadow = mask.filter(ImageFilter.GaussianBlur(18))
    bg_arr = np.asarray(bg_img, float) * (1 - 0.35 * np.asarray(shadow, float)[..., None] / 255)
    comp = Image.composite(warped, Image.fromarray(bg_arr.astype(np.uint8)), mask)
    arr = np.asarray(comp, float)
    yy, xx = np.mgrid[0:CH, 0:CW]
    xn, yn = xx / CW - 0.5, yy / CH - 0.5
    lx, ly = pr.uniform(-0.5, 0.5), pr.uniform(-0.5, 0.5)
    light = (
        1.02
        + pr.uniform(-0.16, 0.16) * xn
        + pr.uniform(-0.16, 0.16) * yn
        - pr.uniform(0.15, 0.35) * ((xn - lx * 0.4) ** 2 + (yn - ly * 0.4) ** 2)
    )
    light = np.clip(light, 0.74, 1.06)
    cast = np.array(pr.choice([(1.0, 0.98, 0.93), (0.97, 0.99, 1.0), (1.0, 1.0, 0.97)]))
    arr = arr * light[..., None] * cast
    arr += nr.normal(0, 2.5, arr.shape)
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    return out.filter(ImageFilter.GaussianBlur(pr.uniform(0.4, 0.9)))


# --------------------------------------------------------------------------- planning & main
def balanced(rng, options, n):
    pool = []
    while len(pool) < n:
        chunk = list(options)
        rng.shuffle(chunk)
        pool += chunk
    pool = pool[:n]
    rng.shuffle(pool)
    return pool


def plan(seed: int, n: int):
    rng = random.Random(seed)
    traps = balanced(rng, TRAPS, n)
    layouts = balanced(rng, LAYOUTS, n)
    wts = balanced(rng, WORK_TYPES, n)
    n_pdf, n_photo = round(n * 0.10), round(n * 0.30)
    pdf_idx = set(rng.sample([i for i in range(n) if layouts[i] in PDF_LAYOUTS], n_pdf))
    photo_idx = set(rng.sample([i for i in range(n) if i not in pdf_idx], n_photo))
    return [
        dict(trap=traps[i], layout=layouts[i], wt=wts[i], pdf=i in pdf_idx, photo=i in photo_idx)
        for i in range(n)
    ]


def generate(set_name: str, count: int, seed: int, out: Path, keep_pages: dict | None = None):
    out.mkdir(parents=True, exist_ok=True)
    prefix = {"development": "dev", "sealed": "sealed"}[set_name]
    truth = []
    for i, p in enumerate(plan(seed, count)):
        doc_seed = seed * 100_003 + i
        rng = random.Random(doc_seed)
        doc_id = f"{prefix}{i + 1:03d}"
        doc = build_doc(rng, doc_id, p["layout"], p["trap"], p["wt"], p["photo"], p["pdf"])
        pages = render_doc(doc)
        if p["pdf"]:
            fname = f"{doc_id}.pdf"
            pages[0].save(
                out / fname,
                "PDF",
                resolution=200,
                save_all=True,
                append_images=pages[1:],
                quality=92,
                title=doc_id,
                creationDate=FIXED_PDF_TIME,
                modDate=FIXED_PDF_TIME,
            )  # fixed for reproducibility
        elif p["photo"]:
            fname = f"{doc_id}.jpg"
            photo_effect(pages[0], doc_seed).save(
                out / fname, "JPEG", quality=random.Random(doc_seed + 7).randint(74, 88)
            )
        else:
            fname = f"{doc_id}.png"
            pages[0].save(out / fname, "PNG", optimize=False)
        if keep_pages is not None:
            keep_pages[doc_id] = pages[0]
        truth.append(
            dict(
                id=doc_id,
                file=fname,
                layout=p["layout"],
                photo=p["photo"],
                trap=p["trap"],
                expected=doc["expected"],
            )
        )
    (out / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    return truth


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", required=True, choices=["development", "sealed"])
    ap.add_argument("--count", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    truth = generate(a.set, a.count, a.seed, a.out)
    print(f"wrote {len(truth)} documents + truth.json to {a.out}")


if __name__ == "__main__":
    main()
