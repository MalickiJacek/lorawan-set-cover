"""Command line entry point: ``python -m lorawan_setcover --help``."""

import argparse
import time

from .pipeline import Config, run, save
from .solver import solve_greedy_cover


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="lorawan_setcover",
        description="Optimal LoRaWAN gateway placement (Set Cover / Max Coverage ILP).",
    )
    p.add_argument("--buildings", required=True, help="buildings to cover (polygons)")
    p.add_argument("--existing", required=True, help="existing antennas (polygons)")
    p.add_argument("--candidates", required=True, help="candidate antenna sites (polygons)")
    p.add_argument("--out", default="output", help="output folder")
    p.add_argument("--radius", default="500",
                   help='range of candidates: "500" or an expression like "400+10*WYSOKOSC"')
    p.add_argument("--radius-existing", default="500", help="range of existing antennas")
    p.add_argument("--budget", type=int, default=0,
                   help="max number of new antennas (0 = minimise count, full coverage)")
    p.add_argument("--weights", action="store_true",
                   help="weight buildings by dwellings + commercial units (LM + LU)")
    p.add_argument("--id-col", default="ID_SWDE")
    p.add_argument("--timeout", type=int, default=300, help="solver time limit [s]")
    a = p.parse_args(argv)

    cfg = Config(a.buildings, a.existing, a.candidates,
                 radius_existing=a.radius_existing, radius_candidates=a.radius,
                 id_col=a.id_col, use_weights=a.weights, max_antennas=a.budget,
                 timeout=a.timeout)
    t0 = time.perf_counter()
    r = run(cfg)
    dt = time.perf_counter() - t0
    gpkg = save(r, a.out, a.id_col)

    print(f"Buildings total:            {len(r.buildings)}")
    print(f"Already covered (existing): {len(r.buildings) - len(r.to_cover)}")
    print(f"To cover:                   {len(r.to_cover)}")
    print(f"Out of range of candidates: {len(r.out_of_range)}")
    print(f"Candidate sites:            {len(r.candidate_buffers)}")
    print(f"Solver status:              {r.solution.status}")
    print(f"Selected antennas:          {r.solution.n_selected} "
          f"(essential {len(r.essential)}, redundant {len(r.redundant)}, "
          f"low value {len(r.low_value)})")
    print(f"Buildings covered:          {len(r.solution.covered_buildings)}")
    if a.budget == 0:
        greedy = solve_greedy_cover(r.pairs)
        print(f"Greedy baseline would need: {greedy.n_selected} antennas")
    print(f"Runtime:                    {dt:.1f} s")
    print(f"Results:                    {gpkg}")


if __name__ == "__main__":
    main()
