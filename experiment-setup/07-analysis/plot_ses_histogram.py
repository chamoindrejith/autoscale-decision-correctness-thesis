#!/usr/bin/env python3
"""
plot_ses_histogram.py — SES distribution per workload pattern.

For each pattern draws a histogram of the Scale Effectiveness Score:

    SES = (p95_before − p95_after) / p95_before

    SES > 0  → latency dropped after scaling — HPA action was effective
    SES = 0  → no change
    SES < 0  → latency worsened after scaling — likely workload artifact
                (e.g. Ramp's monotonically increasing load)

Only decisions in counted runs (run_num > 3) with a computed SES value
are included. Both scale-ups and scale-downs are shown; you can separate
them by editing `INCLUDE_DIRECTIONS`.

Reads:
  - results/decisions_with_ses.csv

Writes:
  - results/plots/ses_histogram_per_pattern.png    (2x2 grid, one per pattern)
  - results/plots/ses_histogram_overlay.png        (single axes, overlay)
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "results" / "decisions_with_ses.csv"
PLOTS_DIR = ROOT / "results" / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

PATTERNS = ["step", "burst", "ramp", "noisy"]
PATTERN_COLORS = {
    "step":  "#1f77b4",
    "burst": "#ff7f0e",
    "ramp":  "#2ca02c",
    "noisy": "#d62728",
}
WARMUP_LAST_RUN_NUM = 3
INCLUDE_DIRECTIONS = {"up", "down"}   # set to {'up'} for scale-ups only
BIN_COUNT = 30


def load_ses(path: Path) -> dict[str, list[float]]:
    if not path.exists():
        print(f"ERROR: {path} not found — run compute_ses.py first",
              file=sys.stderr)
        sys.exit(1)
    out: dict[str, list[float]] = defaultdict(list)
    with open(path) as f:
        for r in csv.DictReader(f):
            pattern = (r.get("pattern") or "").strip()
            if pattern not in PATTERNS:
                continue
            direction = (r.get("direction") or "").lower()
            if direction not in INCLUDE_DIRECTIONS:
                continue
            try:
                run_num = int(r.get("run_num") or 0)
            except ValueError:
                continue
            if run_num <= WARMUP_LAST_RUN_NUM:
                continue
            try:
                ses = float(r["ses"])
            except (KeyError, ValueError):
                continue
            out[pattern].append(ses)
    return out


def summarise(vals: list[float]) -> str:
    if not vals:
        return "no data"
    pos = sum(1 for v in vals if v > 0)
    neg = sum(1 for v in vals if v < 0)
    return (f"n={len(vals)}, "
            f"mean={statistics.mean(vals):.3f}, "
            f"median={statistics.median(vals):.3f}, "
            f"pos={pos} neg={neg}")


def main() -> None:
    data = load_ses(INPUT)
    total = sum(len(v) for v in data.values())
    print(f"Loaded {total} decisions with SES "
          f"(counted runs, direction ∈ {INCLUDE_DIRECTIONS})")
    for p in PATTERNS:
        print(f"  {p:<8} {summarise(data.get(p, []))}")

    all_vals = [v for pat in PATTERNS for v in data.get(pat, [])]
    if not all_vals:
        print("No SES data to plot; exiting.")
        return
    lo, hi = min(all_vals), max(all_vals)
    # Symmetric bin range so 0 is a visible reference
    span = max(abs(lo), abs(hi))
    bin_edges = np.linspace(-span, span, BIN_COUNT + 1)

    # ------------------------------------------------------------------
    # Figure 1: 2x2 grid
    # ------------------------------------------------------------------
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), sharex=True, sharey=False)
    axes = axes.flatten()
    for ax, pattern in zip(axes, PATTERNS):
        vals = data.get(pattern, [])
        colour = PATTERN_COLORS[pattern]
        if vals:
            ax.hist(vals, bins=bin_edges, color=colour, edgecolor="black",
                    alpha=0.85, linewidth=0.4)
            ax.axvline(0, color="black", linestyle="--", alpha=0.7,
                       label="No change (SES = 0)")
            ax.axvline(statistics.mean(vals), color="red", linestyle=":",
                       alpha=0.7,
                       label=f"mean = {statistics.mean(vals):.2f}")
            ax.legend(loc="upper right", fontsize=8)
        else:
            ax.text(0.5, 0.5, "no data",
                    ha="center", va="center", fontsize=11,
                    transform=ax.transAxes, color="grey")
        ax.set_title(f"{pattern.capitalize()} pattern  ({summarise(vals)})",
                     fontsize=10)
        ax.set_xlabel("SES  (positive = latency improved after scaling)")
        ax.set_ylabel("Number of decisions")
        ax.grid(True, alpha=0.3)

    fig.suptitle("Scale Effectiveness Score (SES) Distribution per Workload Pattern\n"
                 "SES = (Latency_before − Latency_after) / Latency_before",
                 fontsize=12)
    plt.tight_layout()
    outpath = PLOTS_DIR / "ses_histogram_per_pattern.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"\nSaved {outpath.name}")
    plt.close()

    # ------------------------------------------------------------------
    # Figure 2: overlay
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(11, 6))
    for pattern in PATTERNS:
        vals = data.get(pattern, [])
        if not vals:
            continue
        ax.hist(vals, bins=bin_edges, alpha=0.55, edgecolor="black",
                linewidth=0.3, color=PATTERN_COLORS[pattern],
                label=f"{pattern.capitalize()}  (n={len(vals)}, "
                      f"median={statistics.median(vals):.2f})")
    ax.axvline(0, color="black", linestyle="--", alpha=0.7,
               label="No change (SES = 0)")
    ax.set_xlabel("SES  (negative = latency worsened; positive = latency improved)")
    ax.set_ylabel("Number of decisions")
    ax.set_title("SES Distribution — Overlay of All Workload Patterns "
                 f"(counted runs only, n={total})")
    ax.legend(loc="best", fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    outpath = PLOTS_DIR / "ses_histogram_overlay.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


if __name__ == "__main__":
    main()
