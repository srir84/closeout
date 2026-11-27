"""Rebuild the Manasquan seed files from the state downloads (design 3.18, R-DATA-2).

  uv run python data/build_seed.py                       # download both sources
  uv run python data/build_seed.py --zip PARCELS.zip --permits PERMITS.json

Writes data/seed/parcels_1329.csv and data/seed/permits_1329.json, and prints the checksums that
go in data/SOURCES.md. Never keeps owner names, mailing addresses or sale prices.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
SEED = HERE / "seed"
PARCELS_URL = "https://geoapps.nj.gov/njgin/parcel/parcels_shp_dbf_Monmouth.zip"
PERMITS_URL = "https://data.nj.gov/resource/w9se-dmra.json?treasurycode=1329&$limit=5000&$order=pk"
TOWN = "1329"
# The only tax-list columns we keep. OWNER_NAME, ST_ADDRESS (owner mailing), CITY_STATE, ZIP_CODE,
# SALE_PRICE, DEED_* and the values are never read.
TAX_FIELDS = ["GIS_PIN", "PROP_LOC", "PROP_CLASS", "YR_CONSTR"]
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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url: str, to: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "closeout-demo-seed-builder"})
    with urllib.request.urlopen(req, timeout=600) as r, open(to, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return to


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Area centroid of a polygon ring (falls back to the average of its points)."""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1], strict=True):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-9:
        return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
    return cx / (3 * a), cy / (3 * a)


def build_parcels(zip_path: Path) -> list[dict[str, object]]:
    import shapefile  # pyshp (MIT)
    from pyproj import CRS, Transformer

    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)
        t = Path(tmp)
        crs = CRS.from_wkt((t / "MonmouthCountyParcels.prj").read_text())
        to_ll = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
        tax: dict[str, dict[str, object]] = {}
        with open(t / "MonmouthTaxList.dbf", "rb") as dbf:
            for rec in shapefile.Reader(dbf=dbf).iterRecords(fields=TAX_FIELDS):
                pin = str(rec["GIS_PIN"])
                if pin.startswith(TOWN + "_"):
                    tax[pin] = {
                        "address": str(rec["PROP_LOC"] or "").strip(),
                        "prop_class": str(rec["PROP_CLASS"] or "").strip(),
                        "year_built": int(rec["YR_CONSTR"])
                        if str(rec["YR_CONSTR"] or "").strip() not in ("", "0")
                        else "",
                    }
        out = []
        r = shapefile.Reader(str(t / "MonmouthCountyParcels"))
        for sr in r.iterShapeRecords(fields=["PAMS_PIN", "MUN", "BLOCK", "LOT", "QCODE"]):
            rec = sr.record
            if str(rec["MUN"]) != TOWN:
                continue
            pts = sr.shape.points
            lat = lon = ""
            if pts:
                end = sr.shape.parts[1] if len(sr.shape.parts) > 1 else len(pts)
                x, y = centroid([(float(p[0]), float(p[1])) for p in pts[:end]])
                lo, la = to_ll.transform(x, y)
                lat, lon = round(la, 6), round(lo, 6)
            pin = str(rec["PAMS_PIN"])
            t_row = tax.get(pin, {})
            out.append(
                {
                    "pin": pin,
                    "town": TOWN,
                    "block": str(rec["BLOCK"]).strip(),
                    "lot": str(rec["LOT"]).strip(),
                    "qualifier": str(rec["QCODE"] or "").strip(),
                    "address": t_row.get("address", ""),
                    "prop_class": t_row.get("prop_class", ""),
                    "year_built": t_row.get("year_built", ""),
                    "lat": lat,
                    "lon": lon,
                }
            )
    out.sort(key=lambda p: str(p["pin"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip")
    ap.add_argument("--permits")
    a = ap.parse_args()
    SEED.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(a.zip) if a.zip else download(PARCELS_URL, Path(tmp) / "parcels.zip")
        permits_path = Path(a.permits) if a.permits else download(PERMITS_URL, Path(tmp) / "permits.json")
        print("parcels zip sha256:", sha256(zip_path))
        print("permits download sha256:", sha256(permits_path))
        parcels = build_parcels(zip_path)
        with open(SEED / "parcels_1329.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=PARCEL_COLUMNS, lineterminator="\n")
            w.writeheader()
            w.writerows(parcels)
        permits = sorted(json.loads(permits_path.read_text()), key=lambda p: int(p["pk"]))
        (SEED / "permits_1329.json").write_text(
            "[\n" + ",\n".join(json.dumps(p, sort_keys=True) for p in permits) + "\n]\n"
        )
    print(f"parcels: {len(parcels)}, with an address: {sum(1 for p in parcels if p['address'])}")
    print(f"permits: {len(permits)}")
    for f in ("parcels_1329.csv", "permits_1329.json"):
        print(f"{f} sha256: {sha256(SEED / f)}")


if __name__ == "__main__":
    main()
