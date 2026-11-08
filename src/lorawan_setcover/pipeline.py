"""
GeoPandas implementation of the LoRaWAN antenna placement workflow.

It mirrors the QGIS script in ``qgis/`` step by step, but runs headless
(no QGIS needed), which makes it easy to test, script and run in CI.

Workflow
--------
1. Load buildings to cover, existing antennas and candidate antenna sites
   (all as building footprints, projected CRS in metres).
2. Convert footprints to centroids.
3. Buffer existing antennas and drop buildings already in range.
4. Buffer every candidate site (constant radius or an expression such as
   ``400 + 10 * WYSOKOSC`` - higher mounting point, longer range).
5. Spatial join -> coverage table ``(building_id, antenna_id)``.
6. Solve Set Cover (min antennas) or Maximum Coverage (budget of k antennas).
7. Post-process: flag redundant antennas (>= 75 % of their range overlaps
   other selected antennas) and low-value antennas (few buildings/dwellings).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.ops import unary_union

from .solver import CoverResult, solve_max_coverage, solve_min_cover


@dataclass
class Config:
    buildings_path: str | Path
    existing_path: str | Path
    candidates_path: str | Path
    radius_existing: str = "500"
    radius_candidates: str = "500"
    id_col: str = "ID_SWDE"
    lm_col: str = "LM"
    lu_col: str = "LU"
    use_weights: bool = False
    max_antennas: int = 0            # 0 = minimise number of antennas
    redundancy_threshold: float = 0.75
    low_value_buildings: int = 5     # used when use_weights is False
    low_value_dwellings: int = 30    # used when use_weights is True
    timeout: int = 300


@dataclass
class Result:
    buildings: gpd.GeoDataFrame          # all building centroids
    to_cover: gpd.GeoDataFrame           # centroids outside existing coverage
    out_of_range: gpd.GeoDataFrame       # not reachable by any candidate
    existing_buffers: gpd.GeoDataFrame
    candidate_buffers: gpd.GeoDataFrame  # with 'category' column for selected sites
    pairs: pd.DataFrame
    solution: CoverResult
    redundant: list[str]
    low_value: list[str]

    @property
    def essential(self) -> list[str]:
        skip = set(self.redundant) | set(self.low_value)
        return [a for a in self.solution.selected if a not in skip]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _radius(gdf: gpd.GeoDataFrame, expr: str) -> pd.Series:
    """Constant radius ('500') or a column expression ('400+10*"WYSOKOSC"')."""
    expr = str(expr).replace('"', "").strip()
    try:
        return pd.Series(float(expr), index=gdf.index)
    except ValueError:
        return pd.to_numeric(gdf.eval(expr), errors="coerce").fillna(0.0)


def _load(path: str | Path, id_col: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None or not gdf.crs.is_projected:
        raise ValueError(f"{path}: data must be in a projected (metric) CRS, got {gdf.crs}")
    if id_col not in gdf.columns:
        raise KeyError(f"{path}: missing ID column '{id_col}'")
    gdf[id_col] = gdf[id_col].astype(str)
    return gdf


def _centroids(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = gdf.copy()
    out["geometry"] = gdf.geometry.centroid
    return out


def _buffers(centroids: gpd.GeoDataFrame, expr: str) -> gpd.GeoDataFrame:
    out = centroids.copy()
    out["radius_m"] = _radius(centroids, expr)
    # shapely.buffer directly: GeoSeries.buffer() takes `resolution` in GeoPandas 1.0
    # but `quad_segs` in newer versions - this works on both.
    out["geometry"] = gpd.GeoSeries(
        shapely.buffer(centroids.geometry.values, out["radius_m"].to_numpy(), quad_segs=16),
        index=centroids.index, crs=centroids.crs,
    )
    return out


def _weights(gdf: gpd.GeoDataFrame, cfg: Config) -> dict[str, int]:
    lm = pd.to_numeric(gdf.get(cfg.lm_col, 0), errors="coerce").fillna(0)
    lu = pd.to_numeric(gdf.get(cfg.lu_col, 0), errors="coerce").fillna(0)
    return dict(zip(gdf[cfg.id_col], (lm + lu).clip(lower=1).astype(int)))


def find_redundant(buffers: gpd.GeoDataFrame, id_col: str,
                   selected: list[str], threshold: float) -> list[str]:
    """Selected antennas whose range overlaps the other selected ranges by >= threshold."""
    geoms = buffers.set_index(id_col).geometry.loc[selected]
    redundant = []
    for aid, geom in geoms.items():
        others = geoms.drop(aid)
        if others.empty or geom.area == 0:
            continue
        overlap = geom.intersection(unary_union(list(others))).area / geom.area
        if overlap >= threshold:
            redundant.append(aid)
    return redundant


# --------------------------------------------------------------------------- #
# main workflow
# --------------------------------------------------------------------------- #

def run(cfg: Config) -> Result:
    buildings = _centroids(_load(cfg.buildings_path, cfg.id_col))
    existing = _centroids(_load(cfg.existing_path, cfg.id_col))
    candidates = _centroids(_load(cfg.candidates_path, cfg.id_col))
    existing = existing.to_crs(buildings.crs)
    candidates = candidates.to_crs(buildings.crs)

    # step 3: remove buildings already covered by existing antennas
    existing_buf = _buffers(existing, cfg.radius_existing)
    if len(existing_buf):
        covered_now = buildings.within(unary_union(list(existing_buf.geometry)))
    else:
        covered_now = pd.Series(False, index=buildings.index)
    to_cover = buildings.loc[~covered_now].copy()

    # step 4-5: candidate ranges and coverage table
    cand_buf = _buffers(candidates, cfg.radius_candidates)
    joined = gpd.sjoin(
        to_cover[[cfg.id_col, "geometry"]],
        cand_buf[[cfg.id_col, "geometry"]].rename(columns={cfg.id_col: "antenna_id"}),
        predicate="within",
        how="inner",
    )
    pairs = (joined.rename(columns={cfg.id_col: "building_id"})
             [["building_id", "antenna_id"]].drop_duplicates().reset_index(drop=True))
    out_of_range = to_cover.loc[~to_cover[cfg.id_col].isin(pairs["building_id"])]

    # step 6: optimisation
    weights = _weights(to_cover, cfg) if cfg.use_weights else None
    if cfg.max_antennas > 0:
        solution = solve_max_coverage(pairs, cfg.max_antennas, weights, cfg.timeout)
    else:
        solution = solve_min_cover(pairs, cfg.timeout)

    # step 7: post-processing
    redundant = find_redundant(cand_buf, cfg.id_col, solution.selected,
                               cfg.redundancy_threshold)
    low_value = []
    for aid, grp in pairs[pairs["antenna_id"].isin(solution.selected)].groupby("antenna_id"):
        if aid in redundant:
            continue
        if cfg.use_weights:
            score = sum(weights.get(b, 1) for b in grp["building_id"])
            if score <= cfg.low_value_dwellings:
                low_value.append(aid)
        elif len(grp) <= cfg.low_value_buildings:
            low_value.append(aid)

    cand_buf["category"] = None
    sel = cand_buf[cfg.id_col].isin(solution.selected)
    cand_buf.loc[sel, "category"] = "essential"
    cand_buf.loc[cand_buf[cfg.id_col].isin(redundant), "category"] = "redundant"
    cand_buf.loc[cand_buf[cfg.id_col].isin(low_value), "category"] = "low_value"

    return Result(buildings, to_cover, out_of_range, existing_buf, cand_buf,
                  pairs, solution, redundant, low_value)


def save(result: Result, out_dir: str | Path, id_col: str = "ID_SWDE") -> Path:
    """Write all layers to one GeoPackage and the selected antennas to CSV."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gpkg = out_dir / "lorawan_result.gpkg"
    result.buildings.to_file(gpkg, layer="building_centroids", driver="GPKG")
    result.to_cover.to_file(gpkg, layer="buildings_to_cover", driver="GPKG")
    if len(result.out_of_range):
        result.out_of_range.to_file(gpkg, layer="buildings_out_of_range", driver="GPKG")
    result.existing_buffers.to_file(gpkg, layer="existing_coverage", driver="GPKG")
    chosen = result.candidate_buffers[result.candidate_buffers["category"].notna()]
    chosen.to_file(gpkg, layer="selected_coverage", driver="GPKG")

    counts = result.pairs.groupby("antenna_id").size()
    table = (chosen[[id_col, "category", "radius_m"]]
             .assign(buildings_in_range=lambda d: d[id_col].map(counts).fillna(0).astype(int)))
    table.to_csv(out_dir / "selected_antennas.csv", index=False)
    result.pairs.to_csv(out_dir / "coverage_matrix.csv", index=False)
    return gpkg
