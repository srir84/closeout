#!/usr/bin/env python3
"""Make 30 made-up town clerk replies to a public records request about one construction permit (evals 4.2).

Letters come from "Borough of Sampleton - Office of the Municipal Clerk" (made up). Three result kinds,
10 each: final_on_file (final approval on file, with its date), no_record (no final approval or
inspection found) and unclear (scheduled inspection, partial approval, missing date, a different permit
number, a request for more information, an extension). tr01-tr10 are a balanced first slice.

Truth is decided first, the letter is drawn from it, then about 10 letters get mild messy-set damage
(fax, photocopy, phone photo; the recipes live in make_messy.py) and 4 become 2-page PDFs.
`--gate` runs Tesseract and the hosted reader (Codex CLI) and logs results in gate.json; damaged
letters that neither reader can read are softened and retried like the messy set.

Run (same throwaway environment as make_messy.py):

  uv run --python 3.12 --with augraphy==8.2.6 --with opencv-python-headless==5.1.0.95 \\
      --with numpy==2.5.5 --with pillow==13.0.0 python evals/generators/make_town_replies.py [--gate]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.dont_write_bytecode = True  # keep the repo free of __pycache__ from this import
sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_messy as mm  # noqa: E402  (shared fonts, handwriting and damage recipes)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "evals" / "sets" / "town_replies"
SEED = 20261128
W, H = mm.PAGE_W, mm.PAGE_H

FIRST10 = [
    "final_on_file",
    "no_record",
    "unclear",
    "final_on_file",
    "unclear",
    "no_record",
    "final_on_file",
    "no_record",
    "unclear",
    "final_on_file",
]
UNCLEAR_VARIANTS = [
    "scheduled",
    "partial",
    "missing_date",
    "other_permit",
    "more_info",
    "interim",
    "scheduled",
    "other_permit",
    "partial",
    "more_info",
]
NO_RECORD_VARIANTS = [
    "search_none",
    "open_no_final",
    "no_responsive",
    "search_none",
    "open_no_final",
    "no_responsive",
    "search_none",
    "open_no_final",
    "no_responsive",
    "search_none",
]
FINAL_VARIANTS = [
    "ca_issued",
    "final_passed",
    "table",
    "ca_issued",
    "final_passed",
    "table",
    "ca_issued",
    "co_issued",
    "final_passed",
    "table",
]
DAMAGE = {
    3: "fax",
    6: "photo",
    9: "photocopy",
    12: "fax",
    15: "photo",
    18: "photocopy",
    21: "fax",
    24: "photo",
    27: "photocopy",
    30: "fax",
}
TWO_PAGE = {5, 14, 22, 28}
CLERKS = [
    ("Maureen T. Kessler, RMC", "Municipal Clerk"),
    ("Daniel Okoye", "Deputy Municipal Clerk"),
    ("Patricia Lund", "Records Custodian"),
    ("Gwen Abernathy, RMC", "Acting Municipal Clerk"),
]
REQUESTERS = [
    ("Closeout Records Desk", "records@closeout.example"),
    ("Dana Whitfield", "dana.whitfield@mail.example"),
    ("Harbor & Pine Builders", "office@harborpine.example"),
    ("Inlet Roofing Co.", "estimates@inletroof.example"),
    ("Marcus Ellery", "m.ellery@mail.example"),
]
WORK = [
    "deck addition",
    "roof replacement",
    "kitchen alteration",
    "bathroom renovation",
    "water heater replacement",
    "electrical service upgrade",
    "window replacement",
    "sunroom addition",
    "furnace and AC replacement",
]


def dfmt(d: date, style: str) -> str:
    return {
        "long": f"{d:%B} {d.day}, {d.year}",
        "abbr": f"{d:%b}. {d.day}, {d.year}",
        "us": f"{d:%m/%d/%Y}",
        "eu": f"{d.day} {d:%B} {d.year}",
        "iso": d.isoformat(),
        "dash": f"{d:%m-%d-%Y}",
        "us2": f"{d.month}/{d.day}/{d:%y}",
    }[style]


def make_plan() -> list[dict]:
    rng = random.Random(f"town-replies-{SEED}")
    rest = ["final_on_file"] * 6 + ["no_record"] * 7 + ["unclear"] * 7
    rng.shuffle(rest)
    kinds = FIRST10 + rest
    counters = {"final_on_file": 0, "no_record": 0, "unclear": 0}
    used = {"22-0147", "23-0388", "21-0912"}
    docs = []
    for i, kind in enumerate(kinds, 1):
        r = random.Random(f"town-reply-{SEED}-{i}")
        if i <= 3:
            permit = ["22-0147", "23-0388", "21-0912"][i - 1]
        else:
            while True:
                permit = f"{r.randint(19, 25):02d}-{r.randint(12, 1390):04d}"
                if permit not in used:
                    used.add(permit)
                    break
        yr = 2000 + int(permit[:2])
        issued = date(yr, 1, 10) + timedelta(days=r.randint(0, 330))
        final = issued + timedelta(days=r.randint(40, 420))
        letter = date(2026, 3, 2) + timedelta(days=r.randint(0, 260))
        if final >= letter - timedelta(days=20):
            final = letter - timedelta(days=r.randint(30, 200))
        final = max(final, issued + timedelta(days=30))
        request = letter - timedelta(days=r.randint(3, 9))
        variant = {
            "final_on_file": FINAL_VARIANTS,
            "no_record": NO_RECORD_VARIANTS,
            "unclear": UNCLEAR_VARIANTS,
        }[kind][counters[kind]]
        counters[kind] += 1
        st = mm.STREETS[r.randrange(len(mm.STREETS))]
        num = r.randint(2, 248)
        other = permit[:3] + f"{(int(permit[3:]) + r.choice([27, 1, 90, 9])) % 10000:04d}"
        if other[3:] == permit[3:5] + permit[6] + permit[5]:
            other = permit[:3] + f"{(int(permit[3:]) + 3) % 10000:04d}"
        docs.append(
            {
                "i": i,
                "id": f"tr{i:02d}",
                "kind": kind,
                "variant": variant,
                "permit": permit,
                "other_permit": other if variant in ("other_permit", "more_info") else None,
                "address": f"{num} {st[0]} {st[1] if r.random() < 0.6 else st[2]}",
                "work": r.choice(WORK),
                "issued": issued,
                "final": final,
                "letter": letter,
                "request": request,
                "scheduled": letter + timedelta(days=r.randint(5, 30)),
                "opra_no": f"W-{letter.year}-{r.randint(100, 980):04d}",
                "style": ["centered", "seal_left", "band", "memo"][(i + r.randint(0, 3)) % 4],
                "letter_fmt": r.choice(["long", "abbr", "us", "eu"]),
                "date_fmt": r.choice(["long", "us", "abbr", "dash", "eu", "us2"]),
                "clerk": CLERKS[r.randrange(len(CLERKS))],
                "requester": REQUESTERS[r.randrange(len(REQUESTERS))],
                "damage": DAMAGE.get(i),
                "pages": 2 if i in TWO_PAGE else 1,
                "subcode": r.choice(["plumbing", "electrical", "fire"]),
            }
        )
    return docs


def expected(t: dict) -> dict:
    return {
        "permit_number": t["permit"],
        "result": t["kind"],
        "result_date": t["final"].isoformat() if t["kind"] == "final_on_file" else None,
    }


def result_paragraphs(t: dict) -> tuple[list[str], list[tuple[str, str]] | None]:
    """Body text for the result, plus an optional record table (label, value)."""
    p, a = t["permit"], t["address"]
    fd = dfmt(t["final"], t["date_fmt"])
    iss = dfmt(t["issued"], t["date_fmt"])
    v = t["variant"]
    table = None
    if v == "ca_issued":
        paras = [
            f"Our records show that a Certificate of Approval was issued for Construction Permit No. {p} "
            f"({t['work']}) at {a} on {fd}. A copy of the Certificate of Approval is on file with the "
            f"Construction Office and is enclosed."
        ]
    elif v == "final_passed":
        paras = [
            f"Permit No. {p} for the {t['work']} at {a} was issued on {iss}. The final inspection was "
            f"passed on {fd}, and the final approval is on file. No further inspections are required "
            f"under this permit."
        ]
    elif v == "co_issued":
        paras = [
            f"Construction Permit No. {p} ({t['work']}, {a}) is closed. A Certificate of Occupancy was "
            f"issued on {fd} after the final inspection was approved. The certificate is on file in this "
            f"office."
        ]
    elif v == "table":
        paras = [
            "The Construction Office record for the permit you asked about is summarized below. The "
            "permit is closed and its final approval is on file."
        ]
        table = [
            ("Permit No.", p),
            ("Location", f"{a}, Sampleton"),
            ("Description", t["work"].capitalize()),
            ("Permit issued", iss),
            ("Final inspection", f"APPROVED  {fd}"),
            ("Certificate of Approval", "Issued (same date)"),
            ("Status", "CLOSED"),
        ]
    elif v == "search_none":
        paras = [
            f"A search of the records of the Construction Office found no record of a final inspection "
            f"or a Certificate of Approval for Construction Permit No. {p} at {a}."
        ]
    elif v == "open_no_final":
        paras = [
            f"Permit No. {p} ({t['work']}) was issued on {iss}. There is no final inspection on record "
            f"for this permit and no final approval has been issued. The permit remains open."
        ]
        table = (
            [
                ("Permit No.", p),
                ("Location", f"{a}, Sampleton"),
                ("Permit issued", iss),
                ("Final inspection", "NONE ON RECORD"),
                ("Certificate of Approval", "NOT ISSUED"),
                ("Status", "OPEN"),
            ]
            if t["i"] % 2 == 0
            else None
        )
    elif v == "no_responsive":
        paras = [
            f"No responsive records exist. The Borough has no Certificate of Approval and no final "
            f"inspection record on file for Permit No. {p}, {a}."
        ]
    elif v == "scheduled":
        paras = [
            f"Regarding Permit No. {p} at {a}: a final inspection has been scheduled for "
            f"{dfmt(t['scheduled'], t['date_fmt'])}. The outcome will be entered in the permit record "
            f"after the inspector's visit."
        ]
    elif v == "partial":
        paras = [
            f"For Permit No. {p} ({a}), the building subcode final inspection was approved on {fd}. The "
            f"{t['subcode']} subcode final inspection is still outstanding. A Certificate of Approval "
            f"will be issued once all subcodes are approved."
        ]
    elif v == "missing_date":
        paras = [
            f"Our records indicate that a Certificate of Approval was issued under Permit No. {p} for "
            f"{a}. The date of issue is not shown in the file copy available to this office."
        ]
    elif v == "other_permit":
        paras = [
            f"Enclosed please find the Certificate of Approval for Construction Permit No. "
            f"{t['other_permit']}, issued {fd}, located at {a}."
        ]
    elif v == "more_info":
        paras = [
            f"We could not locate Permit No. {p} as written. Our index shows Permit No. "
            f"{t['other_permit']} at a similar address. Please confirm the permit number, or provide the "
            f"block and lot and the owner's name at the time of the work, so that we can complete the "
            f"search."
        ]
    elif v == "interim":
        paras = [
            f"This office is still gathering the records for Permit No. {p} at {a}. Some files are held "
            f"in off-site storage. Under N.J.S.A. 47:1A-5(i) we are extending the response time by seven "
            f"business days and will reply by {dfmt(t['scheduled'], t['date_fmt'])}."
        ]
    else:
        raise ValueError(v)
    return paras, table


BOILER = [
    "If you believe access to a record has been improperly denied, you may file a complaint with the "
    "Government Records Council or in Superior Court.",
    "Please contact this office if you have any questions about this response.",
]


# ----------------------------------------------------------------------------------------------
# Drawing
# ----------------------------------------------------------------------------------------------
def seal(p: mm.Page, cx: int, cy: int, r: int, color):
    d = p.d
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=4)
    d.ellipse((cx - r + 16, cy - r + 16, cx + r - 16, cy + r - 16), outline=color, width=2)
    text = "BOROUGH OF SAMPLETON * NEW JERSEY * "
    f = mm.font("serif_b", int(r * 0.17))
    for k, ch in enumerate(text):
        ang = -math.pi / 2 + 2 * math.pi * k / len(text)
        x, y = cx + (r - 30) * math.cos(ang), cy + (r - 30) * math.sin(ang)
        tile = Image.new("L", (60, 60), 0)
        ImageDraw.Draw(tile).text((30, 30), ch, font=f, fill=255, anchor="mm")
        tile = tile.rotate(-math.degrees(ang) - 90, resample=Image.BICUBIC)
        p.img.paste(Image.new("RGB", (60, 60), color), (int(x - 30), int(y - 30)), tile)
    d.text((cx, cy - 10), "1897", font=mm.font("serif_b", int(r * 0.3)), fill=color, anchor="mm")
    d.text((cx, cy + r * 0.3), "INC.", font=mm.font("serif", int(r * 0.15)), fill=color, anchor="mm")


def letterhead(p: mm.Page, t: dict, rng: random.Random, small=False):
    style = t["style"]
    navy = (25, 45, 90)
    addr = "Borough Hall, 1 Plaza Way, Sampleton, NJ 08736  |  (732) 555-0100  |  clerk@sampleton-nj.example"
    if small:
        p.y = 90
        p.text(
            110,
            f"Borough of Sampleton - Office of the Municipal Clerk - OPRA {t['opra_no']} - page 2",
            "serif",
            28,
            fill=navy,
            dy=50,
        )
        p.rule(w=1, fill=navy, gap=40)
        return
    if style == "centered":
        seal(p, W // 2, 190, 100, navy)
        p.y = 310
        p.text(W // 2, "BOROUGH OF SAMPLETON", "serif_b", 58, fill=navy, anchor="ma", dy=74)
        p.text(W // 2, "Office of the Municipal Clerk", "serif", 38, fill=navy, anchor="ma", dy=52)
        p.text(W // 2, addr, "serif", 24, fill=(60, 60, 60), anchor="ma", dy=44)
        p.rule(w=2, fill=navy, gap=60)
    elif style == "seal_left":
        seal(p, 230, 210, 110, navy)
        p.y = 120
        p.text(390, "Borough of Sampleton", "serif_b", 62, fill=navy, dy=78)
        p.text(390, "Office of the Municipal Clerk", "serif", 40, fill=navy, dy=56)
        p.text(
            390,
            "Borough Hall, 1 Plaza Way, Sampleton, New Jersey 08736",
            "serif",
            26,
            fill=(70, 70, 70),
            dy=36,
        )
        p.text(
            390, "Tel. (732) 555-0100  ~  clerk@sampleton-nj.example", "serif", 26, fill=(70, 70, 70), dy=60
        )
        p.y = max(p.y, 360)
        p.rule(w=3, fill=navy, gap=8)
        p.rule(w=1, fill=navy, gap=60)
    elif style == "band":
        p.d.rectangle((0, 0, W, 230), fill=(32, 70, 60))
        p.y = 60
        p.text(110, "BOROUGH OF SAMPLETON", "sans_b", 60, fill="white", dy=76)
        p.text(
            110, "Office of the Municipal Clerk  -  Public Records (OPRA)", "sans", 34, fill=(220, 235, 228)
        )
        p.y = 260
        p.text(110, addr, "sans", 24, fill=(70, 70, 70), dy=70)
    else:  # memo / typewriter
        p.y = 120
        p.text(110, "BOROUGH OF SAMPLETON", "mono_b", 46, dy=56)
        p.text(110, "OFFICE OF THE MUNICIPAL CLERK", "mono_b", 34, dy=46)
        p.text(110, "1 Plaza Way, Sampleton NJ 08736   (732) 555-0100", "mono", 28, dy=70)
        p.text(110, "RESPONSE TO OPEN PUBLIC RECORDS ACT REQUEST", "mono_b", 32, dy=60)


def body_font(t):
    return {
        "centered": ("serif", "serif_b"),
        "seal_left": ("serif", "serif_b"),
        "band": ("sans", "sans_b"),
        "memo": ("mono", "mono_b"),
    }[t["style"]]


def para(p: mm.Page, text: str, key: str, size=34, x=110, gap=26):
    for ln in mm.wrap(text, key, size, W - x - 110):
        p.text(x, ln, key, size, dy=int(size * 1.45))
    p.y += gap


def draw_table(p: mm.Page, rows, key, key_b, size=32):
    x0, x1, xm = 150, W - 150, 640
    p.d.rectangle((x0, p.y, x1, p.y + 8 + len(rows) * (size + 30)), outline="black", width=2)
    for k, (lab, val) in enumerate(rows):
        y = p.y + 10 + k * (size + 30)
        if k:
            p.d.line((x0, y - 6, x1, y - 6), fill=(150, 150, 150), width=1)
        p.d.text((x0 + 20, y + 8), lab, font=mm.font(key_b, size - 2), fill="black")
        p.d.text((xm, y + 8), val, font=mm.font(key, size), fill="black")
    p.d.line((xm - 20, p.y, xm - 20, p.y + 8 + len(rows) * (size + 30)), fill=(150, 150, 150), width=1)
    p.y += 8 + len(rows) * (size + 30) + 40


def sign_block(p: mm.Page, t, rng, key):
    p.text(110, "Sincerely," if rng.random() < 0.6 else "Very truly yours,", key, 34, dy=60)
    name, title = t["clerk"]
    mm.signature(p.hand, 120, p.y + 40, name.split(",")[0], rng, size=58)
    p.y += 90
    p.text(110, name, key, 34)
    p.text(110, title, key, 34)
    p.text(110, "Borough of Sampleton", key, 34, dy=60)
    if rng.random() < 0.5:
        p.text(110, "cc: Construction Official", key, 28)


def render_letter(t: dict) -> list[np.ndarray]:
    rng = random.Random(f"town-draw-{SEED}-{t['i']}")
    key, key_b = body_font(t)
    paper = (255, 255, 255) if t["style"] != "memo" else (250, 248, 240)
    p1 = mm.Page(bg=paper)
    letterhead(p1, t, rng)
    p1.text(110, dfmt(t["letter"], t["letter_fmt"]), key, 34, dy=70)
    req_name, req_email = t["requester"]
    p1.text(110, req_name, key, 34)
    p1.text(110, f"Via email: {req_email}", key, 34, dy=70)
    re_line = f"Re: OPRA Request {t['opra_no']} - Construction Permit No. {t['permit']}, {t['address']}"
    for ln in mm.wrap(re_line, key_b, 34, W - 220):
        p1.text(110, ln, key_b, 34, dy=50)
    p1.y += 30
    p1.text(110, "Dear Requester:" if rng.random() < 0.5 else f"Dear {req_name}:", key, 34, dy=64)
    para(
        p1,
        f"This office received your request on {dfmt(t['request'], t['letter_fmt'])} for records showing "
        f"the final inspection or final approval of the construction permit named above.",
        key,
    )
    paras, table = result_paragraphs(t)
    pages = [p1]
    if t["pages"] == 2:
        para(p1, "Our response continues on the next page.", key)
        p2 = mm.Page(bg=paper)
        letterhead(p2, t, rng, small=True)
        target = p2
        pages.append(p2)
    else:
        target = p1
    for s in paras:
        para(target, s, key)
    if table:
        draw_table(target, table, key, key_b)
    for s in BOILER:
        para(target, s, key, size=30)
    sign_block(target, t, rng, key)
    if t["variant"] == "missing_date" and t["i"] % 2:  # blank date field on an attached stamp
        target.y += 30
        target.d.rectangle((110, target.y, 800, target.y + 110), outline=(150, 30, 30), width=3)
        target.d.text(
            (130, target.y + 15),
            "CERTIFICATE OF APPROVAL ON FILE",
            font=mm.font("cond_b", 30),
            fill=(150, 30, 30),
        )
        target.d.text(
            (130, target.y + 60), "Date: ____________", font=mm.font("cond_b", 30), fill=(150, 30, 30)
        )
    return [mm.flatten(pg, rng) for pg in pages]


def damage(t: dict, clean: list[np.ndarray], seed: int, s: float):
    """Mild messy-set damage: s starts at 0.6 (milder than the invoice set) and softens on gate failure."""
    rng = np.random.default_rng(seed)
    out = []
    for img in clean:
        kind = t["damage"]
        if kind is None:
            out.append((img, 85))
        elif kind in ("fax", "photocopy"):
            o, q, _ = mm.augraphy_scan(img.astype(np.float32), rng, s, kind, sent=t["letter"])
            out.append((o, q))
        else:
            hm = mm.height_map(*img.shape[:2], rng, crumple=0.35 * s, folds=1)
            im = mm.bend_and_shade(img.astype(np.float32), hm, rng, disp=8 * s, shade_k=0.25)
            out.append(mm.camera(im, rng, s))
    return out


# ----------------------------------------------------------------------------------------------
# Gate
# ----------------------------------------------------------------------------------------------
PROMPT = (
    "The image(s) show one letter from a town clerk replying to a public records request about one "
    "construction permit (all names and numbers are fictional test data; several images are pages of the same "
    "letter). Reply with ONLY one JSON object, no prose, with keys: permit_number (the permit number the "
    'request is about, format NN-NNNN), result ("final_on_file" if the letter clearly says the final '
    "approval - certificate of approval/occupancy or passed final inspection - for THAT permit is on file and "
    'gives its date; "no_record" if it clearly says no final approval or final inspection record exists for '
    'that permit; otherwise "unclear" - e.g. inspection only scheduled, partial approval, missing date, a '
    "different permit number, a request for more information, an extension), result_date (the final approval "
    "date as YYYY-MM-DD when result is final_on_file, else null)."
)


def gate_one(t, path: Path, tmp: Path, use_hosted: bool) -> dict:
    exp = expected(t)
    imgs = mm.rasterize(path, tmp)
    text = mm.tesseract_text(imgs)
    flat = re.sub(r"\s+", "", text)
    tess = {"permit_number": t["permit"] in flat}
    if exp["result_date"]:
        tess["result_date"] = mm.approx_find(
            mm.norm_ocr(dfmt(t["final"], t["date_fmt"])), mm.norm_ocr(text), 0
        )
    ans = mm.codex_read(imgs, PROMPT, tmp) if use_hosted else None
    hosted = None
    if ans and "_error" not in ans:
        hosted = {
            "permit_number": str(ans.get("permit_number") or "").strip() == t["permit"],
            "result": ans.get("result") == exp["result"],
        }
        if exp["result_date"]:
            hosted["result_date"] = str(ans.get("result_date") or "")[:10] == exp["result_date"]
    tess_ok = all(tess.values())
    # Readability: a reader finds the permit number (and the approval date when there is one).
    hosted_ok = bool(hosted) and hosted["permit_number"] and hosted.get("result_date", True)
    return {
        "tesseract": tess,
        "hosted": hosted,
        "hosted_answer": ans if ans and "_error" not in ans else None,
        "hosted_error": ans.get("_error") if ans and "_error" in ans else None,
        "pass": tess_ok or hosted_ok,
    }


def save(t, results, out_dir: Path):
    jpegs = [mm.encode_jpeg(img, q) for img, q in results]
    finals = [cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR) for j in jpegs]
    if len(jpegs) == 1:
        name = f"{t['id']}.jpg"
        (out_dir / name).write_bytes(jpegs[0])
    else:
        name = f"{t['id']}.pdf"
        mm.write_pdf(jpegs, [(img.shape[1], img.shape[0]) for img, _ in results], out_dir / name)
    return name, finals


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--no-hosted", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = make_plan()
    gate_path = out_dir / "gate.json"
    gate = json.loads(gate_path.read_text()) if gate_path.exists() else {"docs": {}}
    level = {
        t["id"]: 0 if args.gate else (gate["docs"].get(t["id"], {}).get("final") or {}).get("level", 0)
        for t in plan
    }
    names, finals = {}, {}

    def produce(t):
        s = round(0.6 * 0.7 ** level[t["id"]], 4)
        res = damage(t, render_letter(t), SEED * 100 + t["i"], s)
        names[t["id"]], finals[t["id"]] = save(t, res, out_dir)

    for t in plan:
        produce(t)
        print(f"rendered {t['id']} {t['kind']} {t['variant']} {t['damage'] or ''}", file=sys.stderr)

    if args.gate:
        log = {t["id"]: [] for t in plan}
        pending = list(plan)
        tmp = Path(tempfile.mkdtemp(prefix="town-gate-"))
        while pending:
            with ThreadPoolExecutor(max_workers=6) as ex:
                res = list(
                    ex.map(lambda t: gate_one(t, out_dir / names[t["id"]], tmp, not args.no_hosted), pending)
                )
            nxt = []
            for t, r in zip(pending, res):
                r["level"] = level[t["id"]]
                r["strength"] = round(0.6 * 0.7 ** level[t["id"]], 4) if t["damage"] else None
                log[t["id"]].append(r)
                print(
                    f"gate {t['id']} {t['kind']:13s} {t['variant']:13s} pass={r['pass']} tess={r['tesseract']} "
                    f"hosted={r['hosted']}",
                    file=sys.stderr,
                )
                if not r["pass"] and t["damage"] and level[t["id"]] < 3:
                    level[t["id"]] += 1
                    nxt.append(t)
            for t in nxt:
                produce(t)
            pending = nxt
        shutil.rmtree(tmp, ignore_errors=True)
        docs = {}
        for t in plan:
            atts = log[t["id"]]
            last = atts[-1]
            outcome = (
                "passed_first_try"
                if len(atts) == 1 and last["pass"]
                else "softened"
                if last["pass"]
                else "rejected"
            )
            docs[t["id"]] = {
                "kind": t["kind"],
                "variant": t["variant"],
                "damage": t["damage"],
                "file": names[t["id"]],
                "outcome": outcome,
                "hosted_result_agrees": bool(last["hosted"] and last["hosted"]["result"]),
                "final": {"level": last["level"], "strength": last["strength"]},
                "attempts": atts,
            }
        counts = {}
        for d in docs.values():
            counts[d["outcome"]] = counts.get(d["outcome"], 0) + 1
        gate = {
            "rule": "keep if Tesseract OR the hosted reader (Codex CLI) finds the permit number and, for "
            "final_on_file, the approval date; damaged letters that fail are softened x0.7, up to 3 "
            "times. hosted_result_agrees records whether the hosted reader's result kind matched truth "
            "(not gated, so hard 'unclear' letters stay in).",
            "summary": counts,
            "hosted_result_agreement": sum(d["hosted_result_agrees"] for d in docs.values()),
            "docs": docs,
        }
        gate_path.write_text(json.dumps(gate, indent=1, default=str) + "\n")

    truth = []
    for t in plan:
        e = {
            "id": t["id"],
            "file": f"{t['id']}.{'pdf' if t['pages'] > 1 else 'jpg'}",
            "expected": expected(t),
            "first10": t["i"] <= 10,
            "variant": t["variant"],
            "damage": t["damage"],
            "pages": t["pages"],
            "address": t["address"],
        }
        if t["other_permit"]:
            e["other_permit_in_letter"] = t["other_permit"]
        truth.append(e)
    (out_dir / "truth.json").write_text(json.dumps(truth, indent=1) + "\n")
    items = [
        (
            f"{t['id']}  {t['kind']}",
            f"{t['variant']}{' / ' + t['damage'] if t['damage'] else ''}"
            f"{'  PDF 2p (p2 shown)' if t['pages'] > 1 else ''}",
            finals[t["id"]],
        )
        for t in plan
    ]
    mm.contact_sheet(items, out_dir / "contact_sheet.png")
    print(f"wrote {len(plan)} letters to {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
