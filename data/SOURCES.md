# Data sources

Real public data for Manasquan, NJ (municipality code 1329). Rebuilt with `uv run python data/build_seed.py`.

| File | Source | Downloaded | sha256 |
|---|---|---|---|
| `seed/parcels_1329.csv` | NJGIN parcels for Monmouth County: `https://geoapps.nj.gov/njgin/parcel/parcels_shp_dbf_Monmouth.zip` (zip sha256 `c709d9b07cbc4a8c35a614ca7e741aa8ee1d13eb0f724828dba9b2e0cba9eb7a`). The zip holds the parcel map (`MonmouthCountyParcels.*`) and the county tax list (`MonmouthTaxList.dbf`). | 27 Nov 2026 | `2aef80ea0a1b84e91dce615d02ce337618cb88ab52394d53c3059d14ba9e7587` |
| `seed/permits_1329.json` | NJ Department of Community Affairs construction permits on data.nj.gov, dataset `w9se-dmra`, `treasurycode=1329`, ordered by `pk` | 27 Nov 2026 | `587d6b5b6ae9320d8668e2a17e8abb0e91580b4c0122737a2e08bfb4d616e179` |

**Field mapping (parcels).** From the parcel map: `PAMS_PIN` → `pin`, `BLOCK`, `LOT`, `QCODE` → `qualifier`. From the tax list, joined on `GIS_PIN` = `PAMS_PIN`: `PROP_LOC` → `address`, `PROP_CLASS` → `prop_class`, `YR_CONSTR` → `year_built`. The map point is the centroid of each parcel's first ring.

**Never kept:** owner name, owner mailing address, sale price, deed details and assessed values. The builder reads only the columns above.

**Coordinate system.** The parcel map is NAD 1983 State Plane New Jersey, US feet (EPSG:3424, read from the `.prj`). Converted to latitude and longitude (WGS 84) with `pyproj`.

**Counts on this snapshot** (checked by tests): 3,982 parcels, 3,362 with a street address; 2,914 permits; by block and lot, 2,712 link to one parcel, 82 to several and 120 to none (agreeing row by row with `tools/join_check.py`); 2,706 permits get exactly one street address; 280 permits have no trade fees.

**Credits and terms**
- Construction permits: New Jersey Department of Community Affairs, via data.nj.gov. Public domain.
- Parcels and tax list: New Jersey Office of Information Technology, Office of GIS (NJOGIS). Credit requested.

## The made-up town

`seed/sampleton/` is made by `data/make_sampleton.py` (seed 9999): Sampleton, code 9999. Every person, home, company and permit in it is made up. No street name or address in it exists in Manasquan (checked when the script runs and by a test).
