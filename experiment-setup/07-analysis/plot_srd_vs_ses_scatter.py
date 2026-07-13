#!/usr/bin/env python3
"""
plot_srd_vs_ses_scatter.py — Joint distribution of SRD × SES per decision.

For every scale-up decision with both metrics available, plots a single
dot at (SRD, SES), coloured by workload pattern. Divides the plane into
four labelled quadrants:

    Q2 (top-left)   SRD ≤ 0, SES > 0  → pre-emptive AND effective  (ideal)
    Q1 (top-right)  SRD > 0, SES > 0  → late but latency recovered
    Q3 (bottom-right) SRD > 0, SES < 0 → late AND ineffective       (worst)
    Q4 (bottom-left)  SRD ≤ 0, SES < 0 → pre-emptive, latency worse
                      (usually a workload-monotonic-growth artifact,
                       especially for the Ramp pattern)

Only decisions in counted runs (run_num > 3) with both SRD and SES
values are plotted.

Reads:
  - results/decisions_with_ses.csv

Writes:
  - results/plots/srd_vs_ses_scatter.png     (all patterns overlaid)
  - results/plots/srd_vs_ses_scatter_grid.png (2×2 panel per pattern)
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

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


def load_points(path: Path) -> dict[str, list[tuple[float, float]]]:
    """Return {pattern: [(srd_s, ses), ...]} for scale-ups where both
    metrics are present."""
    if not path.exists():
        print(f"ERROR: {path} not found — run compute_srd.py and "
              f"compute_ses.py first",
              file=sys.stderr)
        sys.exit(1)
    out: dict[str, list[tuple[float, float]]] = defaultdict(list)
    with open(path) as f:
        for r in csv.DictReader(f):
            pattern = (r.get("pattern") or "").strip()
            if pattern not in PATTERNS:
                continue
            if (r.get("direction") or "").lower() != "up":
                continue
            try:
                run_num = int(r.get("run_num") or 0)
            except ValueError:
                continue
            if run_num <= WARMUP_LAST_RUN_NUM:
                continue
            if r.get("srd_source") not in ("late", "pre_emptive"):
                continue
            try:
                srd = float(r["srd_seconds"])
                ses = float(r["ses"])
            except (KeyError, ValueError):
                continue
            out[pattern].append((srd, ses))
    return out


def annotate_quadrants(ax, xlim, ylim):
    """Add subtle background shading and quadrant labels."""
    x_left, x_right = xlim
    y_bot, y_top = ylim
    # Quadrant labels
    ax.text(x_left * 0.95, y_top * 0.9, "Pre-emptive\n& effective",
            fontsize=9, color="darkgreen", alpha=0.7,
            ha="left", va="top")
    ax.text(x_right * 0.95, y_top * 0.9, "Late\nbut recovered",
            fontsize=9, color="darkgoldenrod", alpha=0.7,
            ha="right", va="top")
    ax.text(x_left * 0.95, y_bot * 0.9, "Pre-emptive\nbut worsened\n(workload artifact)",
            fontsize=9, color="grey", alpha=0.7,
            ha="left", va="bottom")
    ax.text(x_right * 0.95, y_bot * 0.9, "Late\n& ineffective",
            fontsize=9, color="darkred", alpha=0.7,
            ha="right", va="bottom")
    # Reference lines
    ax.axhline(0, color="black", linestyle="--", alpha=0.6, linewidth=1)
    ax.axvline(0, color="black", linestyle="--", alpha=0.6, linewidth=1)


def main() -> None:
    data = load_points(INPUT)
    total = sum(len(v) for v in data.values())
    print(f"Loaded {total} scale-up decisions with both SRD and SES "
          f"(counted runs, run > {WARMUP_LAST_RUN_NUM})")

    if not total:
        print("No plottable points; exiting.")
        return

    # Common axis limits — a bit of padding
    all_srd = [srd for pts in data.values() for srd, _ in pts]
    all_ses = [ses for pts in data.values() for _, ses in pts]
    srd_span = max(abs(min(all_srd)), abs(max(all_srd))) * 1.1
    ses_span = max(abs(min(all_ses)), abs(max(all_ses))) * 1.1

    # ------------------------------------------------------------------
    # Figure 1: single-panel overlay
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(11, 7.5))
    for pattern in PATTERNS:
        pts = data.get(pattern, [])
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.scatter(xs, ys, s=60, alpha=0.65,
                   color=PATTERN_COLORS[pattern],
                   edgecolor="black", linewidth=0.4,
                   label=f"{pattern.capitalize()}  (n={len(pts)}, "
                         f"median SRD={statistics.median(xs):.1f} s, "
                         f"median SES={statistics.median(ys):.2f})")

    ax.set_xlim(-srd_span, srd_span)
    ax.set_ylim(-ses_span, ses_span)
    annotate_quadrants(ax, ax.get_xlim(), ax.get_ylim())

    ax.set_xlabel("SRD — Scale Reaction Delay (seconds; ← pre-emptive, late →)")
    ax.set_ylabel("SES — Scale Effectiveness Score (↑ latency improved after scaling)")
    ax.set_title("SRD × SES per Scale-Up Decision, by Workload Pattern\n"
                 f"(counted runs only, n = {total})")
    ax.legend(loc="lower left", fontsize=9, framealpha=0.95)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    outpath = PLOTS_DIR / "srd_vs_ses_scatter.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()

    # ------------------------------------------------------------------
    # Figure 2: 2x2 grid, one subplot per pattern
    # ------------------------------------------------------------------
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), sharex=True, sharey=True)
    axes = axes.flatten()
    for ax, pattern in zip(axes, PATTERNS):
        pts = data.get(pattern, [])
        if not pts:
            ax.text(0.5, 0.5, "no data\n(no decisions in counted runs)",
                    ha="center", va="center", fontsize=11,
                    transform=ax.transAxes, color="grey")
            ax.set_title(f"{pattern.capitalize()}  (n=0)", fontsize=11)
            ax.set_xlim(-srd_span, srd_span)
            ax.set_ylim(-ses_span, ses_span)
            ax.axhline(0, color="black", linestyle="--", alpha=0.5, linewidth=0.8)
            ax.axvline(0, color="black", linestyle="--", alpha=0.5, linewidth=0.8)
            ax.grid(True, alpha=0.3)
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.scatter(xs, ys, s=45, alpha=0.7,
                   color=PATTERN_COLORS[pattern],
                   edgecolor="black", linewidth=0.4)
        ax.axhline(0, color="black", linestyle="--", alpha=0.5, linewidth=0.8)
        ax.axvline(0, color="black", linestyle="--", alpha=0.5, linewidth=0.8)
        ax.set_title(
            f"{pattern.capitalize()}  "
            f"(n={len(pts)}, median SRD={statistics.median(xs):.1f} s, "
            f"median SES={statistics.median(ys):.2f})",
            fontsize=10,
        )
        ax.set_xlim(-srd_span, srd_span)
        ax.set_ylim(-ses_span, ses_span)
        ax.set_xlabel("SRD (s)")
        ax.set_ylabel("SES")
        ax.grid(True, alpha=0.3)

    fig.suptitle("SRD × SES Scatter by Workload Pattern — 2×2 Panel View",
                 fontsize=12)
    plt.tight_layout()
    outpath = PLOTS_DIR / "srd_vs_ses_scatter_grid.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


if __name__ == "__main__":
    main()
