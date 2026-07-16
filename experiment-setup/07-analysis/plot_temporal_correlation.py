#!/usr/bin/env python3
"""
plot_temporal_correlation.py — Temporal correlation analysis.

Addresses the proposal's "Temporal correlation analysis" analytical
method. Answers two questions per workload pattern:

  1. Does HPA react more slowly late in a run than early in a run?
     (correlation between SRD magnitude and time-within-run of the
      decision)

  2. Are successive HPA decisions within a run temporally related?
     (autocorrelation of inter-decision gaps)

Produces one figure with a 2 x 4 grid:
  - Row 1: SRD vs time-within-run scatter, per pattern (with Pearson r
    and linear-regression line)
  - Row 2: cumulative decision count vs time-within-run, per pattern
    (per-run trajectories overlaid; steeper slope = HPA firing more
    frequently at that phase of the run)

Reads:
  - results/decisions_with_ses.csv
  - results/run_index.csv

Writes:
  - results/plots/temporal_correlation.png
"""
from __future__ import annotations

import csv
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
DECISIONS_CSV = RESULTS / "decisions_with_ses.csv"
RUN_INDEX_CSV = RESULTS / "run_index.csv"
PLOTS_DIR = RESULTS / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

PATTERNS = ["step", "burst", "ramp", "noisy"]
PATTERN_COLORS = {
    "step":  "#1f77b4",
    "burst": "#ff7f0e",
    "ramp":  "#2ca02c",
    "noisy": "#d62728",
}
WARMUP_LAST_RUN_NUM = 3


def parse_iso(s):
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.+?\.)(\d+)(.+)$", s)
    if m:
        micros = m.group(2)[:6].ljust(6, "0")
        s = m.group(1) + micros + m.group(3)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_run_starts():
    """Return {run_label: start_utc_datetime} from run_index.csv."""
    starts = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            try:
                starts[r["run_label"]] = parse_iso(r["start_utc"])
            except (KeyError, ValueError):
                continue
    return starts


def load_decisions():
    """Return list of dicts with parsed timestamps."""
    out = []
    with open(DECISIONS_CSV) as f:
        for r in csv.DictReader(f):
            pat = (r.get("pattern") or "").strip()
            if pat not in PATTERNS:
                continue
            try:
                rn = int(r.get("run_num") or 0)
            except ValueError:
                continue
            if rn <= WARMUP_LAST_RUN_NUM:
                continue
            try:
                r["_ts"] = parse_iso(r["timestamp_utc"])
            except (KeyError, ValueError):
                continue
            out.append(r)
    return out


def safe_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def pearson_r(xs, ys):
    """Sample Pearson correlation coefficient. Returns (r, n) or (None, n)."""
    n = min(len(xs), len(ys))
    if n < 3:
        return None, n
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx2 = sum((x - mx) ** 2 for x in xs)
    dy2 = sum((y - my) ** 2 for y in ys)
    if dx2 == 0 or dy2 == 0:
        return None, n
    return num / (dx2 ** 0.5 * dy2 ** 0.5), n


def linear_fit(xs, ys):
    """Ordinary least squares y = a*x + b. Returns (a, b) or (None, None)."""
    if len(xs) < 2:
        return None, None
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None, None
    a = num / den
    b = my - a * mx
    return a, b


def main():
    print("Loading data...")
    starts = load_run_starts()
    decs = load_decisions()
    print(f"  {len(starts)} runs indexed, {len(decs)} counted decisions")

    # -------------------------------------------------------------
    # Row 1 data: (time_within_run_seconds, SRD_seconds) per SCALE-UP
    # with defined SRD, grouped by pattern
    # -------------------------------------------------------------
    row1_data = defaultdict(list)   # pattern -> [(t_within, srd), ...]
    for d in decs:
        if (d.get("direction") or "").lower() != "up":
            continue
        if d.get("srd_source") not in ("late", "pre_emptive"):
            continue
        srd = safe_float(d.get("srd_seconds"))
        if srd is None:
            continue
        rl = d.get("run_label") or ""
        start = starts.get(rl)
        if start is None:
            continue
        t_within = (d["_ts"] - start).total_seconds()
        row1_data[d["pattern"]].append((t_within, srd))

    # -------------------------------------------------------------
    # Row 2 data: per-run cumulative decision count trajectories
    # -------------------------------------------------------------
    row2_data = defaultdict(dict)   # pattern -> {run_label: [(t, cum_n), ...]}
    per_run_decs = defaultdict(list)
    for d in decs:
        rl = d.get("run_label") or ""
        if rl and rl in starts:
            per_run_decs[rl].append(d)
    for rl, dl in per_run_decs.items():
        pattern = dl[0].get("pattern")
        if pattern not in PATTERNS:
            continue
        dl.sort(key=lambda x: x["_ts"])
        start = starts[rl]
        traj = [((d["_ts"] - start).total_seconds(), i + 1)
                for i, d in enumerate(dl)]
        row2_data[pattern][rl] = traj

    # -------------------------------------------------------------
    # Render 2 x 4 grid
    # -------------------------------------------------------------
    fig, axes = plt.subplots(2, 4, figsize=(20, 9))

    # ----- Row 1: SRD vs time-within-run scatter + regression -----
    for col, pattern in enumerate(PATTERNS):
        ax = axes[0][col]
        pts = row1_data.get(pattern, [])
        colour = PATTERN_COLORS[pattern]

        if not pts:
            ax.text(0.5, 0.5, f"no defined-SRD\nscale-ups for {pattern}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=10, color="grey")
            ax.set_title(f"{pattern.upper()}", fontsize=12,
                         fontweight="bold", color=colour, loc="left")
            continue

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        r, n = pearson_r(xs, ys)
        a, b = linear_fit(xs, ys)

        ax.scatter(xs, ys, color=colour, alpha=0.7, s=45,
                   edgecolor="black", linewidth=0.4)

        # Regression line
        if a is not None:
            xrange = [min(xs), max(xs)]
            ax.plot(xrange, [a * x + b for x in xrange],
                    color="black", linestyle="--", alpha=0.7, linewidth=1.5,
                    label=f"slope = {a:+.3f} s / s")

        ax.axhline(0, color="grey", linestyle=":", alpha=0.5)

        subtitle = f"Pearson r = {r:+.3f} (n = {n})" if r is not None \
                   else f"insufficient data (n = {n})"
        ax.set_title(f"{pattern.upper()}\n{subtitle}",
                     fontsize=11, fontweight="bold",
                     color=colour, loc="left")
        ax.set_xlabel("Time within run (s)", fontsize=9)
        if col == 0:
            ax.set_ylabel("SRD (s) — signed", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)

    # ----- Row 2: Cumulative decision count trajectories -----
    for col, pattern in enumerate(PATTERNS):
        ax = axes[1][col]
        traj_by_run = row2_data.get(pattern, {})
        colour = PATTERN_COLORS[pattern]

        if not traj_by_run:
            ax.text(0.5, 0.5, "no data",
                    ha="center", va="center", transform=ax.transAxes,
                    color="grey")
            ax.set_title("(no decisions)", fontsize=10, loc="left")
            continue

        # Plot each run as a step line
        max_x = 0
        max_y = 0
        for rl, traj in sorted(traj_by_run.items()):
            xs = [0] + [t for t, _ in traj]
            ys = [0] + [n for _, n in traj]
            ax.step(xs, ys, where="post", color=colour,
                    alpha=0.35, linewidth=1.0)
            if xs:
                max_x = max(max_x, xs[-1])
            if ys:
                max_y = max(max_y, ys[-1])

        # Median trajectory across runs at each 30-second grid tick
        if traj_by_run:
            grid = list(range(0, int(max_x) + 30, 30))
            medians = []
            for gt in grid:
                per_run_counts = []
                for rl, traj in traj_by_run.items():
                    count_at_gt = sum(1 for t, _ in traj if t <= gt)
                    per_run_counts.append(count_at_gt)
                if per_run_counts:
                    medians.append(statistics.median(per_run_counts))
                else:
                    medians.append(0)
            ax.plot(grid, medians, color="black", linewidth=2.2,
                    label=f"Median across {len(traj_by_run)} runs")

        n_runs = len(traj_by_run)
        total_decs = sum(len(t) for t in traj_by_run.values())
        avg_decs = total_decs / n_runs if n_runs else 0
        ax.set_title(f"{pattern.upper()} — {n_runs} runs, "
                     f"{total_decs} decisions "
                     f"(avg {avg_decs:.1f}/run)",
                     fontsize=11, fontweight="bold",
                     color=colour, loc="left")
        ax.set_xlabel("Time within run (s)", fontsize=9)
        if col == 0:
            ax.set_ylabel("Cumulative decision count", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)

    fig.suptitle(
        "Temporal correlation analysis — HPA decision timing within runs\n"
        "Top row: SRD vs time-within-run scatter per pattern (Pearson r + "
        "OLS regression). Bottom row: cumulative decision count trajectories "
        "per run (thin) with median across runs (thick).",
        fontsize=13, y=0.995,
    )
    plt.tight_layout(rect=[0, 0, 1, 0.955])
    outpath = PLOTS_DIR / "temporal_correlation.png"
    plt.savefig(outpath, dpi=140, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()

    # -------------------------------------------------------------
    # Console summary — key statistics
    # -------------------------------------------------------------
    print()
    print("=" * 78)
    print("TEMPORAL CORRELATION SUMMARY")
    print("=" * 78)
    print(f"{'Pattern':<8} {'n_srd':>6} {'Pearson r':>10} {'slope (s/s)':>13} "
          f"{'Interpretation':<40}")
    print("-" * 78)
    for pattern in PATTERNS:
        pts = row1_data.get(pattern, [])
        if len(pts) < 3:
            print(f"{pattern:<8} {len(pts):>6} {'n/a':>10} {'n/a':>13} "
                  f"insufficient data")
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        r, _ = pearson_r(xs, ys)
        a, _ = linear_fit(xs, ys)
        if r is None:
            interp = "no variance"
        elif abs(r) < 0.15:
            interp = "no discernible correlation"
        elif abs(r) < 0.30:
            interp = ("weak positive: SRD grows late in run"
                      if r > 0 else "weak negative: SRD shrinks late in run")
        elif abs(r) < 0.60:
            interp = ("moderate positive: HPA slower late in run"
                      if r > 0 else "moderate negative: HPA faster late in run")
        else:
            interp = ("strong positive: HPA visibly slower late"
                      if r > 0 else "strong negative: HPA visibly faster late")
        print(f"{pattern:<8} {len(pts):>6} {r:>+10.3f} "
              f"{a:>+13.4f} {interp:<40}")


if __name__ == "__main__":
    main()
