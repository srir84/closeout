"""Draw the demo's sample invoices from Sampleton's truth (data/seed/sampleton/world.json).

Writes data/demo/invoices/*.png, data/demo/harbor_pine_jobs.csv (Mike's import file) and
data/demo/truth.json. Everything is made up. Uses Pillow's built-in font so it runs anywhere.

  uv run python data/make_demo_invoices.py
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
OUT = HERE / "demo"
WORLD = HERE / "seed" / "sampleton" / "world.json"
WORK_LINES = {
    "kitchen": [
        "Demolition of existing kitchen",
        "Install cabinets and quartz countertops",
        "Plumbing and electrical hookups",
    ],
    "bathroom": [
        "Remove existing bathroom fixtures",
        "Tile shower with glass door",
        "New vanity, toilet and exhaust fan",
    ],
    "deck": [
        "Remove old deck boards and railings",
        "Build composite deck with railings",
        "Footings per approved plan",
    ],
    "windows": ["Replace double-hung windows", "Capping and caulking", "Haul away old units"],
    "roof": [
        "Tear off existing shingles",
        "Ice and water shield, underlayment",
        "Install architectural shingles and ridge vent",
    ],
    "hvac": [
        "Remove old furnace and air conditioner",
        "Install high-efficiency furnace and AC",
        "Thermostat, start-up and test",
    ],
    "addition": [
        "Frame and close in rear addition",
        "Windows, insulation and drywall",
        "Tie into existing roof",
    ],
    "plumbing": [
        "Replace leaking main water supply line",
        "New kitchen sink drain and shutoff valves",
        "Pressure test of supply piping",
    ],
    "electrical": [
        "Upgrade service panel to 200A",
        "Add new circuits and surge protection",
        "Utility coordination",
    ],
}
STREET_LONG = {"LN": "Lane", "WAY": "Way", "RD": "Road", "CT": "Court", "AVE": "Avenue", "PL": "Place"}


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def title_address(a: str) -> str:
    """'14 OSPREY LN' -> '14 Osprey Ln'."""
    return " ".join(w.capitalize() if not w[0].isdigit() else w for w in a.split())


def draw_invoice(job: dict, contractor: dict, layout: int, rng: random.Random) -> Image.Image:
    w, h = 1700, 2200
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    y = 90
    if layout == 0:
        d.rectangle((0, 0, w, 230), fill="#1f4e5f")
        d.text((100, 60), contractor["name"], font=font(64), fill="white")
        d.text(
            (100, 150),
            f"NJ HIC Reg. # {contractor['license']}   |   {contractor['email']}",
            font=font(32),
            fill="#dbe8ee",
        )
        y = 300
    else:
        d.text((w // 2, 80), contractor["name"], font=font(66), fill="#222", anchor="ma")
        d.text(
            (w // 2, 170),
            f"Registration No. {contractor['license']}  ~  {contractor['email']}",
            font=font(32),
            fill="#444",
            anchor="ma",
        )
        d.line((100, 240, w - 100, 240), fill="#222", width=3)
        y = 290
    finished = date.fromisoformat(job["finish"])
    issued = finished + timedelta(days=rng.randint(2, 9))
    d.text((100, y), "INVOICE", font=font(56), fill="#111")
    y += 90
    d.text(
        (100, y),
        f"Invoice # {rng.randint(1100, 9800)}     Invoice date: {issued.strftime('%B %-d, %Y')}",
        font=font(36),
        fill="#111",
    )
    y += 60
    d.text((100, y), f"Work completed: {finished.strftime('%B %-d, %Y')}", font=font(36), fill="#111")
    y += 100
    d.text((100, y), "BILL TO / JOB SITE", font=font(34), fill="#1f4e5f")
    y += 55
    for line in (job["name"], title_address(job["address"]), "Sampleton, NJ 08736"):
        d.text((100, y), line, font=font(38), fill="#111")
        y += 56
    y += 50
    d.line((100, y, w - 100, y), fill="#999", width=2)
    y += 40
    for line in WORK_LINES[job["work"]]:
        d.text((130, y), "-  " + line, font=font(36), fill="#111")
        y += 60
    y += 40
    d.line((100, y, w - 100, y), fill="#999", width=2)
    y += 50
    total = float(job["total"])
    d.text((900, y), "TOTAL", font=font(44), fill="#111")
    d.text((w - 120, y), f"${total:,.2f}", font=font(44), fill="#111", anchor="ra")
    y += 120
    if job.get("warranty"):
        d.text(
            (100, y),
            f"Workmanship warranty: {job['warranty']} years from completion.",
            font=font(32),
            fill="#333",
        )
    y += 80
    d.text((100, y), "Thank you for your business.", font=font(32), fill="#555")
    return img


def main() -> None:
    world = json.loads(WORLD.read_text())
    contractors = {c["key"]: c for c in world["contractors"]}
    people = {p["key"]: p for p in world["people"]}
    for c in contractors.values():
        c["email"] = people[c["person"]]["email"]
    rng = random.Random(9999)
    (OUT / "invoices").mkdir(parents=True, exist_ok=True)
    truth, rows = [], []
    for i, job in enumerate(world["jobs"], start=1):
        ref = f"HP-25-{i:03d}"
        k = contractors[job["contractor"]]
        img = draw_invoice(job, k, i % 2, rng)
        name = f"{ref}.png"
        img.save(OUT / "invoices" / name, optimize=True)
        rows.append(
            {
                "job_ref": ref,
                "homeowner_name": job["name"],
                "homeowner_email": job["email"],
                "job_address": title_address(job["address"]),
                "work_type": job["work"],
                "same_size_replacement": "",
                "detached_1_2_family": "",
                "finish_date": job["finish"],
                "total": job["total"],
                "invoice_file": name,
            }
        )
        truth.append(
            {
                "id": ref,
                "file": f"invoices/{name}",
                "persona": job["code"],
                "expected": {
                    "contractor_name": k["name"],
                    "contractor_license": k["license"],
                    "contractor_email": k["email"],
                    "customer_name": job["name"],
                    "job_address": title_address(job["address"]),
                    "work_type": job["work"],
                    "completion_date": job["finish"],
                    "total_amount": float(job["total"]),
                    "warranty_years": job["warranty"],
                },
            }
        )
    k1 = world["k1"]
    dana = people["homeowner:H1"]
    bay = contractors[k1["contractor"]]
    k1_job = {
        "name": dana["name"],
        "address": "14 OSPREY LN",
        "work": k1["work"],
        "finish": k1["finish"],
        "total": k1["total"],
        "warranty": k1["warranty"],
    }
    draw_invoice(k1_job, bay, 1, rng).save(OUT / "invoices" / "K1-bayside.png", optimize=True)
    truth.append(
        {
            "id": "K1-bayside",
            "file": "invoices/K1-bayside.png",
            "persona": "K1",
            "expected": {
                "contractor_name": bay["name"],
                "contractor_license": bay["license"],
                "contractor_email": bay["email"],
                "customer_name": dana["name"],
                "job_address": "14 Osprey Ln",
                "work_type": k1["work"],
                "completion_date": k1["finish"],
                "total_amount": float(k1["total"]),
                "warranty_years": k1["warranty"],
            },
        }
    )
    with open(OUT / "harbor_pine_jobs.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        wr.writeheader()
        wr.writerows(rows)
    (OUT / "truth.json").write_text(json.dumps(truth, indent=1) + "\n")
    print(f"wrote {len(truth)} invoices, {len(rows)} CSV rows")


if __name__ == "__main__":
    main()
