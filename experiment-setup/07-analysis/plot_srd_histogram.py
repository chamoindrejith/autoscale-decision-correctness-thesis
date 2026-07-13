#!/usr/bin/env python3
"""
plot_srd_histogram.py — SRD distribution per workload pattern.

For each pattern (Step / Burst / Ramp / Noisy) draws a histogram of the
Scale Reaction Delay in seconds, using only scale-up decisions where an
SLO breach was actually detected (srd_source in {'late', 'pre_emptive'}).

Reads:
  - results/decisions_with_ses.csv

Writes:
  - results/plots/srd_histogram_per_pattern.png    (2x2 grid, one subplot per pattern)
  - results/plots/srd_histogram_overlay.png        (single axes, all patterns overlaid)

The vertical dashed line at SRD = 0 separates:
  - SRD < 0  → HPA fired BEFORE p95 latency crossed the 500 ms SLO (pre-emptive)
  - SRD > 0  → HPA fired AFTER the SLO breach (late reaction)

Only counted runs (run_num > 3) are included by default; edit the
`WARMUP_LAST_RUN_NUM` constant to change this.
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
WARMUP_LAST_RUN_NUM = 3  # discard runs 1-3 as warm-up per methodology
BIN_COUNT = 30


def load_srd(path: Path) -> dict[str, list[float]]:
    """Return {pattern: [srd_seconds, ...]} for counted scale-ups with a
    valid SRD (source in late/pre_emptive)."""
    if not path.exists():
        print(f"ERROR: {path} not found — run compute_srd.py first", file=sys.stderr)
        sys.exit(1)

    out: dict[str, list[float]] = defaultdict(list)
    with open(path) as f:
        for r in csv.DictReader(f):
            pattern = (r.get("pattern") or "").strip()
            if pattern not in PATTERNS:
                continue
            # Discard warm-up runs
            try:
                run_num = int(r.get("run_num") or 0)
            except ValueError:
                continue
            if run_num <= WARMUP_LAST_RUN_NUM:
                continue
            # Only scale-ups with a meaningful SRD
            if r.get("srd_source") not in ("late", "pre_emptive"):
                continue
            try:
                srd = float(r["srd_seconds"])
            except (KeyError, ValueError):
                continue
            out[pattern].append(srd)
    return out


def summarise(vals: list[float]) -> str:
    if not vals:
        return "no data"
    return (f"n={len(vals)}, "
            f"mean={statistics.mean(vals):.1f}s, "
            f"median={statistics.median(vals):.1f}s, "
            f"min={min(vals):.1f}s, max={max(vals):.1f}s")


def main() -> None:
    data = load_srd(INPUT)
    total = sum(len(v) for v in data.values())
    print(f"Loaded {total} scale-up decisions with SRD (counted runs, run>{WARMUP_LAST_RUN_NUM})")
    for p in PATTERNS:
        print(f"  {p:<8} {summarise(data.get(p, []))}")

    # Common bin edges so all subplots share the same x-axis binning
    all_vals = [v for pat in PATTERNS for v in data.get(pat, [])]
    if not all_vals:
        print("No SRD data to plot; exiting.")
        return
    lo, hi = min(all_vals), max(all_vals)
    if hi - lo < 1:
        hi = lo + 1
    bin_edges = np.linspace(lo, hi, BIN_COUNT + 1)

    # ------------------------------------------------------------------
    # Figure 1: 2×2 grid, one subplot per pattern
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
                       label="SLO breach moment")
            ax.axvline(statistics.mean(vals), color="red", linestyle=":",
                       alpha=0.7,
                       label=f"mean = {statistics.mean(vals):.1f} s")
            ax.legend(loc="upper right", fontsize=8)
        else:
            ax.text(0.5, 0.5, "no data\n(no SRD in counted runs)",
                    ha="center", va="center", fontsize=11,
                    transform=ax.transAxes, color="grey")
        ax.set_title(f"{pattern.capitalize()} pattern  ({summarise(vals)})",
                     fontsize=10)
        ax.set_xlabel("SRD (seconds)")
        ax.set_ylabel("Number of scale-up decisions")
        ax.grid(True, alpha=0.3)

    fig.suptitle("Scale Reaction Delay (SRD) Distribution per Workload Pattern\n"
                 "SRD = T_decision − T_SLO_risk  |  "
                 "SRD > 0 → HPA fired after the SLO breach",
                 fontsize=12)
    plt.tight_layout()
    outpath = PLOTS_DIR / "srd_histogram_per_pattern.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"\nSaved {outpath.name}")
    plt.close()

    # ------------------------------------------------------------------
    # Figure 2: overlay of all patterns on one axes
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(11, 6))
    for pattern in PATTERNS:
        vals = data.get(pattern, [])
        if not vals:
            continue
        ax.hist(vals, bins=bin_edges, alpha=0.55, edgecolor="black",
                linewidth=0.3,
                color=PATTERN_COLORS[pattern],
                label=f"{pattern.capitalize()}  (n={len(vals)}, "
                      f"median={statistics.median(vals):.1f} s)")
    ax.axvline(0, color="black", linestyle="--", alpha=0.7,
               label="SLO breach moment (SRD = 0)")
    ax.set_xlabel("SRD (seconds) — negative = pre-emptive, positive = late reaction")
    ax.set_ylabel("Number of scale-up decisions")
    ax.set_title("SRD Distribution — Overlay of All Workload Patterns "
                 f"(counted runs only, n={total})")
    ax.legend(loc="best", fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    outpath = PLOTS_DIR / "srd_histogram_overlay.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


if __name__ == "__main__":
    main()
