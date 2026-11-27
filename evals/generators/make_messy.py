#!/usr/bin/env python3
"""Make the messy real-looking invoice set: 30 images, 6 kinds x 5 (test strategy 5.6, evals 4.2).

Everything here is made up. Contractors come from the demo registry, customers are invented, every
address is on a Sampleton street and every email ends in .example.

Pipeline, as 5.6 says:
  1. Truth first: the 9 fields (and the exact printed text of each) are decided before any drawing.
  2. Draw the invoice clean from the truth with committed OFL/Apache fonts (messy_fonts/).
  3. Damage it in code with a fixed seed per image (own OpenCV crumple/fold/camera, tuned Augraphy).
  4. Readability gate (--gate): Tesseract and a hosted reader (Codex CLI) both read each image; one
     of them must find the total and >= 7 of 9 fields. Failures are softened (x0.7) and retried up to
     3 times, then regenerated with a new seed. Every attempt is logged in gate.json.

Without --gate the script rebuilds every image from the seed/strength recorded in gate.json, so a
rerun reproduces truth.json exactly (truth never depends on the gate) and the images byte-for-byte
on the same machine.

Heavy dependencies live in a throwaway uv environment, not in pyproject.toml:

  uv run --python 3.12 --with augraphy==8.2.6 --with opencv-python-headless==5.1.0.95 \\
      --with numpy==2.5.5 --with pillow==13.0.0 python evals/generators/make_messy.py [--gate]

The gate also needs `tesseract` and `pdftoppm` on PATH and, for the hosted reader, `codex`.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import random
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "evals" / "sets" / "messy"
FONT_DIR = HERE / "messy_fonts"
MASTER_SEED = 20261127
TOWN = "Sampleton, NJ 08736"
PAGE_W, PAGE_H = 1700, 2200  # US letter at 200 dpi

# ----------------------------------------------------------------------------------------------
# Fonts (all OFL or Apache-2.0, see messy_fonts/LICENSES.md)
# ----------------------------------------------------------------------------------------------
FONT_FILES = {
    "sans": "lato/Lato-Regular.ttf",
    "sans_b": "lato/Lato-Bold.ttf",
    "serif": "ptserif/PT_Serif-Web-Regular.ttf",
    "serif_b": "ptserif/PT_Serif-Web-Bold.ttf",
    "cond": "ptsans/PT_Sans-Web-Regular.ttf",
    "cond_b": "ptsans/PT_Sans-Web-Bold.ttf",
    "mono": "courierprime/CourierPrime-Regular.ttf",
    "mono_b": "courierprime/CourierPrime-Bold.ttf",
    "kalam": "kalam/Kalam-Regular.ttf",
    "kalam_b": "kalam/Kalam-Bold.ttf",
    "patrick": "patrickhand/PatrickHand-Regular.ttf",
    "gochi": "gochihand/GochiHand-Regular.ttf",
    "indie": "indieflower/IndieFlower-Regular.ttf",
    "reenie": "reeniebeanie/ReenieBeanie.ttf",
    "caveat": "caveat/Caveat-Variable.ttf",
    "marker": "permanentmarker/PermanentMarker-Regular.ttf",
    "jah": "justanotherhand/JustAnotherHand-Regular.ttf",
    "apple": "homemadeapple/HomemadeApple-Regular.ttf",
}
HAND_FIELD = ["kalam", "patrick", "gochi", "caveat", "indie", "kalam_b"]  # legible enough for numbers
HAND_NOTE = ["reenie", "jah", "caveat"]
HAND_SIG = ["apple", "reenie"]
# Relative size so different hand fonts look about the same height on the page.
HAND_SCALE = {
    "kalam": 1.0,
    "kalam_b": 1.0,
    "patrick": 1.1,
    "gochi": 1.1,
    "caveat": 1.3,
    "indie": 1.0,
    "reenie": 1.45,
    "jah": 1.6,
    "apple": 0.8,
    "marker": 0.95,
}


@cache
def font(key: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / FONT_FILES[key]), max(6, int(size)))


# ----------------------------------------------------------------------------------------------
# Truth
# ----------------------------------------------------------------------------------------------
CONTRACTORS = [
    ("Harbor & Pine Builders", "13VH01234567", "office@harborpine.example"),
    ("Inlet Roofing Co.", "13VH00077302", "estimates@inletroof.example"),
    ("Bayside Plumbing & Heating", "13VH00551920", "service@baysideph.example"),
    ("Tidewater Electric LLC", "13VH00938114", "jobs@tidewater-elec.example"),
    ("Dune Grass Decks", "13VH00410267", "build@dunegrassdecks.example"),
]
TRADES = {
    "Harbor & Pine Builders": ["addition", "kitchen", "windows", "bathroom", "other"],
    "Inlet Roofing Co.": ["roof"],
    "Bayside Plumbing & Heating": ["plumbing", "hvac", "bathroom"],
    "Tidewater Electric LLC": ["electrical"],
    "Dune Grass Decks": ["deck"],
}
# work_type -> (line items, subtotal range, warranty choices, keyword that is always printed)
WORK = {
    "deck": (
        [
            "Remove old pressure-treated deck",
            "Build 14x18 composite deck",
            "Aluminum railings + stairs",
            "Helical footings per plan",
        ],
        (9000, 26000),
        [5, 3, 10],
        "deck",
    ),
    "roof": (
        [
            "Tear off 1 layer asphalt shingles",
            "Ice & water shield at eaves",
            "Architectural shingles",
            "Ridge vent, drip edge, flashing",
        ],
        (11000, 24000),
        [10, 15, 5],
        "shingles",
    ),
    "kitchen": (
        [
            "Demo existing kitchen",
            "Shaker cabinets, quartz tops",
            "Tile backsplash",
            "Plumbing & electrical hookups",
        ],
        (28000, 72000),
        [2, 1, None],
        "kitchen",
    ),
    "bathroom": (
        [
            "Gut hall bathroom",
            "Tile shower with glass door",
            "New vanity and toilet",
            "Exhaust fan vented outside",
        ],
        (14000, 38000),
        [2, 1, None],
        "bathroom",
    ),
    "addition": (
        ["Frame 12x16 sunroom addition", "Windows, insulation, drywall", "Tie into existing roof"],
        (38000, 95000),
        [10, 5],
        "addition",
    ),
    "windows": (
        ["Replace 11 double-hung windows", "Exterior capping and caulk", "Haul away old units"],
        (8000, 21000),
        [20, 10],
        "windows",
    ),
    "hvac": (
        ["Remove old furnace and AC", "96% gas furnace + 3-ton AC", "Thermostat, start-up & test"],
        (9000, 17000),
        [10, 5],
        "furnace",
    ),
    "plumbing": (
        ["Replace 50 gal water heater", "Expansion tank and shutoff", "Gas line reconnect"],
        (1800, 4200),
        [6, 1, None],
        "water heater",
    ),
    "electrical": (
        ["Upgrade service to 200A panel", "Add 4 new circuits", "Whole-house surge protector"],
        (3500, 9500),
        [None, 1, 2],
        "panel",
    ),
    "other": (
        ["Remove storm-damaged fence", "Install 120 ft cedar fence", "Two 4 ft walk gates"],
        (4000, 9000),
        [1, None],
        "fence",
    ),
}
FIRST = [
    "Dana",
    "Marcus",
    "Priscilla",
    "Owen",
    "Lena",
    "Raj",
    "Colleen",
    "Teo",
    "Hannah",
    "Victor",
    "Nadia",
    "Gareth",
    "Imani",
    "Felix",
    "Rosalind",
    "Tomasz",
    "Beatrice",
    "Kwame",
    "Ingrid",
    "Desmond",
    "Mirela",
    "Arturo",
    "June",
    "Silas",
    "Pilar",
    "Everett",
    "Odette",
    "Callum",
    "Yesenia",
    "Bram",
]
LAST = [
    "Whitfield",
    "Ellery",
    "Vance",
    "Tabor",
    "Ostrowski",
    "Malhotra",
    "Brady",
    "Marchetti",
    "Greer",
    "Alonzo",
    "Quill",
    "Fairbanks",
    "Okafor",
    "Lindqvist",
    "Harrow",
    "Kowalczyk",
    "Pemberton",
    "Asante",
    "Dahl",
    "Ferreira",
    "Novak",
    "Castellano",
    "Rourke",
    "Abernathy",
    "Moreau",
    "Szabo",
    "Whitlock",
    "Delacroix",
    "Ybarra",
    "Hollis",
]
STREETS = [
    ("Osprey", "Lane", "Ln"),
    ("Heron", "Way", "Way"),
    ("Tidewater", "Road", "Rd"),
    ("Sandbar", "Court", "Ct"),
    ("Beacon", "Avenue", "Ave"),
    ("Lantern", "Place", "Pl"),
    ("Driftwood", "Terrace", "Ter"),
    ("Gull Rock", "Road", "Rd"),
    ("Saltmeadow", "Drive", "Dr"),
    ("Bayberry", "Circle", "Cir"),
]

KINDS = [
    "crumpled_photo",
    "folded_coffee",
    "photocopy_fax_scan",
    "carbon_copy",
    "handwritten_fix",
    "faded_receipt",
]
# Multi-page PDFs (total always on page 2): id -> page count
PDF_PAGES = {3: 2, 5: 2, 8: 3, 15: 3, 19: 2}
LAYOUT_PLAN = {
    "crumpled_photo": ["modern", "classic", "form", "modern", "classic"],
    "folded_coffee": ["classic", "modern", "classic", "classic", "modern"],
    "photocopy_fax_scan": ["form", "modern", "classic", "form", "modern"],
    "carbon_copy": ["carbon"] * 5,
    "handwritten_fix": ["modern", "form", "classic", "modern", "form"],
    "faded_receipt": ["receipt"] * 5,
}
SCAN_PLAN = ["photocopy", "fax", "lowres", "photocopy", "fax"]


def date_fmt(d: date, style: str) -> str:
    return {
        "long": f"{d:%B} {d.day}, {d.year}",
        "abbr": f"{d:%b} {d.day}, {d.year}",
        "us": f"{d:%m/%d/%Y}",
        "short": f"{d.day}-{d:%b-%y}",
        "iso": d.isoformat(),
        "us2": f"{d.month}/{d.day}/{d:%y}",
        "us4": f"{d.month}/{d.day}/{d.year}",
    }[style]


def money(x: float, sign: bool = True) -> str:
    return ("$" if sign else "") + f"{x:,.2f}"


def split_amounts(rng: random.Random, total: float, n: int) -> list[float]:
    w = [rng.uniform(0.5, 1.5) for _ in range(n)]
    parts = [round(total * x / sum(w) / 5) * 5.0 for x in w[:-1]]
    parts.append(round(total - sum(parts), 2))
    return parts


def make_truth(i: int) -> dict:
    """Decide every fact for document i before anything is drawn."""
    rng = random.Random(f"messy-truth-{MASTER_SEED}-{i}")
    kind = KINDS[(i - 1) % 6]
    slot = (i - 1) // 6
    layout = LAYOUT_PLAN[kind][slot]
    pages = PDF_PAGES.get(i, 1)
    name, lic, email = CONTRACTORS[rng.randrange(len(CONTRACTORS))]
    wt = rng.choice(TRADES[name])
    lines, (lo, hi), wchoices, keyword = WORK[wt]
    num = rng.randint(2, 248)
    st = STREETS[rng.randrange(len(STREETS))]
    addr_short = f"{num} {st[0]} {st[2]}"
    addr_long = f"{num} {st[0]} {st[1]}"
    customer = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    done = date(2025, 1, 6) + timedelta(days=rng.randint(0, 640))
    issued = done + timedelta(days=rng.randint(0, 14))
    sub = round(rng.uniform(lo, hi) / 25) * 25.0
    if sub < 10000 and rng.random() < 0.6:
        sub += rng.choice([0.5, 0.75, 0.4, 0.95])
    taxed = rng.random() < 0.4
    tax = round(sub * 0.06625, 2) if taxed else 0.0
    total = round(sub + tax, 2)
    deposit = round(total * rng.choice([0.3, 0.4, 0.5]), 2) if rng.random() < 0.3 else 0.0
    warranty = rng.choice(wchoices)
    hand = kind == "carbon_copy"
    dstyle = rng.choice(["us2", "us4"]) if hand else rng.choice(["long", "abbr", "us", "short", "iso"])
    if layout == "receipt":
        dstyle = rng.choice(["us", "iso", "abbr"])
    t = {
        "i": i,
        "id": f"messy{i:02d}",
        "kind": kind,
        "layout": layout,
        "pages": pages,
        "contractor_name": name,
        "contractor_license": lic,
        "contractor_email": email,
        "customer_name": customer,
        "addr_short": addr_short,
        "addr_print": addr_long if rng.random() < 0.35 else addr_short,
        "work_type": wt,
        "keyword": keyword,
        "lines": list(lines),
        "done": done,
        "issued": issued,
        "subtotal": sub,
        "tax": tax,
        "total": total,
        "deposit": deposit,
        "warranty": warranty,
        "dstyle": dstyle,
        "lic_print": lic if rng.random() < 0.75 else f"{lic[:4]} {lic[4:]}",
        "invoice_no": rng.randint(1040, 9870),
        "phone": f"(732) 555-01{rng.randint(10, 99)}",
        "cust_email": f"{customer.split()[0].lower()}.{customer.split()[1].lower()}@mail.example",
        "amounts": split_amounts(rng, sub, len(lines)),
        "scan": SCAN_PLAN[slot] if kind == "photocopy_fax_scan" else None,
        "fix": None,
    }
    if kind == "handwritten_fix":
        delta = round(rng.uniform(350, 2600) / 5) * 5.0 * rng.choice([1, 1, -1])
        new_total = round(total + delta, 2)
        t["fix"] = {
            "old_total": total,
            "new_total": new_total,
            "note": rng.choice(
                [
                    "rev. per C.O. #1",
                    "corrected - add'l flashing",
                    "per phone 3/14 - credit",
                    "add 2nd coat + trim",
                    "adj. per walk-thru",
                ]
            )
            if delta > 0
            else rng.choice(["credit - unused materials", "adj. per walk-thru", "corrected"]),
            "initials": "".join(w[0] for w in name.split()[:2]).upper(),
            "date_fix": slot in (1, 3),
        }
        if t["fix"]["date_fix"]:
            t["fix"]["old_done"] = done - timedelta(days=rng.randint(3, 9))
        t["total"] = new_total
    t["total_print"] = (
        money(t["total"])
        if kind != "carbon_copy"
        else (money(t["total"]) if rng.random() < 0.6 else "$" + f"{t['total']:,.2f}")
    )
    t["done_print"] = date_fmt(
        done,
        dstyle if kind != "handwritten_fix" else "us2" if (t["fix"] and t["fix"]["date_fix"]) else dstyle,
    )
    return t


def expected(t: dict) -> dict:
    return {
        "contractor_name": t["contractor_name"],
        "contractor_license": t["contractor_license"],
        "contractor_email": t["contractor_email"],
        "customer_name": t["customer_name"],
        "job_address": t["addr_short"],
        "work_type": t["work_type"],
        "completion_date": t["done"].isoformat(),
        "total_amount": t["total"],
        "warranty_years": t["warranty"],
    }


def printed(t: dict) -> dict:
    """The exact text of each field as it is drawn (what a reader should be able to find)."""
    w = t["warranty"]
    return {
        "contractor_name": t["contractor_name"],
        "contractor_license": t["lic_print"],
        "contractor_email": t["contractor_email"],
        "customer_name": t["customer_name"],
        "job_address": t["addr_print"],
        "work_type": t["keyword"],
        "completion_date": t["done_print"],
        "total_amount": t["total_print"],
        "warranty_years": (f"{w} yr" if t["layout"] in ("carbon", "receipt") else f"{w} years")
        if w
        else (
            "no labor warranty"
            if t["layout"] in ("carbon", "receipt")
            else "no separate workmanship warranty"
        ),
    }


# ----------------------------------------------------------------------------------------------
# Drawing helpers
# ----------------------------------------------------------------------------------------------
class Page:
    """A clean page plus a handwriting mask (ink coverage, drawn per letter)."""

    def __init__(self, w=PAGE_W, h=PAGE_H, bg=(255, 255, 255)):
        self.img = Image.new("RGB", (w, h), bg)
        self.d = ImageDraw.Draw(self.img)
        self.hand = Image.new("L", (w, h), 0)  # blue-ink handwriting coverage
        self.y = 110

    def text(self, x, s, key, size, fill="black", dy=None, anchor="la"):
        self.d.text((x, self.y), s, font=font(key, size), fill=fill, anchor=anchor)
        self.y += dy if dy is not None else int(size * 1.45)

    def rule(self, x0=110, x1=PAGE_W - 110, w=2, fill="black", gap=24):
        self.d.line((x0, self.y, x1, self.y), fill=fill, width=w)
        self.y += gap


def hand(
    mask: Image.Image,
    x: float,
    base: float,
    text: str,
    key: str,
    size: float,
    rng: random.Random,
    jit: float = 1.0,
    slant: float | None = None,
) -> float:
    """Draw handwriting letter by letter: each glyph tilted, scaled and moved a little; returns end x."""
    size = size * HAND_SCALE.get(key, 1.0)
    slant = rng.uniform(-3, 3) if slant is None else slant
    drift = 0.0
    sp = font(key, int(size)).getlength(" ")
    for ch in text:
        if ch == " ":
            x += sp * rng.uniform(0.8, 1.7)
            continue
        s = 1 + rng.uniform(-0.08, 0.08) * jit
        f = font(key, int(size * s))
        drift = max(-size * 0.1, min(size * 0.1, drift + rng.uniform(-0.05, 0.05) * size * jit))
        tw = int(size * 2.4)
        tile = Image.new("L", (tw, tw), 0)
        ox, oy = int(size * 0.6), int(size * 1.5)
        ImageDraw.Draw(tile).text((ox, oy), ch, font=f, fill=255, anchor="ls")
        tile = tile.rotate(slant + rng.uniform(-4, 4) * jit, resample=Image.BICUBIC, center=(ox, oy))
        a = rng.uniform(0.72, 1.0)
        tile = tile.point(lambda v, a=a: int(v * a))
        px, py = int(x - ox), int(base + drift - oy)
        box = (px, py, px + tw, py + tw)
        mask.paste(ImageChops.lighter(mask.crop(box), tile), box)
        x += f.getlength(ch) + rng.uniform(-0.03, 0.06) * size * jit
    return x


def hand_len(text: str, key: str, size: float) -> float:
    return font(key, int(size * HAND_SCALE.get(key, 1.0))).getlength(text) * 1.08


def wavy_line(mask: Image.Image, x0, y0, x1, y1, rng, width=4, amp=3.0):
    d = ImageDraw.Draw(mask)
    n = max(8, int(abs(x1 - x0) / 18))
    ph = rng.uniform(0, 6.28)
    pts = [
        (x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n + amp * math.sin(ph + k * 1.3) + rng.uniform(-1, 1))
        for k in range(n + 1)
    ]
    d.line(pts, fill=int(rng.uniform(200, 245)), width=width, joint="curve")


def ink(img: Image.Image, mask: Image.Image, color=(30, 40, 120), rng=None, blur=0.0) -> Image.Image:
    """Multiply blue ink onto the page using the mask, with smooth 'pen pressure' variation."""
    arr = np.asarray(img).astype(np.float32)
    m = np.asarray(mask).astype(np.float32) / 255.0
    if blur:
        m = cv2.GaussianBlur(m, (0, 0), blur)
    if rng is not None:
        h, w = m.shape
        nrng = np.random.default_rng(rng.randrange(1 << 30))
        n = cv2.resize(
            nrng.standard_normal((h // 120 + 2, w // 120 + 2)).astype(np.float32),
            (w, h),
            interpolation=cv2.INTER_CUBIC,
        )
        m = m * np.clip(0.88 + 0.12 * n, 0.65, 1.0)
    c = np.array(color, np.float32) / 255.0
    arr = arr * (1 - m[..., None] * (1 - c))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def signature(mask, x, base, name, rng, size=54):
    key = rng.choice(HAND_SIG)
    end = hand(mask, x, base, name, key, size, rng, jit=1.6, slant=rng.uniform(-8, -2))
    d = ImageDraw.Draw(mask)
    d.line([(x - 10, base + 14), (end * 0.6 + x * 0.4, base + 22), (end + 20, base + 8)], fill=210, width=3)


# ----------------------------------------------------------------------------------------------
# Layouts. Each returns a list of Page objects (1-3 pages). The total is on page 2 of multi-page docs.
# ----------------------------------------------------------------------------------------------
def money_rows(t, deposit_ok=True):
    rows = [("Subtotal", money(t["subtotal"]))]
    if t["tax"]:
        rows.append(("NJ sales tax 6.625%", money(t["tax"])))
    printed_total = money(t["fix"]["old_total"]) if t["fix"] else t["total_print"]
    rows.append(("CONTRACT TOTAL" if t["deposit"] else "TOTAL", printed_total))
    if t["deposit"] and deposit_ok:
        base = t["fix"]["old_total"] if t["fix"] else t["total"]
        rows += [("Deposit received", "-" + money(t["deposit"])), ("BALANCE DUE", money(base - t["deposit"]))]
    return rows


def draw_money(p: Page, t, rng, key, key_b, x_lab, x_val, size=36):
    total_box = None
    for lab, val in money_rows(t):
        bold = lab in ("TOTAL", "CONTRACT TOTAL", "BALANCE DUE")
        f = key_b if bold else key
        p.d.text((x_lab, p.y), lab, font=font(f, size), fill="black")
        p.d.text((x_val, p.y), val, font=font(f, size + (2 if bold else 0)), fill="black", anchor="ra")
        if lab in ("TOTAL", "CONTRACT TOTAL"):
            tw = font(f, size + 2).getlength(val)
            total_box = (x_val - tw, p.y, x_val, p.y + size + 4)
        p.y += int(size * 1.6)
    if t["fix"]:
        apply_fix(p, t, rng, total_box, x_lab)
        p.y += 40
    p.y += 20
    return total_box


def apply_fix(p: Page, t, rng, box, x_lab):
    """Strike out the printed total and write the corrected one by hand left of the TOTAL label."""
    x0, y0, x1, y1 = box
    ym = (y0 + y1) / 2 + 4
    for _ in range(rng.choice([1, 2])):
        wavy_line(
            p.hand,
            x0 - 12,
            ym + rng.uniform(-6, 6),
            x1 + 12,
            ym + rng.uniform(-8, 8),
            rng,
            width=rng.choice([3, 4]),
            amp=2.5,
        )
    key = rng.choice(["kalam_b", "patrick", "gochi", "marker"])
    size = 52
    txt = t["total_print"]
    w = hand_len(txt, key, size)
    hx, hb = x_lab - 90 - w, y1 + 4
    end = hand(p.hand, hx, hb, txt, key, size, rng, jit=0.8)
    d = ImageDraw.Draw(p.hand)
    ay = hb - 16
    d.line([(end + 12, ay), (x_lab - 14, ay + rng.uniform(-4, 4))], fill=220, width=3)
    d.line([(x_lab - 30, ay - 10), (x_lab - 14, ay), (x_lab - 30, ay + 10)], fill=220, width=3)
    nk = rng.choice(HAND_NOTE)
    nend = hand(p.hand, hx, hb + 58, t["fix"]["note"], nk, 30, rng)
    hand(p.hand, nend + 16, hb + 58, t["fix"]["initials"], rng.choice(HAND_NOTE), 34, rng)


def done_line(p: Page, t, rng, x, label, key, size, fill="black"):
    """Print the completion date; on 'date_fix' docs print the old date, strike it, write the true one."""
    if t["fix"] and t["fix"]["date_fix"]:
        old = date_fmt(t["fix"]["old_done"], "us")
        p.d.text((x, p.y), f"{label} {old}", font=font(key, size), fill=fill)
        lw = font(key, size).getlength(label + " ")
        ow = font(key, size).getlength(old)
        wavy_line(
            p.hand, x + lw - 6, p.y + size * 0.55, x + lw + ow + 6, p.y + size * 0.6, rng, width=3, amp=2
        )
        hand(
            p.hand,
            x + lw + ow + 30,
            p.y + size * 0.95,
            t["done_print"],
            rng.choice(["kalam", "patrick"]),
            size * 1.2,
            rng,
            jit=0.7,
        )
        p.y += int(size * 1.7)
    else:
        p.text(x, f"{label} {t['done_print']}", key, size, fill=fill)


def warranty_text(t) -> str:
    w = t["warranty"]
    if w:
        return f"Workmanship warranty: {w} years from completion. Manufacturer warranties pass to the owner."
    return "Manufacturer warranties apply. There is no separate workmanship warranty on this job."


TERMS = [
    "1. Payment is due within 15 days of the invoice date. Late balances accrue 1.5% per month.",
    "2. Manufacturer warranties on materials are passed through to the owner as issued.",
    "3. Any change to the scope of work must be agreed in writing as a change order.",
    "4. Owner is responsible for moving personal property out of the work area.",
    "5. Contractor is not responsible for pre-existing conditions hidden behind finished surfaces.",
    "6. Permits and inspections were requested by the contractor where required by the Borough.",
    "7. Disputes go first to mediation in Ocean County before any other action.",
]


def wrap(text, key, size, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        nxt = (cur + " " + w).strip()
        if font(key, size).getlength(nxt) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = nxt
    return lines + [cur]


def terms_page(t, rng, key, key_b, header) -> Page:
    p = Page()
    header(p, small=True, page_no=3)
    p.y += 30
    p.text(110, "TERMS AND CONDITIONS", key_b, 40, dy=70)
    for para in TERMS:
        for ln in wrap(para, key, 30, PAGE_W - 240):
            p.text(130, ln, key, 30, dy=44)
        p.y += 16
    p.y += 60
    p.text(110, "Owner acceptance: ______________________________    Date: ____________", key, 30, dy=90)
    signature(p.hand, 120, p.y + 40, t["contractor_name"].split()[0] + " " + rng.choice(LAST), rng)
    p.y += 70
    p.text(110, f"For {t['contractor_name']}", key, 30)
    return p


def item_rows(p: Page, t, key, size, x_desc, x_amt, bullet="", amounts=True):
    for ln, amt in zip(t["lines"], t["amounts"]):
        p.d.text((x_desc, p.y), bullet + ln, font=font(key, size), fill="black")
        if amounts:
            p.d.text((x_amt, p.y), money(amt), font=font(key, size), fill="black", anchor="ra")
        p.y += int(size * 1.55)


def layout_modern(t, rng):
    band = rng.choice(["#1f4e5f", "#3b3f6b", "#5a3a2e", "#24563a"])
    pages_n = t["pages"]

    def header(p: Page, small=False, page_no=1):
        if small:
            p.d.rectangle((0, 0, PAGE_W, 90), fill=band)
            p.d.text(
                (110, 26),
                f"{t['contractor_name']}  -  Invoice # {t['invoice_no']}",
                font=font("sans_b", 34),
                fill="white",
            )
            p.d.text(
                (PAGE_W - 110, 30),
                f"Page {page_no} of {pages_n}",
                font=font("sans", 28),
                fill="white",
                anchor="ra",
            )
            p.y = 140
            return
        p.d.rectangle((0, 0, PAGE_W, 260), fill=band)
        p.y = 60
        p.text(110, t["contractor_name"], "sans_b", 76, fill="white", dy=100)
        p.text(
            110,
            f"NJ HIC Reg. # {t['lic_print']}   |   {t['contractor_email']}   |   {t['phone']}",
            "sans",
            32,
            fill="#e2ecef",
        )
        p.y = 320

    p1 = Page()
    header(p1)
    p1.text(110, "INVOICE", "sans_b", 64, dy=90)
    p1.text(
        110,
        f"Invoice # {t['invoice_no']}        Invoice date: {date_fmt(t['issued'], t['dstyle'])}",
        "sans",
        34,
    )
    done_line(p1, t, rng, 110, "Work completed:", "sans", 34)
    p1.y += 40
    p1.text(110, "BILL TO", "sans_b", 30, fill=band, dy=44)
    p1.text(110, t["customer_name"], "sans", 36)
    if rng.random() < 0.5:
        p1.text(110, t["cust_email"], "sans", 30)
    p1.y += 20
    p1.text(110, "JOB SITE", "sans_b", 30, fill=band, dy=44)
    p1.text(110, t["addr_print"], "sans", 36)
    p1.text(110, TOWN, "sans", 36, dy=90)
    p1.d.rectangle((110, p1.y, PAGE_W - 110, p1.y + 56), fill="#eef2f3")
    p1.d.text((130, p1.y + 12), "DESCRIPTION", font=font("sans_b", 30), fill=band)
    p1.d.text((PAGE_W - 130, p1.y + 12), "AMOUNT", font=font("sans_b", 30), fill=band, anchor="ra")
    p1.y += 80
    item_rows(p1, t, "sans", 36, 130, PAGE_W - 130)
    p1.y += 10
    p1.rule(w=1, fill="#999999")
    if pages_n == 1:
        draw_money(p1, t, rng, "sans", "sans_b", 900, PAGE_W - 130)
        for ln in wrap(warranty_text(t), "sans", 28, PAGE_W - 220):
            p1.text(110, ln, "sans", 28, fill="#333333", dy=40)
        p1.text(
            110,
            "Thank you for your business! Checks payable to the company above.",
            "sans",
            28,
            fill="#333333",
        )
        return [p1]
    p1.y += 30
    p1.text(110, "Continued on page 2.", "sans", 30, fill="#555555")
    p2 = Page()
    header(p2, small=True, page_no=2)
    p2.y += 40
    p2.text(110, "Materials and notes", "sans_b", 36, dy=60)
    for ln in [
        "All debris removed from site, magnetic sweep done",
        "Final walk-through with owner completed",
        f"Work completed {t['done_print']}"
        if not (t["fix"] and t["fix"]["date_fix"])
        else "Punch list items closed out",
    ]:
        p2.text(130, "-  " + ln, "sans", 32)
    p2.y += 50
    p2.rule(w=1, fill="#999999")
    draw_money(p2, t, rng, "sans", "sans_b", 900, PAGE_W - 130)
    for ln in wrap(warranty_text(t), "sans", 28, PAGE_W - 220):
        p2.text(110, ln, "sans", 28, fill="#333333", dy=40)
    pages = [p1, p2]
    if pages_n == 3:
        pages.append(terms_page(t, rng, "sans", "sans_b", header))
    return pages


def layout_classic(t, rng):
    pages_n = t["pages"]
    cx = PAGE_W // 2

    def header(p: Page, small=False, page_no=1):
        if small:
            p.y = 90
            p.text(
                cx,
                f"{t['contractor_name']}  ~  Statement {t['invoice_no']}  ~  page {page_no} of {pages_n}",
                "serif",
                28,
                anchor="ma",
                dy=50,
            )
            p.rule(w=1)
            return
        p.y = 110
        p.text(cx, t["contractor_name"], "serif_b", 64, anchor="ma", dy=84)
        p.text(
            cx,
            f"Licensed & Insured  ~  NJ Registration No. {t['lic_print']}",
            "serif",
            32,
            anchor="ma",
            dy=46,
        )
        p.text(cx, f"{TOWN}  ~  {t['contractor_email']}  ~  {t['phone']}", "serif", 30, anchor="ma", dy=60)
        p.rule(w=3, gap=8)
        p.rule(w=1, gap=50)

    p1 = Page(bg=(253, 252, 247))
    header(p1)
    p1.text(110, f"Date of statement: {date_fmt(t['issued'], t['dstyle'])}", "serif", 34, dy=70)
    p1.text(110, f"Statement for: {t['customer_name']}", "serif", 36)
    p1.text(110, f"Property: {t['addr_print']}, {TOWN}", "serif", 36)
    done_line(p1, t, rng, 110, "Project finished on", "serif", 36)
    p1.y += 40
    p1.text(110, f"Dear {t['customer_name'].split()[0]},", "serif", 34, dy=60)
    for ln in wrap(
        f"Thank you for choosing {t['contractor_name']}. Please find below our statement for the "
        f"{t['keyword']} work at your property.",
        "serif",
        34,
        PAGE_W - 220,
    ):
        p1.text(110, ln, "serif", 34, dy=50)
    p1.y += 30
    p1.text(110, "Description of work", "serif_b", 38, dy=60)
    item_rows(p1, t, "serif", 34, 150, PAGE_W - 150)
    p1.y += 20
    if pages_n == 1:
        p1.rule(w=1, x0=850, x1=PAGE_W - 150)
        draw_money(p1, t, rng, "serif", "serif_b", 850, PAGE_W - 150, size=34)
        for ln in wrap(warranty_text(t), "serif", 30, PAGE_W - 220):
            p1.text(110, ln, "serif", 30, fill="#333333", dy=44)
        p1.y += 30
        p1.text(110, "Respectfully,", "serif", 32, dy=70)
        signature(p1.hand, 120, p1.y + 20, t["contractor_name"].split()[0] + " " + rng.choice(LAST), rng)
        return [p1]
    p1.y += 30
    p1.text(110, "(statement continues on the next page)", "serif", 30, fill="#555555")
    p2 = Page(bg=(253, 252, 247))
    header(p2, small=True, page_no=2)
    p2.y += 30
    p2.text(110, "Summary of charges", "serif_b", 38, dy=70)
    p2.rule(w=1, x0=850, x1=PAGE_W - 150)
    draw_money(p2, t, rng, "serif", "serif_b", 850, PAGE_W - 150, size=34)
    for ln in wrap(warranty_text(t), "serif", 30, PAGE_W - 220):
        p2.text(110, ln, "serif", 30, fill="#333333", dy=44)
    p2.y += 40
    p2.text(110, "Respectfully,", "serif", 32, dy=70)
    signature(p2.hand, 120, p2.y + 20, t["contractor_name"].split()[0] + " " + rng.choice(LAST), rng)
    pages = [p1, p2]
    if pages_n == 3:
        pages.append(terms_page(t, rng, "serif", "serif_b", header))
    return pages


def layout_form(t, rng):
    """Pre-printed office form with boxes, values typed in."""
    pages_n = t["pages"]
    line_c = rng.choice(["#22335a", "#444444", "#7a1f1f"])

    def header(p: Page, small=False, page_no=1):
        p.d.rectangle((70, 70, PAGE_W - 70, PAGE_H - 70), outline=line_c, width=4)
        if small:
            p.y = 100
            p.text(
                110,
                f"{t['contractor_name'].upper()}   INVOICE {t['invoice_no']}   PAGE {page_no}/{pages_n}",
                "cond_b",
                32,
                fill=line_c,
                dy=60,
            )
            return
        p.y = 100
        p.text(110, t["contractor_name"].upper(), "cond_b", 62, fill=line_c, dy=78)
        p.text(
            110,
            f"HIC # {t['lic_print']}    {t['contractor_email']}    {t['phone']}",
            "cond",
            32,
            fill=line_c,
            dy=70,
        )
        p.d.text((PAGE_W - 110, 110), "INVOICE", font=font("cond_b", 54), fill=line_c, anchor="ra")
        p.d.text(
            (PAGE_W - 110, 180), f"No. {t['invoice_no']}", font=font("cond", 36), fill=line_c, anchor="ra"
        )

    def box_row(p: Page, cells):
        """cells: list of (label, value, width_fraction)."""
        x = 110
        full = PAGE_W - 220
        h = 100
        for lab, val, fr in cells:
            w = full * fr
            p.d.rectangle((x, p.y, x + w, p.y + h), outline=line_c, width=2)
            p.d.text((x + 12, p.y + 8), lab, font=font("cond", 22), fill=line_c)
            if callable(val):
                val(x + 16, p.y + 82)
            else:
                p.d.text((x + 16, p.y + 44), val, font=font("cond", 38), fill="black")
            x += w
        p.y += h

    p1 = Page()
    header(p1)
    box_row(
        p1,
        [("CUSTOMER", t["customer_name"], 0.55), ("INVOICE DATE", date_fmt(t["issued"], t["dstyle"]), 0.45)],
    )
    if t["fix"] and t["fix"]["date_fix"]:

        def date_val(x, base):
            old = date_fmt(t["fix"]["old_done"], "us")
            p1.d.text((x, base - 38), old, font=font("cond", 38), fill="black")
            ow = font("cond", 38).getlength(old)
            wavy_line(p1.hand, x - 4, base - 18, x + ow + 6, base - 16, rng, width=3, amp=2)
            hand(p1.hand, x + ow + 24, base, t["done_print"], "kalam", 40, rng, jit=0.7)

        done_val = date_val
    else:
        done_val = t["done_print"]
    box_row(
        p1,
        [
            ("JOB SITE ADDRESS", f"{t['addr_print']}, Sampleton", 0.55),
            ("DATE WORK COMPLETED", done_val, 0.45),
        ],
    )
    box_row(p1, [("TOWN / ZIP", TOWN, 0.55), ("CUSTOMER PHONE", f"(732) 555-01{rng.randint(10, 99)}", 0.45)])
    p1.y += 40
    p1.d.rectangle((110, p1.y, PAGE_W - 110, p1.y + 56), fill=line_c)
    p1.d.text((130, p1.y + 12), "QTY", font=font("cond_b", 28), fill="white")
    p1.d.text((260, p1.y + 12), "DESCRIPTION OF WORK", font=font("cond_b", 28), fill="white")
    p1.d.text((PAGE_W - 130, p1.y + 12), "AMOUNT", font=font("cond_b", 28), fill="white", anchor="ra")
    p1.y += 76
    for ln, amt in zip(t["lines"], t["amounts"]):
        p1.d.text((140, p1.y), "1", font=font("cond", 36), fill="black")
        p1.d.text((260, p1.y), ln, font=font("cond", 36), fill="black")
        p1.d.text((PAGE_W - 130, p1.y), money(amt), font=font("cond", 36), fill="black", anchor="ra")
        p1.y += 50
        p1.d.line((110, p1.y, PAGE_W - 110, p1.y), fill="#bbbbbb", width=1)
        p1.y += 14
    p1.y += 30

    def bottom(p: Page):
        draw_money(p, t, rng, "cond", "cond_b", 950, PAGE_W - 130, size=36)
        p.y += 10
        for ln in wrap(warranty_text(t), "cond", 30, PAGE_W - 240):
            p.text(120, ln, "cond", 30, fill="#222222", dy=42)
        p.y += 30
        p.text(120, "Customer signature: ________________________", "cond", 30, fill=line_c)

    if pages_n == 1:
        bottom(p1)
        return [p1]
    p1.text(120, "SEE PAGE 2 FOR TOTALS", "cond_b", 32, fill=line_c)
    p2 = Page()
    header(p2, small=True, page_no=2)
    p2.y += 30
    p2.text(120, "NOTES", "cond_b", 30, fill=line_c, dy=46)
    p2.text(120, "Site cleaned daily. Final walk-through done with owner.", "cond", 32, dy=80)
    bottom(p2)
    pages = [p1, p2]
    if pages_n == 3:
        pages.append(terms_page(t, rng, "cond", "cond_b", header))
    return pages


def layout_carbon(t, rng):
    """Invoice-book carbon copy: pre-printed form on colored paper, filled in by hand."""
    W, H = 1240, 1700
    paper = rng.choice([(246, 214, 220), (247, 238, 186), (244, 242, 234), (226, 236, 246)])
    pc = rng.choice([(176, 40, 46), (40, 40, 110), (30, 30, 30)])
    p = Page(W, H, bg=paper)
    d = p.d
    hk = rng.choice(HAND_FIELD)
    hs = 40
    d.text((70, 60), "INVOICE", font=font("cond_b", 64), fill=pc)
    d.text(
        (W - 70, 72),
        f"No. {t['invoice_no'] + 3000:06d}",
        font=font("mono_b", 40),
        fill=(170, 30, 30),
        anchor="ra",
    )
    # company stamp in purple ink, slightly tilted
    stamp = Image.new("L", (1000, 230), 0)
    sd = ImageDraw.Draw(stamp)
    sd.rectangle((4, 4, 996, 226), outline=255, width=4)
    sd.text((500, 22), t["contractor_name"].upper(), font=font("cond_b", 50), fill=255, anchor="ma")
    sd.text((500, 92), f"NJ HIC # {t['lic_print']}", font=font("cond_b", 38), fill=255, anchor="ma")
    sd.text(
        (500, 148), f"{t['contractor_email']}  {t['phone']}", font=font("cond", 32), fill=255, anchor="ma"
    )
    stamp = stamp.rotate(rng.uniform(-2.5, 2.5), resample=Image.BICUBIC, expand=True)
    sn = np.random.default_rng(rng.randrange(1 << 30)).uniform(0.55, 1.0, (stamp.height, stamp.width))
    stamp = Image.fromarray(
        (np.asarray(stamp) * cv2.GaussianBlur(sn.astype(np.float32), (0, 0), 1.5)).astype(np.uint8)
    )
    stamp_layer = Image.new("L", (W, H), 0)
    stamp_layer.paste(stamp, (110 + rng.randint(-20, 20), 160))
    y = 430
    fields = [("Name", t["customer_name"]), ("Job Address", t["addr_print"]), ("City", "Sampleton NJ 08736")]
    for lab, val in fields:
        d.text((70, y), lab, font=font("cond", 30), fill=pc)
        lx = 70 + font("cond", 30).getlength(lab) + 16
        d.line((lx, y + 34, W - 70, y + 34), fill=pc, width=2)
        hand(p.hand, lx + 20, y + 28, val, hk, hs, rng)
        y += 74
    d.text((70, y), "Date", font=font("cond", 30), fill=pc)
    d.line((150, y + 34, 560, y + 34), fill=pc, width=2)
    hand(p.hand, 170, y + 28, date_fmt(t["issued"], t["dstyle"]), hk, hs, rng)
    d.text((600, y), "Job Completed", font=font("cond", 30), fill=pc)
    d.line((790, y + 34, W - 70, y + 34), fill=pc, width=2)
    hand(p.hand, 810, y + 28, t["done_print"], hk, hs, rng)
    y += 96
    # item table
    cols = [70, 170, W - 330, W - 70]
    d.rectangle((70, y, W - 70, y + 50), outline=pc, width=2)
    for lab, x in [("QTY", 85), ("DESCRIPTION", 190), ("AMOUNT", W - 310)]:
        d.text((x, y + 10), lab, font=font("cond_b", 26), fill=pc)
    y += 50
    rows = 9
    row_h = 62
    for r in range(rows):
        d.line((70, y + row_h * (r + 1), W - 70, y + row_h * (r + 1)), fill=pc, width=1)
    for x in cols:
        d.line((x, y, x, y + row_h * rows), fill=pc, width=2)
    items = list(zip(t["lines"], t["amounts"]))
    for r, (ln, amt) in enumerate(items):
        bl = y + row_h * (r + 1) - 14
        hand(p.hand, 105, bl, "1", hk, hs * 0.9, rng)
        hand(p.hand, 190, bl, ln, hk, hs * 0.85, rng)
        hand(p.hand, W - 315, bl, f"{amt:,.2f}", hk, hs * 0.9, rng)
    r = len(items)
    note = f"{t['warranty']} yr warranty on labor" if t["warranty"] else "No labor warranty - mfr only"
    hand(p.hand, 190, y + row_h * (r + 1) - 14, note, rng.choice(HAND_NOTE), hs * 0.85, rng)
    y += row_h * rows + 14
    for lab, val in ([("TAX", f"{t['tax']:,.2f}")] if t["tax"] else []) + [("TOTAL", t["total_print"])]:
        d.text((W - 520, y + 10), lab, font=font("cond_b", 34), fill=pc)
        d.rectangle((W - 330, y, W - 70, y + 64), outline=pc, width=3 if lab == "TOTAL" else 2)
        hand(p.hand, W - 315, y + 50, val, hk, hs * (1.0 if lab == "TOTAL" else 0.9), rng)
        y += 76
    if t["deposit"]:
        hand(p.hand, 90, y - 20, f"Dep. pd {money(t['deposit'])}", rng.choice(HAND_NOTE), 34, rng)
    y += 30
    d.text((70, y), "Rec'd by", font=font("cond", 30), fill=pc)
    d.line((200, y + 34, 700, y + 34), fill=pc, width=2)
    hand(
        p.hand,
        220,
        y + 28,
        t["customer_name"].split()[0][0] + ". " + t["customer_name"].split()[1],
        "apple",
        34,
        rng,
        jit=1.5,
    )
    d.text((70, H - 90), "CUSTOMER COPY  -  THANK YOU", font=font("cond_b", 26), fill=pc)
    p.stamp = stamp_layer
    return [p]


def layout_receipt(t, rng):
    """Narrow card-terminal style receipt; faded later."""
    W = 760
    lines_needed = 36 + len(t["lines"]) * 2
    H = 230 + lines_needed * 44
    p = Page(W, H, bg=(250, 249, 244))
    p.y = 60
    f, fb = "mono", "mono_b"
    s = 26
    cx = W // 2
    for ln in wrap(t["contractor_name"].upper(), fb, 34, W - 80):
        p.text(cx, ln, fb, 34, anchor="ma", dy=46)
    p.text(cx, f"NJ HIC REG {t['lic_print']}", f, s, anchor="ma", dy=38)
    p.text(cx, t["contractor_email"], f, s - 2, anchor="ma", dy=38)
    p.text(cx, t["phone"], f, s, anchor="ma", dy=50)
    dash = "-" * 38

    def lr(left, right, key=f, size=s):
        p.d.text((40, p.y), left, font=font(key, size), fill="black")
        p.d.text((W - 40, p.y), right, font=font(key, size), fill="black", anchor="ra")
        p.y += int(size * 1.5)

    p.text(40, dash, f, s, dy=40)
    lr("INVOICE", f"#{t['invoice_no']}")
    lr("PRINTED", date_fmt(t["issued"], "iso"))
    p.text(40, f"CUST: {t['customer_name']}", f, s, dy=40)
    p.text(40, f"SITE: {t['addr_print']}", f, s, dy=40)
    p.text(40, "      SAMPLETON NJ 08736", f, s, dy=40)
    p.text(40, f"WORK DONE: {t['done_print']}", f, s, dy=40)
    p.text(40, dash, f, s, dy=40)
    for ln, amt in zip(t["lines"], t["amounts"]):
        for k, part in enumerate(wrap(ln.upper(), f, s, W - 80)):
            p.text(40, part, f, s, dy=38)
        lr("", f"{amt:,.2f}")
    p.text(40, dash, f, s, dy=40)
    lr("SUBTOTAL", f"{t['subtotal']:,.2f}")
    if t["tax"]:
        lr("TAX 6.625%", f"{t['tax']:,.2f}")
    lr("TOTAL", t["total_print"], fb, 30)
    if t["deposit"]:
        lr("DEPOSIT PAID", f"-{t['deposit']:,.2f}")
        lr("BALANCE", f"{t['total'] - t['deposit']:,.2f}")
    p.text(40, dash, f, s, dy=40)
    lr("VISA ****" + str(rng.randint(1000, 9999)), "APPROVED")
    lr("AUTH", f"{rng.randint(100000, 999999)}")
    p.text(40, dash, f, s, dy=40)
    w = t["warranty"]
    p.text(40, f"LABOR WARRANTY: {w} YR" if w else "NO LABOR WARRANTY", f, s, dy=40)
    p.text(40, "MFR WARRANTIES PASS TO OWNER", f, s, dy=40)
    p.text(cx, "THANK YOU!", fb, 30, anchor="ma", dy=60)
    p.img = p.img.crop((0, 0, W, p.y + 140))  # tear the strip just below the last line
    p.hand = p.hand.crop((0, 0, W, p.y + 140))
    return [p]


LAYOUTS = {
    "modern": layout_modern,
    "classic": layout_classic,
    "form": layout_form,
    "carbon": layout_carbon,
    "receipt": layout_receipt,
}


def flatten(p: Page, rng: random.Random, carbon: bool = False) -> np.ndarray:
    """Composite handwriting onto the page; carbon copies get a blurred, mottled, offset-ghost look."""
    if carbon:
        c = (60, 50, 150)
        img = p.img
        stamp = getattr(p, "stamp", None)
        if stamp is not None:
            img = ink(img, stamp, color=(110, 60, 150), blur=0.6)
        dx, dy = rng.choice([-1, 1]) * rng.randint(2, 4), rng.choice([-1, 1]) * rng.randint(1, 3)
        ghost = Image.fromarray((np.roll(np.asarray(p.hand), (dy, dx), (0, 1)) * 0.5).astype(np.uint8))
        img = ink(img, ghost, color=c, blur=1.4)
        # carbon smudges where a hand rested on the book, plus fine carbon dust
        h, w = p.hand.height, p.hand.width
        nrng = np.random.default_rng(rng.randrange(1 << 30))
        sm = np.zeros((h, w), np.float32)
        for _ in range(rng.randint(2, 4)):
            cv2.ellipse(
                sm,
                (int(nrng.uniform(0, w)), int(nrng.uniform(0.3 * h, h))),
                (int(nrng.uniform(60, 220)), int(nrng.uniform(30, 90))),
                float(nrng.uniform(0, 180)),
                0,
                360,
                float(nrng.uniform(10, 26)),
                -1,
            )
        sm = cv2.GaussianBlur(sm, (0, 0), 25) + (nrng.random((h, w)) > 0.9993) * 120
        img = ink(img, Image.fromarray(np.clip(sm, 0, 255).astype(np.uint8)), color=c, blur=0.6)
        img = ink(img, p.hand, color=c, rng=rng, blur=0.8)
    else:
        img = ink(p.img, p.hand, color=rng.choice([(30, 40, 120), (25, 30, 90), (20, 20, 30)]), rng=rng)
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


# ----------------------------------------------------------------------------------------------
# Damage (OpenCV + tuned Augraphy). All randomness comes from the numpy Generator passed in.
# Strength s is 1.0 at first and drops by x0.7 each time the gate fails.
# ----------------------------------------------------------------------------------------------
def smooth_noise(rng, h, w, cell):
    n = rng.standard_normal((int(h // cell) + 2, int(w // cell) + 2)).astype(np.float32)
    return cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)


def height_map(h, w, rng, crumple=1.0, folds=0, creases=()):
    k = w / 1240
    hm = np.zeros((h, w), np.float32)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    if crumple > 0:
        for cell, amp in ((260, 0.8), (110, 0.25)):
            hm += amp * crumple * (1 - np.abs(smooth_noise(rng, h, w, cell * k)))
        # straight random creases make the flat facets of real crumpled paper
        for _ in range(int(6 + 12 * crumple)):
            x0, y0, th = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(0, np.pi)
            d = np.abs((xs - x0) * np.sin(th) - (ys - y0) * np.cos(th))
            width = rng.uniform(60, 320) * k
            hm += rng.choice([-1, 1]) * rng.uniform(0.3, 0.8) * crumple * np.clip(1 - d / width, 0, 1)
    for _ in range(folds):
        d = (
            np.abs(ys - h * rng.uniform(0.3, 0.7))
            if rng.random() < 0.5
            else np.abs(xs - w * rng.uniform(0.3, 0.7))
        )
        hm += 2.0 * np.clip(1 - d / (0.5 * max(h, w)), 0, 1)
    for frac, sign in creases:  # letter folded in thirds: sharp tent creases across the page
        d = np.abs(ys - h * frac)
        hm += sign * (1.6 * np.clip(1 - d / (0.33 * h), 0, 1) + 0.5 * np.clip(1 - d / 10, 0, 1))
    return hm * 18


def bend_and_shade(img, hm, rng, disp=25.0, shade_k=0.45, light=None):
    h, w = hm.shape
    gy, gx = np.gradient(cv2.GaussianBlur(hm, (0, 0), 2))
    dgy, dgx = np.gradient(cv2.GaussianBlur(hm, (0, 0), 25))
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    out = cv2.remap(
        img.astype(np.float32),
        xs + dgx * disp,
        ys + dgy * disp,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    if light is None:
        light = np.array([rng.uniform(-0.7, 0.7), rng.uniform(-0.8, -0.3), 1.0])
    light = light / np.linalg.norm(light)
    nz = 1 / np.sqrt(gx**2 + gy**2 + 1)
    sh = -gx * nz * light[0] - gy * nz * light[1] + nz * light[2]
    sh = (1 - shade_k) + shade_k * sh / sh.max()
    return np.clip(out * sh[..., None], 0, 255)


def background(rng, H, W, kind):
    if kind == "wood":
        base = np.array([rng.uniform(45, 70), rng.uniform(75, 100), rng.uniform(110, 145)], np.float32)
        bg = np.ones((H, W, 3), np.float32) * base
        streak = cv2.resize(rng.standard_normal((H // 3, 6)).astype(np.float32), (W, H)) * 14
        grain = cv2.resize(rng.standard_normal((H, 24)).astype(np.float32), (W, H)) * 6
        bg += (streak + grain)[..., None]
        for _ in range(3):  # plank seams
            x = int(rng.uniform(0, W))
            bg[:, max(0, x - 1) : x + 2] *= 0.82
    elif kind == "granite":
        base = np.array([rng.uniform(110, 160)] * 3, np.float32) + np.array([0, 4, 10], np.float32)
        bg = np.ones((H, W, 3), np.float32) * base
        bg += (smooth_noise(rng, H, W, 40) * 10)[..., None]
        speck = rng.random((H, W)).astype(np.float32)
        bg -= ((speck > 0.93) * 55 + (speck < 0.04) * -40)[..., None]
        bg = cv2.GaussianBlur(bg, (0, 0), 1.0)
    elif kind == "laminate":
        base = np.array([rng.uniform(190, 215), rng.uniform(195, 220), rng.uniform(200, 225)], np.float32)
        bg = np.ones((H, W, 3), np.float32) * base + (smooth_noise(rng, H, W, 60) * 5)[..., None]
    else:  # dark desk / truck seat
        base = np.array([rng.uniform(35, 55)] * 3, np.float32)
        bg = np.ones((H, W, 3), np.float32) * base + (smooth_noise(rng, H, W, 8) * 6)[..., None]
    return bg


def camera(img, rng, s=1.0, bg_kind=None, scale=None, quality=None, page_mask=None):
    """Phone photo of a page lying on a surface: shadow, perspective tilt, light falloff, noise, JPEG."""
    img = img.astype(np.float32)
    h, w = img.shape[:2]
    pad = int(0.1 * max(h, w))
    H, W = h + 2 * pad, w + 2 * pad
    bg_kind = bg_kind or rng.choice(["wood", "granite", "laminate", "dark"])
    canvas = background(rng, H, W, bg_kind)
    mask = np.zeros((H, W), np.float32)
    pm = np.ones((h, w), np.float32) if page_mask is None else page_mask
    mask[pad : pad + h, pad : pad + w] = pm
    sh = cv2.GaussianBlur(
        np.roll(mask, (int(rng.uniform(8, 22)), int(rng.uniform(-18, 18))), (0, 1)), (0, 0), 15
    )
    canvas = canvas * (1 - 0.5 * sh[..., None])
    region = canvas[pad : pad + h, pad : pad + w]
    canvas[pad : pad + h, pad : pad + w] = region * (1 - pm[..., None]) + img * pm[..., None]
    q = np.float32([[pad, pad], [W - pad, pad], [W - pad, H - pad], [pad, H - pad]])
    tilt = (0.03 + 0.07 * s) * w

    def j():
        return rng.uniform(-tilt, tilt)

    dst = np.float32(
        [
            [pad + j(), pad + j()],
            [W - pad + j(), pad + j()],
            [W - pad + j(), H - pad + j()],
            [pad + j(), H - pad + j()],
        ]
    )
    M = cv2.getPerspectiveTransform(q, dst)
    out = cv2.warpPerspective(canvas, M, (W, H), borderMode=cv2.BORDER_REFLECT_101)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    ang = rng.uniform(0, 2 * np.pi)
    side = (np.cos(ang) * (xx / W - 0.5) + np.sin(ang) * (yy / H - 0.5)) + 0.5
    fall = 1 - (0.15 + 0.2 * s) * side - 0.2 * (((xx - W / 2) / W) ** 2 + ((yy - H / 2) / H) ** 2)
    out = out * fall[..., None]
    if rng.random() < 0.6:  # soft shadow of the phone / a hand
        poly = np.zeros((H, W), np.float32)
        cx, cy = rng.uniform(0, W), rng.choice([0, H])
        pts = np.int32(
            [
                [cx - W * 0.3, cy],
                [cx + W * 0.3, cy],
                [cx + W * 0.2, cy + (H * 0.35 if cy == 0 else -H * 0.35)],
                [cx - W * 0.25, cy + (H * 0.3 if cy == 0 else -H * 0.3)],
            ]
        )
        cv2.fillPoly(poly, [pts], 1.0)
        poly = cv2.GaussianBlur(poly, (0, 0), 60)
        out = out * (1 - (0.15 + 0.15 * s) * poly[..., None])
    blur = rng.uniform(0.3, 0.6 + 0.6 * s)
    out = cv2.GaussianBlur(out, (0, 0), blur)
    out = np.clip(out + rng.normal(0, 2 + 3 * s, out.shape), 0, 255).astype(np.uint8)
    scale = scale or rng.uniform(0.62, 0.78)
    out = cv2.resize(out, (int(W * scale), int(H * scale)), interpolation=cv2.INTER_AREA)
    return out, int(quality or rng.uniform(62, 78))


def coffee_ring(img, rng, s, avoid=None):
    h, w = img.shape[:2]
    r = rng.uniform(0.13, 0.17) * w
    for _ in range(20):
        cx, cy = rng.uniform(0.15 * w, 0.85 * w), rng.uniform(0.15 * h, 0.85 * h)
        if avoid is None or not (avoid[0] - r < cx < avoid[2] + r and avoid[1] - r < cy < avoid[3] + r):
            break
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    th = np.arctan2(yy - cy, xx - cx)
    wob = r * (1 + 0.015 * np.sin(3 * th + rng.uniform(0, 6)))
    edge = np.exp(-(((d - wob) / (0.022 * r)) ** 2))
    arc = np.clip(0.55 + 0.45 * np.sin(th * rng.choice([1, 2]) + rng.uniform(0, 6)), 0.15, 1)
    fill = (d < wob) * 0.22 * np.clip(0.6 + 0.4 * smooth_noise(rng, h, w, 80), 0, 1)
    a = np.clip((0.95 * edge * arc + fill) * (0.3 + 0.15 * s), 0, 0.5)
    if rng.random() < 0.5:  # a drip
        dx, dy = cx + r * rng.uniform(0.8, 1.3), cy + r * rng.uniform(-0.6, 0.6)
        a = np.maximum(a, np.exp(-(((xx - dx) ** 2 + (yy - dy) ** 2) / (0.06 * r) ** 2)) * 0.35 * s)
    stain = np.array([40, 85, 135], np.float32) / 255.0  # BGR brown
    return img * (1 - a[..., None] * (1 - stain)), {"center": [round(cx), round(cy)], "radius": round(r)}


def seed_globals(rng):
    s = int(rng.integers(0, 2**31 - 1))
    random.seed(s)
    np.random.seed(s)


def augraphy_scan(img, rng, s, variant, sent: date | None = None):
    from augraphy import AugraphyPipeline, BadPhotoCopy, DirtyDrum, Faxify, InkBleed, LowInkPeriodicLines

    seed_globals(rng)
    img = img.astype(np.uint8)
    h, w = img.shape[:2]
    eff = {"variant": variant}
    if variant == "photocopy":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = np.clip((gray.astype(np.float32) - 255) * (1.1 + 0.15 * s) + 250, 0, 255).astype(np.uint8)
        pipe = AugraphyPipeline(
            ink_phase=[
                InkBleed(intensity_range=(0.1, 0.1 + 0.2 * s), kernel_size=(3, 3), severity=(0.2, 0.3))
            ],
            paper_phase=[],
            post_phase=[
                BadPhotoCopy(
                    noise_type=1,
                    noise_side="random",
                    noise_iteration=(1, 1),
                    noise_size=(1, 2),
                    noise_value=(int(60 + 80 * (1 - s)), int(120 + 60 * (1 - s))),
                    noise_sparsity=(0.6 + 0.2 * (1 - s), 0.8 + 0.1 * (1 - s)),
                    noise_concentration=(0.1 + 0.1 * s, 0.15 + 0.2 * s),
                    blur_noise=0,
                    wave_pattern=0,
                    edge_effect=0,
                ),
                LowInkPeriodicLines(count_range=(1, 2), period_range=(40, 80), noise_probability=0.05),
                DirtyDrum(
                    line_width_range=(1, 2),
                    line_concentration=0.015 * s,
                    direction=int(rng.integers(0, 2)),
                    noise_intensity=0.15 * s,
                    noise_value=(40, 110),
                    ksize=(3, 3),
                ),
            ],
        )
        out = pipe(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
        out = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY) if out.ndim == 3 else out
        band = int(rng.uniform(25, 60))  # dark copier edge
        side = rng.integers(0, 4)
        g = out.astype(np.float32)
        ramp = np.linspace(0.25, 1, band, dtype=np.float32)
        if side == 0:
            g[:, :band] *= ramp[None, :]
        elif side == 1:
            g[:, -band:] *= ramp[::-1][None, :]
        elif side == 2:
            g[:band, :] *= ramp[:, None]
        else:
            g[-band:, :] *= ramp[::-1][:, None]
        g = g * rng.uniform(0.86, 0.93) + rng.uniform(4, 12)  # grey copier paper
        out = cv2.GaussianBlur(g, (0, 0), 0.5 + 0.3 * s)
        ang = rng.uniform(-1.5, 1.5) * s
        scale, q = 0.8, 55
    elif variant == "fax":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # standard fax: ~200x100 dpi, black and white
        small = cv2.resize(
            gray, (int(w * 0.6), int(h * (0.42 + 0.18 * (1 - s)))), interpolation=cv2.INTER_AREA
        )
        pipe = AugraphyPipeline(
            ink_phase=[],
            paper_phase=[],
            post_phase=[
                Faxify(scale_range=(1.0, 1.0), monochrome=1, monochrome_method="threshold_otsu", halftone=0)
            ],
        )
        out = pipe(cv2.cvtColor(small, cv2.COLOR_GRAY2BGR))
        out = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY) if out.ndim == 3 else out
        out = cv2.resize(out, (int(w * 0.6), int(h * 0.6)), interpolation=cv2.INTER_NEAREST).astype(
            np.float32
        )
        sp = rng.random(out.shape)
        out[sp < 0.0015 * s] = 0  # line noise specks
        hdr = (
            f"{date_fmt((sent or date(2026, 1, 5)) + timedelta(days=int(rng.integers(0, 3))), 'abbr').upper()}  "
            f"{int(rng.integers(7, 18)):02d}:{int(rng.integers(0, 60)):02d}   FROM: 732 555 01{int(rng.integers(10, 99))}   P.01"
        )
        bar = Image.new("L", (out.shape[1], 40), 255)
        ImageDraw.Draw(bar).text((20, 8), hdr, font=font("mono", 22), fill=0)
        out = np.vstack([np.asarray(bar, np.float32), out])
        eff["header"] = hdr
        ang = rng.uniform(-1.0, 1.0) * s
        scale, q = 1.0, 60
    else:  # low-res scan
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gray = cv2.GaussianBlur(gray, (0, 0), 0.8 + 0.6 * s)
        out = gray * rng.uniform(0.92, 0.98) + 8
        ang = rng.uniform(-1.2, 1.2) * s
        scale, q = 0.5 + 0.1 * (1 - s), int(40 + 15 * (1 - s))
    hh, ww = out.shape[:2]
    M = cv2.getRotationMatrix2D((ww / 2, hh / 2), ang, 1.0)
    out = cv2.warpAffine(out, M, (ww, hh), borderValue=245 if variant != "fax" else 255)
    out = cv2.resize(out, (int(ww * scale), int(hh * scale)), interpolation=cv2.INTER_AREA)
    eff.update({"skew_deg": round(float(ang), 2), "scale": round(scale, 2), "jpeg": q})
    return np.clip(out, 0, 255).astype(np.uint8), q, eff


def fade_ink(img, rng, s):
    """Thermal fade: ink lightened (more at one end), slight yellow-grey paper, a few low-ink lines."""
    from augraphy import AugraphyPipeline, LowInkRandomLines

    h, w = img.shape[:2]
    g = img.astype(np.float32)
    ink_amt = 255 - g
    along = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    if rng.random() < 0.5:
        along = along[::-1]
    keep = (0.55 + 0.45 * (1 - s)) * (1 - (0.25 + 0.2 * s) * along)
    keep = keep * np.clip(1 + 0.12 * smooth_noise(rng, h, w, 90), 0.7, 1.15)
    gamma = 1.0 + 0.5 * s
    ink_amt = 255 * (ink_amt / 255) ** gamma * keep[..., None]
    paper = np.array([228, 238, 242], np.float32) - rng.uniform(0, 12)
    out = paper - ink_amt * (paper / 255)
    seed_globals(rng)
    out = AugraphyPipeline(
        ink_phase=[],
        paper_phase=[],
        post_phase=[LowInkRandomLines(count_range=(2, 4), noise_probability=0.05)],
    )(np.clip(out, 0, 255).astype(np.uint8))
    return out.astype(np.float32)


def curl_strip(img, rng, s):
    """Long strip that curls up at one end and bows sideways; returns warped image and page mask."""
    h, w = img.shape[:2]
    pad = int(0.08 * w)
    big = np.full((h, w + 2 * pad, 3), 0, np.float32)
    big[:, pad : pad + w] = img
    mask = np.zeros((h, w + 2 * pad), np.float32)
    mask[:, pad : pad + w] = 1
    H, W = big.shape[:2]
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    t = ys / H
    bow = (rng.uniform(0.03, 0.06) * s) * w * (t - 0.5) ** 2 * 4 * rng.choice([-1, 1])
    start = rng.uniform(0.78, 0.86)
    R = H * (0.12 + 0.1 * (1 - s))
    u = np.clip(ys - start * H, 0, None)
    # inverse map: destination y -> source y on a cylinder (compresses the curled end)
    src_y = np.where(ys > start * H, start * H + R * np.arcsin(np.clip(u / R, -1, 1)), ys)
    src_x = (xs - bow).astype(np.float32)
    out = cv2.remap(big, src_x, src_y.astype(np.float32), cv2.INTER_LINEAR, borderValue=0)
    m = cv2.remap(mask, src_x, src_y.astype(np.float32), cv2.INTER_LINEAR, borderValue=0)
    valid = (src_y < H - 1) & (u < R * 0.98)
    m = m * valid
    shade = np.where(ys > start * H, 1 - 0.35 * np.clip(u / R, 0, 1) ** 1.5, 1.0)
    out = out * shade[..., None]
    return out, m


def torn_edge(img, rng, paper_mask_side="top"):
    """Ragged perforation tear along one edge of an invoice-book page: returns page mask."""
    h, w = img.shape[:2]
    m = np.ones((h, w), np.float32)
    depth = rng.uniform(10, 22)
    if paper_mask_side == "top":
        prof = depth + np.cumsum(rng.normal(0, 1.2, w)).astype(np.float32) * 0.6
        prof = np.clip(prof - prof.min() + 4, 4, 40)
        for x in range(w):
            m[: int(prof[x]), x] = 0
            if x % 24 < 3:
                m[int(prof[x]) : int(prof[x]) + 6, x] = 0  # perforation holes
    else:
        prof = depth + np.cumsum(rng.normal(0, 1.2, h)).astype(np.float32) * 0.6
        prof = np.clip(prof - prof.min() + 4, 4, 40)
        for y in range(h):
            m[y, : int(prof[y])] = 0
    return cv2.GaussianBlur(m, (0, 0), 0.8)


# ----------------------------------------------------------------------------------------------
# Per-kind recipes
# ----------------------------------------------------------------------------------------------
def render_pages(t: dict) -> tuple[list[np.ndarray], list]:
    rng = random.Random(f"messy-draw-{MASTER_SEED}-{t['i']}")
    pages = LAYOUTS[t["layout"]](t, rng)
    return [flatten(p, rng, carbon=t["layout"] == "carbon") for p in pages], pages


def damage(t: dict, clean: list[np.ndarray], seed: int, s: float):
    """Apply the kind's damage to every page; returns [(uint8 image, jpeg quality)], effects record."""
    rng = np.random.default_rng(seed)
    kind = t["kind"]
    out, effects = [], []
    for pi, img in enumerate(clean):
        img = img.astype(np.float32)
        e = {"page": pi + 1}
        if kind == "crumpled_photo":
            folds = int(rng.integers(0, 3))
            hm = height_map(*img.shape[:2], rng, crumple=0.6 + 0.6 * s, folds=folds)
            disp = 10 + 15 * s
            img = bend_and_shade(img, hm, rng, disp=disp, shade_k=0.3 + 0.2 * s)
            res = camera(img, rng, s)
            e.update({"crumple": round(0.6 + 0.6 * s, 2), "folds": folds, "displace": round(disp, 1)})
        elif kind == "folded_coffee":
            hm = height_map(
                *img.shape[:2],
                rng,
                crumple=0.25 * s,
                creases=((1 / 3, 1), (2 / 3, -1)) if rng.random() < 0.7 else ((1 / 2, 1),),
            )
            img = bend_and_shade(img, hm, rng, disp=8 + 8 * s, shade_k=0.28 + 0.15 * s)
            if pi == 0 or rng.random() < 0.4:
                img, ring = coffee_ring(img, rng, s)
                e["coffee_ring"] = ring
            res = camera(img, rng, s * 0.8)
            e.update({"creases": "thirds"})
        elif kind == "photocopy_fax_scan":
            o, q, se = augraphy_scan(img, rng, s, t["scan"], sent=t["issued"])
            res = (o, q)
            e.update(se)
        elif kind == "carbon_copy":
            from augraphy import AugraphyPipeline, InkMottling

            seed_globals(rng)
            img = AugraphyPipeline(
                ink_phase=[InkMottling(ink_mottling_alpha_range=(0.1, 0.1 + 0.1 * s))],
                paper_phase=[],
                post_phase=[],
            )(img.astype(np.uint8)).astype(np.float32)
            pm = torn_edge(img, rng, rng.choice(["top", "left"]))
            if t["i"] % 2 == 0:
                hm = height_map(*img.shape[:2], rng, crumple=0.3 * s)
                img = bend_and_shade(img, hm, rng, disp=6 * s, shade_k=0.2)
                res = camera(img, rng, s * 0.7, page_mask=pm, scale=0.8)
                e.update({"step": "camera"})
            else:  # flatbed scan: white lid behind the torn edge
                img = img * pm[..., None] + 250 * (1 - pm[..., None])
                img = cv2.GaussianBlur(img, (0, 0), 0.7)
                ang = rng.uniform(-2, 2) * s
                M = cv2.getRotationMatrix2D((img.shape[1] / 2, img.shape[0] / 2), ang, 1.0)
                img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderValue=(250, 250, 250))
                img = np.clip(img + rng.normal(0, 2, img.shape), 0, 255)
                res = (img.astype(np.uint8), 70)
                e.update({"step": "flatbed", "skew_deg": round(float(ang), 2)})
        elif kind == "handwritten_fix":
            if t["i"] % 2 == 1 and t["pages"] == 1:
                o, q, se = augraphy_scan(img, rng, s * 0.6, "photocopy")
                res = (o, q)
                e.update({"step": "light photocopy", **se})
            else:
                hm = height_map(*img.shape[:2], rng, crumple=0.3 * s)
                img = bend_and_shade(img, hm, rng, disp=6 * s, shade_k=0.2)
                res = camera(img, rng, s * 0.6)
                e.update({"step": "light photo"})
        elif kind == "faded_receipt":
            img = fade_ink(img, rng, s)
            img, pm = curl_strip(img, rng, s)
            hm = height_map(*img.shape[:2], rng, crumple=0.2 * s)
            img = bend_and_shade(img, hm, rng, disp=4 * s, shade_k=0.15)
            res = camera(img, rng, s * 0.8, page_mask=pm, scale=rng.uniform(0.85, 0.95))
            e.update({"fade": round(s, 2), "curl": True})
        else:
            raise ValueError(kind)
        out.append(res)
        effects.append(e)
    return out, effects


def encode_jpeg(img: np.ndarray, q: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(q), cv2.IMWRITE_JPEG_OPTIMIZE, 1])
    assert ok
    return buf.tobytes()


def write_pdf(jpegs: list[bytes], sizes: list[tuple[int, int]], path: Path):
    """Minimal deterministic PDF: one JPEG (DCTDecode) per page, no timestamps."""
    objs = []
    n = len(jpegs)
    kids = " ".join(f"{3 + 3 * k} 0 R" for k in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for k, (jp, (w, h)) in enumerate(zip(jpegs, sizes)):
        pw = 612.0
        ph = round(pw * h / w, 2)
        img, cont = 4 + 3 * k, 5 + 3 * k
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw} {ph}] /Resources << /XObject << /Im{k} "
            f"{img} 0 R >> >> /Contents {cont} 0 R >>".encode()
        )
        gray = Image.open(io.BytesIO(jp)).mode == "L"
        objs.append(
            f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace "
            f"{'/DeviceGray' if gray else '/DeviceRGB'} /BitsPerComponent 8 /Filter /DCTDecode /Length "
            f"{len(jp)} >>\nstream\n".encode()
            + jp
            + b"\nendstream"
        )
        cs = f"q {pw} 0 0 {ph} 0 0 cm /Im{k} Do Q".encode()
        objs.append(f"<< /Length {len(cs)} >>\nstream\n".encode() + cs + b"\nendstream")
    body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for k, o in enumerate(objs, 1):
        offs.append(len(body))
        body += f"{k} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        body += f"{o:010d} 00000 n \n".encode()
    body += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(body))


def save_doc(t: dict, results, out_dir: Path) -> tuple[str, list[np.ndarray]]:
    jpegs = [encode_jpeg(img, q) for img, q in results]
    finals = [cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR) for j in jpegs]
    if len(jpegs) == 1:
        name = f"{t['id']}.jpg"
        (out_dir / name).write_bytes(jpegs[0])
    else:
        name = f"{t['id']}.pdf"
        write_pdf(jpegs, [(img.shape[1], img.shape[0]) for img, _ in results], out_dir / name)
    return name, finals


# ----------------------------------------------------------------------------------------------
# Readability gate
# ----------------------------------------------------------------------------------------------
FIELDS = [
    "contractor_name",
    "contractor_license",
    "contractor_email",
    "customer_name",
    "job_address",
    "work_type",
    "completion_date",
    "total_amount",
    "warranty_years",
]
CONFUSE = str.maketrans({"o": "0", "l": "1", "i": "1", "|": "1", "s": "5", "b": "8", "z": "2", "g": "9"})


def norm_ocr(s: str) -> str:
    s = s.lower().replace("$", "").replace(",", "")
    s = re.sub(r"\s+", "", s)
    return s.translate(CONFUSE)


def approx_find(pat: str, text: str, k: int) -> bool:
    """True if pat occurs in text with at most k edits (Sellers' algorithm)."""
    if not pat:
        return True
    if k == 0:
        return pat in text
    m = len(pat)
    prev = list(range(m + 1))
    for ch in text:
        cur = [0]
        for i in range(1, m + 1):
            cur.append(min(prev[i] + 1, cur[i - 1] + 1, prev[i - 1] + (pat[i - 1] != ch)))
        if cur[m] <= k:
            return True
        prev = cur
    return False


def tesseract_text(paths: list[Path]) -> str:
    out = []
    for p in paths:
        for psm in ("3", "6"):
            r = subprocess.run(["tesseract", str(p), "stdout", "--psm", psm], capture_output=True, text=True)
            out.append(r.stdout)
    return "\n".join(out)


def check_tesseract(text: str, pr: dict) -> dict:
    nt = norm_ocr(text)
    found = {}
    for f in FIELDS:
        pat = norm_ocr(pr[f])
        if f == "total_amount":
            found[f] = pat in nt
        else:
            found[f] = approx_find(pat, nt, 1 if len(pat) >= 8 else 0)
    return found


def rasterize(path: Path, tmp: Path) -> list[Path]:
    if path.suffix == ".jpg":
        return [path]
    stem = tmp / path.stem
    subprocess.run(["pdftoppm", "-r", "150", "-jpeg", str(path), str(stem)], check=True)
    return sorted(tmp.glob(path.stem + "-*.jpg"))


def codex_read(images: list[Path], prompt: str, tmp: Path) -> dict | None:
    if not shutil.which("codex"):
        return None
    outf = tmp / f"codex-{images[0].stem}.txt"
    cmd = [
        "codex",
        "exec",
        "-s",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "-C",
        str(tmp),
        "-o",
        str(outf),
        prompt,
    ]
    for im in images:
        cmd += ["-i", str(im)]
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
        txt = outf.read_text()
    except Exception as exc:  # noqa: BLE001 - a reader failure is just a failed read
        return {"_error": str(exc)}
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return {"_error": "no json", "_raw": txt[:300]}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"_error": "bad json", "_raw": txt[:300]}


INVOICE_PROMPT = (
    "These image(s) show one home-repair contractor invoice (all names and numbers are fictional test data; "
    "if there are several images they are pages of the same invoice). Read it and reply with ONLY one JSON "
    "object, no prose, with exactly these keys: contractor_name (the company that did the work), "
    "contractor_license (NJ HIC registration number exactly as written), contractor_email, customer_name, "
    'job_address (job-site street line only, e.g. "14 Osprey Ln" - no town or ZIP), work_type (one of: '
    "addition, deck, kitchen, bathroom, windows, roof, hvac, plumbing, electrical, other), completion_date "
    "(the date the work was finished, as YYYY-MM-DD), total_amount (the full invoice total including tax, as "
    "a number - not the balance due; if a printed total was crossed out and corrected by hand, use the "
    "corrected total), warranty_years (workmanship/labor warranty in years as an integer, or null if there is "
    "none). Use null for anything you cannot read."
)

SUFFIX = {
    "lane": "ln",
    "way": "way",
    "road": "rd",
    "court": "ct",
    "avenue": "ave",
    "place": "pl",
    "terrace": "ter",
    "drive": "dr",
    "circle": "cir",
    "street": "st",
}


def _n(s) -> str:
    return re.sub(r"[^a-z0-9@]", "", str(s or "").lower())


def _addr(s) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split()
    return " ".join(SUFFIX.get(w, w) for w in words)


def _date(s):
    try:
        return date.fromisoformat(str(s)[:10]).isoformat()
    except ValueError:
        return None


def check_hosted(ans: dict | None, exp: dict) -> dict | None:
    if not ans or "_error" in ans:
        return None

    def strip(x):
        return re.sub(r"\b(llc|inc|co)\b", "", re.sub(r"[^a-z0-9 ]", "", str(x or "").lower())).replace(
            " ", ""
        )

    def name_eq(a, b):
        return approx_find(strip(b), strip(a), 1) and abs(len(strip(a)) - len(strip(b))) <= 1

    def money_eq(a, b):
        try:
            return abs(float(str(a).replace("$", "").replace(",", "")) - b) < 0.01
        except ValueError:
            return False

    def int_eq(a, b):
        if b is None:
            return a in (None, "", "null", 0)
        try:
            return int(float(a)) == b
        except (TypeError, ValueError):
            return False

    return {
        "contractor_name": name_eq(ans.get("contractor_name"), exp["contractor_name"]),
        "contractor_license": _n(ans.get("contractor_license")) == _n(exp["contractor_license"]),
        "contractor_email": _n(ans.get("contractor_email")) == _n(exp["contractor_email"]),
        "customer_name": name_eq(ans.get("customer_name"), exp["customer_name"]),
        "job_address": _addr(ans.get("job_address")) == _addr(exp["job_address"]),
        "work_type": _n(ans.get("work_type")) == exp["work_type"],
        "completion_date": _date(ans.get("completion_date")) == exp["completion_date"],
        "total_amount": money_eq(ans.get("total_amount"), exp["total_amount"]),
        "warranty_years": int_eq(ans.get("warranty_years"), exp["warranty_years"]),
    }


def reader_pass(found: dict | None, need: int = 7) -> bool:
    return bool(found) and found["total_amount"] and sum(found.values()) >= need


def summarize(found):
    if found is None:
        return None
    return {
        "total": found["total_amount"],
        "fields_found": sum(found.values()),
        "missing": [k for k, v in found.items() if not v],
    }


def gate_one(t: dict, path: Path, tmp: Path, use_hosted: bool) -> dict:
    exp, pr = expected(t), printed(t)
    imgs = rasterize(path, tmp)
    tf = check_tesseract(tesseract_text(imgs), pr)
    hosted_ans = codex_read(imgs, INVOICE_PROMPT, tmp) if use_hosted else None
    hf = check_hosted(hosted_ans, exp)
    return {
        "tesseract": summarize(tf),
        "hosted": summarize(hf),
        "hosted_error": hosted_ans.get("_error") if hosted_ans and "_error" in hosted_ans else None,
        "hosted_answer": hosted_ans if hosted_ans and "_error" not in hosted_ans else None,
        "pass": reader_pass(tf) or reader_pass(hf),
    }


# ----------------------------------------------------------------------------------------------
# Contact sheet
# ----------------------------------------------------------------------------------------------
def contact_sheet(items, path: Path, cols=6, tw=300, th=390):
    """items: list of (label line 1, label line 2, [page images BGR])."""
    rows = math.ceil(len(items) / cols)
    sheet = Image.new("RGB", (cols * (tw + 16) + 16, rows * (th + 70) + 16), (236, 236, 236))
    d = ImageDraw.Draw(sheet)
    for k, (l1, l2, pages) in enumerate(items):
        x, y = 16 + (k % cols) * (tw + 16), 16 + (k // cols) * (th + 70)
        im = Image.fromarray(cv2.cvtColor(pages[0], cv2.COLOR_BGR2RGB) if pages[0].ndim == 3 else pages[0])
        if len(pages) > 1:  # show the page with the total
            im = Image.fromarray(
                cv2.cvtColor(pages[1], cv2.COLOR_BGR2RGB) if pages[1].ndim == 3 else pages[1]
            )
        im.thumbnail((tw, th))
        sheet.paste(im, (x + (tw - im.width) // 2, y + (th - im.height) // 2))
        d.text((x, y + th + 6), l1, font=font("sans_b", 20), fill=(20, 20, 20))
        d.text((x, y + th + 32), l2, font=font("sans", 17), fill=(70, 70, 70))
    sheet.save(path, optimize=True)


# ----------------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------------
def base_seed(i: int, regen: int = 0) -> int:
    return MASTER_SEED * 100 + i * 10 + regen


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gate", action="store_true", help="run the readability gate and rewrite gate.json")
    ap.add_argument("--no-hosted", action="store_true", help="gate with Tesseract only")
    ap.add_argument("--only", type=int, nargs="*", help="only these document numbers (debugging)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    truths = [make_truth(i) for i in range(1, 31)]
    if args.only:
        truths = [t for t in truths if t["i"] in args.only]
    gate_path = out_dir / "gate.json"
    gate = json.loads(gate_path.read_text()) if gate_path.exists() else {"docs": {}}

    clean = {t["id"]: render_pages(t)[0] for t in truths}
    params = {}
    for t in truths:
        g = gate["docs"].get(t["id"], {})
        fin = g.get("final") or {}
        params[t["id"]] = {"regen": fin.get("regen", 0), "level": fin.get("level", 0)}
    if args.gate:
        params = {t["id"]: {"regen": 0, "level": 0} for t in truths}

    finals, effects_rec = {}, {}

    def produce(t):
        pr = params[t["id"]]
        s = round(0.7 ** pr["level"], 4)
        res, eff = damage(t, clean[t["id"]], base_seed(t["i"], pr["regen"]), s)
        name, fin = save_doc(t, res, out_dir)
        finals[t["id"]] = fin
        effects_rec[t["id"]] = eff
        return name, s

    names = {}
    for t in truths:
        names[t["id"]], _ = produce(t)
        print(f"rendered {t['id']} {t['kind']}", file=sys.stderr)

    if args.gate:
        log = {t["id"]: [] for t in truths}
        pending = list(truths)
        tmp = Path(tempfile.mkdtemp(prefix="messy-gate-"))
        while pending:
            with ThreadPoolExecutor(max_workers=6) as ex:
                results = list(
                    ex.map(lambda t: gate_one(t, out_dir / names[t["id"]], tmp, not args.no_hosted), pending)
                )
            nxt = []
            for t, r in zip(pending, results):
                pr = params[t["id"]]
                r.update(
                    {
                        "regen": pr["regen"],
                        "level": pr["level"],
                        "seed": base_seed(t["i"], pr["regen"]),
                        "strength": round(0.7 ** pr["level"], 4),
                    }
                )
                log[t["id"]].append(r)
                print(
                    f"gate {t['id']} regen={pr['regen']} level={pr['level']} pass={r['pass']} "
                    f"tess={r['tesseract']} hosted={r['hosted']}",
                    file=sys.stderr,
                )
                if r["pass"]:
                    continue
                if pr["level"] < 3:
                    pr["level"] += 1
                elif pr["regen"] < 2:
                    pr["regen"], pr["level"] = pr["regen"] + 1, 0
                else:
                    continue  # rejected for good; kept out of the final set below
                nxt.append(t)
            for t in nxt:
                names[t["id"]], _ = produce(t)
            pending = nxt
        shutil.rmtree(tmp, ignore_errors=True)
        docs = {}
        for t in truths:
            atts = log[t["id"]]
            last = atts[-1]
            if not last["pass"]:
                outcome = "rejected"
            elif len(atts) == 1:
                outcome = "passed_first_try"
            elif last["regen"] > 0:
                outcome = "regenerated"
            else:
                outcome = "softened"
            docs[t["id"]] = {
                "kind": t["kind"],
                "file": names[t["id"]],
                "outcome": outcome,
                "final": {
                    "regen": last["regen"],
                    "level": last["level"],
                    "seed": last["seed"],
                    "strength": last["strength"],
                },
                "effects": effects_rec[t["id"]],
                "attempts": atts,
            }
        if args.only:
            gate["docs"].update(docs)
            docs = gate["docs"]
        counts = {}
        for d in docs.values():
            counts[d["outcome"]] = counts.get(d["outcome"], 0) + 1
        gate = {
            "rule": "keep if Tesseract OR the hosted reader (Codex CLI) finds the total and >= 7 of 9 fields; "
            "on failure soften x0.7 and retry up to 3 times, then regenerate with a new seed (max 2)",
            "hosted_reader": "codex exec (default model), read-only, prompt in make_messy.py INVOICE_PROMPT",
            "summary": counts,
            "docs": dict(sorted(docs.items())),
        }
        gate_path.write_text(json.dumps(gate, indent=1, default=str) + "\n")

    all_truth = [make_truth(i) for i in range(1, 31)]
    truth_out = []
    for t in all_truth:
        ext = "pdf" if t["pages"] > 1 else "jpg"
        truth_out.append(
            {
                "id": t["id"],
                "file": f"{t['id']}.{ext}",
                "kind": t["kind"],
                "expected": expected(t),
                "printed": printed(t),
                "pages": t["pages"],
                "layout": t["layout"],
            }
        )
        if gate["docs"].get(t["id"], {}).get("outcome") == "rejected":
            truth_out[-1]["readable"] = False  # even strong readers failed: score separately
    (out_dir / "truth.json").write_text(json.dumps(truth_out, indent=1) + "\n")
    if not args.only:
        items = [
            (
                f"{t['id']}  {t['kind']}",
                f"{t['layout']}{' / ' + t['scan'] if t['scan'] else ''}"
                f"{'  PDF %dp (p2 shown)' % t['pages'] if t['pages'] > 1 else ''}",
                finals[t["id"]],
            )
            for t in truths
        ]
        contact_sheet(items, out_dir / "contact_sheet.png")
    print(f"wrote {len(truths)} documents to {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
