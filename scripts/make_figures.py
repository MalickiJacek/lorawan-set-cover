"""Render the README figures from the synthetic sample data.

Usage:  python scripts/make_figures.py
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lorawan_setcover.pipeline import Config, run  # noqa: E402
from lorawan_setcover.solver import solve_greedy_cover  # noqa: E402

DATA = ROOT / "data" / "sample"
IMG = ROOT / "docs" / "img"
IMG.mkdir(parents=True, exist_ok=True)

COLORS = {"existing": "#3b6fd8", "essential": "#2e9e4f",
          "redundant": "#f08c00", "low_value": "#8e44ad"}


def cfg(radius):
    return Config(DATA / "Buildings_To_Cover.shp", DATA / "Existing_Antennas.shp",
                  DATA / "Candidate_Antennas.shp", radius_candidates=radius)


def draw(ax, r, title):
    r.existing_buffers.plot(ax=ax, color=COLORS["existing"], alpha=0.18,
                            edgecolor=COLORS["existing"], linewidth=0.8)
    sel = r.candidate_buffers[r.candidate_buffers["category"].notna()]
    for cat in ["essential", "redundant", "low_value"]:
        part = sel[sel["category"] == cat]
        if len(part):
            part.plot(ax=ax, facecolor=COLORS[cat], alpha=0.12,
                      edgecolor=COLORS[cat], linewidth=0.7)
            part.centroid.plot(ax=ax, color=COLORS[cat], markersize=14, marker="^", zorder=4)
    r.buildings.plot(ax=ax, color="#9aa0a6", markersize=0.6, zorder=2)
    r.to_cover.plot(ax=ax, color="#d93025", markersize=0.9, zorder=3)
    if len(r.out_of_range):
        r.out_of_range.plot(ax=ax, color="black", markersize=8, marker="x", zorder=5)
    r.existing_buffers.centroid.plot(ax=ax, color=COLORS["existing"], markersize=30,
                                     marker="s", zorder=5)
    ax.set_title(title, fontsize=11)
    ax.set_axis_off()
    ax.set_aspect("equal")


def main():
    r500 = run(cfg("500"))
    rh = run(cfg("300+10*WYSOKOSC"))

    fig, axes = plt.subplots(1, 2, figsize=(14, 7), dpi=130)
    draw(axes[0], r500, f"Fixed range 500 m  →  {r500.solution.n_selected} new antennas")
    draw(axes[1], rh, f"Range = 300 m + 10 × height  →  {rh.solution.n_selected} new antennas")
    handles = [
        Line2D([], [], marker="s", ls="", color=COLORS["existing"], label="existing antenna + range"),
        Line2D([], [], marker="^", ls="", color=COLORS["essential"], label="selected – essential"),
        Line2D([], [], marker="^", ls="", color=COLORS["redundant"], label="selected – redundant (≥75 % overlap)"),
        Line2D([], [], marker="^", ls="", color=COLORS["low_value"], label="selected – low value"),
        Line2D([], [], marker="o", ls="", color="#d93025", markersize=4, label="building to cover"),
        Line2D([], [], marker="x", ls="", color="black", label="out of range of all candidates"),
        Patch(color="#9aa0a6", label="already covered building"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False, fontsize=9)
    fig.suptitle("LoRaWAN gateway placement – exact Set Cover (ILP) on a synthetic town",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0.07, 1, 0.96))
    fig.savefig(IMG / "result_map.png")
    plt.close(fig)

    # sensitivity: ILP vs greedy for several radii
    radii = [300, 400, 500, 600, 700]
    ilp, greedy = [], []
    for rad in radii:
        r = run(cfg(str(rad)))
        ilp.append(r.solution.n_selected)
        greedy.append(solve_greedy_cover(r.pairs).n_selected)
    fig, ax = plt.subplots(figsize=(7, 4), dpi=130)
    x = range(len(radii))
    ax.bar([i - 0.2 for i in x], greedy, width=0.4, color="#bdc1c6", label="Greedy heuristic")
    ax.bar([i + 0.2 for i in x], ilp, width=0.4, color=COLORS["essential"], label="ILP (exact optimum)")
    for i, (g, o) in enumerate(zip(greedy, ilp)):
        ax.text(i + 0.2, o + 1, f"−{(g - o) / g:.0%}", ha="center", fontsize=8)
    ax.set_xticks(list(x), [f"{r} m" for r in radii])
    ax.set_xlabel("Antenna range")
    ax.set_ylabel("New antennas needed for full coverage")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(IMG / "ilp_vs_greedy.png")
    plt.close(fig)

    print("radius | greedy | ilp")
    for rad, g, o in zip(radii, greedy, ilp):
        print(f"{rad:>6} | {g:>6} | {o:>3}")


if __name__ == "__main__":
    main()
