"""
Set Cover solvers working on a building-antenna coverage table.

The input is a table of pairs ``(building_id, antenna_id)`` meaning
"antenna covers building". No GIS dependencies here, so the optimisation
logic can be unit-tested and reused outside QGIS.

Two exact ILP models (PuLP) and one greedy baseline are provided:

* :func:`solve_min_cover`      - classic Set Cover: minimum number of antennas
                                 that cover every coverable building
* :func:`solve_max_coverage`   - Maximum (weighted) Coverage: best antennas
                                 within a budget of ``k`` antennas
* :func:`solve_greedy_cover`   - textbook greedy heuristic, used as a baseline
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

import pandas as pd
import pulp


@dataclass
class CoverResult:
    """Result of a coverage optimisation."""

    selected: list[str]
    covered_buildings: set[str] = field(default_factory=set)
    status: str = "Optimal"
    objective: float = 0.0

    @property
    def n_selected(self) -> int:
        return len(self.selected)


def get_solver(timeout: int = 300) -> pulp.LpSolver:
    """
    Return an available ILP solver.

    PuLP 2.x/3.x bundles CBC (``PULP_CBC_CMD``); if it is missing, HiGHS
    (``pip install highspy``) is used as a fallback. PuLP 4.0 changed its API
    and is not supported yet (see requirements.txt).
    """
    available = pulp.listSolvers(onlyAvailable=True)
    if "PULP_CBC_CMD" in available:
        return pulp.PULP_CBC_CMD(msg=0, timeLimit=timeout)
    if "HiGHS" in available:
        return pulp.HiGHS(msg=False, timeLimit=timeout)
    if "HiGHS_CMD" in available:
        return pulp.HiGHS_CMD(msg=False, timeLimit=timeout)
    raise RuntimeError(
        'No ILP solver available. Install `pip install "pulp>=2.7,<4"` (bundled CBC) '
        "or `pip install highspy`."
    )


def _binary(prob: pulp.LpProblem, name: str):
    """Binary variable: prob.add_variable (PuLP >= 3.3) or LpVariable (older PuLP)."""
    if hasattr(prob, "add_variable"):
        return prob.add_variable(name, cat="Binary")
    return pulp.LpVariable(name, cat="Binary")


def _coverage_sets(pairs: pd.DataFrame,
                   building_col: str = "building_id",
                   antenna_col: str = "antenna_id") -> dict[str, set[str]]:
    """antenna_id -> set of building_ids it covers."""
    df = pairs[[building_col, antenna_col]].dropna().astype(str).drop_duplicates()
    return {a: set(g[building_col]) for a, g in df.groupby(antenna_col)}


def _check_status(prob: pulp.LpProblem) -> str:
    status = pulp.LpStatus[prob.status]
    # 1 = Optimal, 0 = Not Solved (time limit hit, best feasible solution returned)
    if prob.status not in (0, 1):
        raise RuntimeError(f"Solver returned status '{status}'")
    return status


def solve_min_cover(pairs: pd.DataFrame, timeout: int = 300,
                    building_col: str = "building_id",
                    antenna_col: str = "antenna_id") -> CoverResult:
    """
    Classic Set Cover as an Integer Linear Program::

        min  sum_i x_i
        s.t. sum_{i covers j} x_i >= 1     for every building j
             x_i in {0, 1}

    Only buildings that appear in ``pairs`` (i.e. are coverable by at least
    one antenna) are constrained, so the model is always feasible.
    """
    cover = _coverage_sets(pairs, building_col, antenna_col)
    if not cover:
        return CoverResult(selected=[], status="Empty")

    antennas = sorted(cover)
    buildings = sorted(set().union(*cover.values()))
    covering: dict[str, list[str]] = {b: [] for b in buildings}
    for a, bs in cover.items():
        for b in bs:
            covering[b].append(a)

    prob = pulp.LpProblem("SetCover_Minimize", pulp.LpMinimize)
    x = {a: _binary(prob, f"x_{i}") for i, a in enumerate(antennas)}
    prob += pulp.lpSum(x.values())
    for b in buildings:
        prob += pulp.lpSum(x[a] for a in covering[b]) >= 1

    prob.solve(get_solver(timeout))
    status = _check_status(prob)

    selected = [a for a in antennas if (x[a].value() or 0) > 0.5]
    covered = set().union(*(cover[a] for a in selected)) if selected else set()
    return CoverResult(selected, covered, status, float(len(selected)))


def solve_max_coverage(pairs: pd.DataFrame, max_antennas: int,
                       weights: Mapping[str, float] | None = None,
                       timeout: int = 300,
                       building_col: str = "building_id",
                       antenna_col: str = "antenna_id") -> CoverResult:
    """
    Budgeted Maximum (weighted) Coverage as an ILP::

        max  sum_j w_j * y_j
        s.t. sum_i x_i <= k
             y_j <= sum_{i covers j} x_i   for every building j
             x_i, y_j in {0, 1}

    ``weights`` maps building_id -> weight (e.g. number of dwellings);
    missing buildings default to weight 1.
    """
    if max_antennas <= 0:
        raise ValueError("max_antennas must be > 0")

    cover = _coverage_sets(pairs, building_col, antenna_col)
    if not cover:
        return CoverResult(selected=[], status="Empty")

    antennas = sorted(cover)
    buildings = sorted(set().union(*cover.values()))
    covering: dict[str, list[str]] = {b: [] for b in buildings}
    for a, bs in cover.items():
        for b in bs:
            covering[b].append(a)
    w = {b: float((weights or {}).get(b, 1)) for b in buildings}

    prob = pulp.LpProblem("MaxWeightedCoverage", pulp.LpMaximize)
    x = {a: _binary(prob, f"x_{i}") for i, a in enumerate(antennas)}
    y = {b: _binary(prob, f"y_{j}") for j, b in enumerate(buildings)}
    prob += pulp.lpSum(w[b] * y[b] for b in buildings)
    prob += pulp.lpSum(x.values()) <= max_antennas
    for b in buildings:
        prob += y[b] <= pulp.lpSum(x[a] for a in covering[b])

    prob.solve(get_solver(timeout))
    status = _check_status(prob)

    selected = [a for a in antennas if (x[a].value() or 0) > 0.5]
    covered = set().union(*(cover[a] for a in selected)) if selected else set()
    return CoverResult(selected, covered, status, float(pulp.value(prob.objective) or 0))


def solve_greedy_cover(pairs: pd.DataFrame,
                       building_col: str = "building_id",
                       antenna_col: str = "antenna_id") -> CoverResult:
    """
    Greedy Set Cover heuristic (ln(n)-approximation): repeatedly pick the
    antenna that covers the most still-uncovered buildings. Used as a baseline
    to show how much the exact ILP saves.
    """
    cover = _coverage_sets(pairs, building_col, antenna_col)
    uncovered = set().union(*cover.values()) if cover else set()
    selected: list[str] = []
    while uncovered:
        best = max(sorted(cover), key=lambda a: len(cover[a] & uncovered))
        gain = cover[best] & uncovered
        if not gain:
            break
        selected.append(best)
        uncovered -= gain
    covered = set().union(*(cover[a] for a in selected)) if selected else set()
    return CoverResult(selected, covered, "Heuristic", float(len(selected)))


def is_full_cover(pairs: pd.DataFrame, selected: Iterable[str],
                  building_col: str = "building_id",
                  antenna_col: str = "antenna_id") -> bool:
    """True if ``selected`` antennas cover every building present in ``pairs``."""
    cover = _coverage_sets(pairs, building_col, antenna_col)
    all_b = set().union(*cover.values()) if cover else set()
    got = set().union(*(cover.get(str(a), set()) for a in selected)) if selected else set()
    return got == all_b
