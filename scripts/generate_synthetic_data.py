"""
Generate a reproducible, fully synthetic test town.

The original tool was built on real cadastral data that cannot be published,
so this script creates a fictional town with the same attribute schema:

* Buildings_To_Cover.shp  - ID_SWDE, LM (dwellings), LU (commercial units)
* Existing_Antennas.shp   - ID_SWDE, WYSOKOSC (height, m)
* Candidate_Antennas.shp  - ID_SWDE, WYSOKOSC, ILOSC_KOND (storeys), Adres

Geometry is in EPSG:2180 (Poland CS92, metres). Nothing here describes a real
place: IDs, addresses, heights and dwelling counts are all random.

Usage:  python scripts/generate_synthetic_data.py [--out data/sample] [--seed 42]
"""

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely import affinity
from shapely.geometry import box

CRS = "EPSG:2180"
ORIGIN = np.array([480_000.0, 350_000.0])  # arbitrary point, metres

# (name, centre offset [m], spread [m], n buildings, storeys range, footprint [m])
DISTRICTS = [
    ("Old Town",       (0, 0),         350, 320, (3, 6),  (14, 24)),
    ("North Estate",   (900, 1500),    300, 160, (4, 11), (40, 14)),
    ("East Estate",    (2300, 300),    320, 170, (4, 11), (45, 14)),
    ("Riverside",      (-1600, -900),  420, 260, (2, 5),  (20, 18)),
    ("Garden Suburb",  (-500, 1900),   650, 330, (1, 2),  (11, 10)),
    ("South Suburb",   (1200, -1700),  700, 300, (1, 2),  (12, 10)),
    ("Industrial",     (3300, -1400),  450, 90,  (1, 3),  (60, 40)),
    ("Hamlet",         (-3300, 1300),  250, 60,  (1, 2),  (10, 9)),
]
STREETS = ["Maple St", "Oak Ave", "River Rd", "Station St", "Market Sq", "Mill Ln",
           "Park Ave", "Church St", "School St", "Garden Rd", "Lake View", "Hill St"]


def make_buildings(rng: np.random.Generator) -> gpd.GeoDataFrame:
    rows = []
    n = 0
    for name, centre, spread, count, (smin, smax), (w, h) in DISTRICTS:
        c = ORIGIN + np.array(centre)
        pts = c + rng.normal(0, spread, size=(count, 2))
        angle = rng.uniform(0, 90)  # each district has its own street grid
        for x, y in pts:
            n += 1
            ww = w * rng.uniform(0.7, 1.3)
            hh = h * rng.uniform(0.7, 1.3)
            poly = affinity.rotate(box(x - ww / 2, y - hh / 2, x + ww / 2, y + hh / 2),
                                   angle + rng.normal(0, 5), origin="centroid")
            storeys = int(rng.integers(smin, smax + 1))
            height = round(storeys * 3.0 + rng.uniform(0.5, 2.5), 2)
            if name == "Industrial":
                lm, lu = 0, int(rng.integers(1, 6))
            elif storeys <= 2:
                lm, lu = int(rng.integers(1, 3)), int(rng.random() < 0.1)
            else:
                lm = int(storeys * rng.integers(2, 9))
                lu = int(rng.integers(0, 4)) if name == "Old Town" else int(rng.random() < 0.3)
            rows.append({
                "ID_SWDE": f"SYN.{n:05d}_BUD",
                "district": name,
                "ILOSC_KOND": storeys,
                "WYSOKOSC": height,
                "LM": lm,
                "LU": lu,
                "Adres": f"{STREETS[n % len(STREETS)]} {int(rng.integers(1, 120))}",
                "geometry": poly,
            })
    return gpd.GeoDataFrame(rows, crs=CRS)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--out", default="data/sample")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    b = make_buildings(rng)

    # buildings to cover = buildings with at least one dwelling / unit
    to_cover = b[(b["LM"] + b["LU"]) > 0][["ID_SWDE", "LM", "LU", "geometry"]]

    # candidate sites = buildings with >= 2 storeys (mounting height),
    # thinned out to mimic "only some roofs are available"
    cand = b[b["ILOSC_KOND"] >= 2]
    cand = cand.sample(frac=0.45, random_state=a.seed)
    candidates = cand[["ID_SWDE", "ILOSC_KOND", "WYSOKOSC", "Adres", "geometry"]]

    # existing antennas = 3 tall buildings, one per selected district
    existing = (b[b["district"].isin(["Old Town", "North Estate", "Riverside"])]
                .sort_values("WYSOKOSC", ascending=False)
                .groupby("district").head(1)[["ID_SWDE", "WYSOKOSC", "geometry"]])
    # an existing antenna site is not a candidate anymore
    candidates = candidates[~candidates["ID_SWDE"].isin(existing["ID_SWDE"])]

    to_cover.to_file(out / "Buildings_To_Cover.shp", encoding="utf-8")
    existing.to_file(out / "Existing_Antennas.shp", encoding="utf-8")
    candidates.to_file(out / "Candidate_Antennas.shp", encoding="utf-8")
    print(f"buildings to cover: {len(to_cover)}, candidates: {len(candidates)}, "
          f"existing: {len(existing)} -> {out}")


if __name__ == "__main__":
    main()
