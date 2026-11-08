from pathlib import Path

import pytest

gpd = pytest.importorskip("geopandas")

from lorawan_setcover.pipeline import Config, run  # noqa: E402
from lorawan_setcover.solver import solve_greedy_cover  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data" / "sample"


def cfg(**kw):
    return Config(DATA / "Buildings_To_Cover.shp",
                  DATA / "Existing_Antennas.shp",
                  DATA / "Candidate_Antennas.shp", **kw)


def test_full_cover_on_sample_data():
    r = run(cfg(radius_candidates="500"))
    coverable = set(r.pairs["building_id"])
    # every building reachable by some candidate is covered
    assert r.solution.covered_buildings == coverable
    # buildings already in range of existing antennas were excluded
    assert len(r.to_cover) < len(r.buildings)
    # exact ILP beats the greedy heuristic on this instance
    assert r.solution.n_selected < solve_greedy_cover(r.pairs).n_selected
    # categories partition the selection
    assert set(r.essential) | set(r.redundant) | set(r.low_value) == set(r.solution.selected)


def test_height_dependent_radius():
    r = run(cfg(radius_candidates='300+10*"WYSOKOSC"'))
    radii = r.candidate_buffers.set_index("ID_SWDE")["radius_m"]
    cand = gpd.read_file(DATA / "Candidate_Antennas.shp").set_index("ID_SWDE")
    assert (radii - (300 + 10 * cand["WYSOKOSC"])).abs().max() < 1e-6


def test_budget_mode():
    r = run(cfg(max_antennas=5, use_weights=True))
    assert r.solution.n_selected <= 5
