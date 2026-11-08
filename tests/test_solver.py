import pandas as pd
import pytest

from lorawan_setcover.solver import (is_full_cover, solve_greedy_cover,
                                     solve_max_coverage, solve_min_cover)


def pairs(mapping):
    """{antenna: [buildings]} -> coverage table."""
    return pd.DataFrame(
        [(b, a) for a, bs in mapping.items() for b in bs],
        columns=["building_id", "antenna_id"],
    )


# Classic instance where greedy is NOT optimal:
# antenna G covers the most buildings (6) and greedy takes it first,
# but the optimum is the two "halves" L and R.
GREEDY_TRAP = pairs({
    "L": ["1", "2", "3", "4"],
    "R": ["5", "6", "7", "8"],
    "G": ["2", "3", "4", "5", "6", "7"],
    "X": ["1"],
    "Y": ["8"],
})


def test_min_cover_finds_optimum():
    res = solve_min_cover(GREEDY_TRAP)
    assert res.status == "Optimal"
    assert sorted(res.selected) == ["L", "R"]
    assert is_full_cover(GREEDY_TRAP, res.selected)


def test_greedy_is_worse_on_trap_instance():
    greedy = solve_greedy_cover(GREEDY_TRAP)
    assert greedy.selected[0] == "G"
    assert greedy.n_selected == 3
    assert is_full_cover(GREEDY_TRAP, greedy.selected)


def test_ilp_never_worse_than_greedy_random():
    import random
    rnd = random.Random(0)
    for _ in range(20):
        mapping = {f"A{i}": rnd.sample([str(b) for b in range(40)], rnd.randint(1, 8))
                   for i in range(25)}
        p = pairs(mapping)
        ilp = solve_min_cover(p)
        assert is_full_cover(p, ilp.selected)
        assert ilp.n_selected <= solve_greedy_cover(p).n_selected


def test_max_coverage_respects_budget_and_weights():
    p = pairs({"A": ["1", "2", "3"], "B": ["4"], "C": ["5"]})
    # building 4 is a large block of flats -> B beats A with budget 1
    res = solve_max_coverage(p, max_antennas=1, weights={"4": 100})
    assert res.selected == ["B"]
    assert res.objective == 100
    res2 = solve_max_coverage(p, max_antennas=1)          # unweighted
    assert res2.selected == ["A"]
    assert solve_max_coverage(p, max_antennas=3).n_selected <= 3


def test_max_coverage_rejects_zero_budget():
    with pytest.raises(ValueError):
        solve_max_coverage(GREEDY_TRAP, max_antennas=0)


def test_empty_input():
    empty = pd.DataFrame(columns=["building_id", "antenna_id"])
    assert solve_min_cover(empty).selected == []
