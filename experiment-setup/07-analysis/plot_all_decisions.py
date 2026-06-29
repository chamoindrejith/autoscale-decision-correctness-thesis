#!/usr/bin/env python3
"""
plot_all_decisions.py — Aggregate latency time-series across ALL decisions
in each pattern, producing a single figure showing how the average latency
profile around HPA decisions differs by workload pattern.

Approach:
  1. For every request in ses_input_dataset.csv, assign it to a 2-second time
     bin relative to its decision (-60 to +90 seconds).
  2. For each (pattern, time_bin), compute the median and 25th/75th percentile
     of all latency values across all decisions.
  3. Plot one line per pattern showing median latency vs time, with a shaded
     band for the inter-quartile range.

Outputs:
  - results/plots/all_decisions_per_pattern_overlay.png  (single-axes overlay)
  - results/plots/all_decisions_per_pattern_grid.png     (2x2 grid, one per pattern)
"""
import csv
from pathlib import Path
from collections import defaultdict
import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "results" / "ses_input_dataset.csv"
PLOTS_DIR = ROOT / "results" / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

BIN_SECONDS = 2  # 2-second time bins for aggregation
T_MIN = -60      # plot from 60s before
T_MAX = 90       # to 90s after
PATTERN_COLORS = {
    'step':  '#1f77b4',
    'burst': '#ff7f0e',
    'ramp':  '#2ca02c',
    'noisy': '#d62728',
}

print("Streaming ses_input_dataset.csv...")
# bins[pattern][bin_center] = list of latencies
bins = defaultdict(lambda: defaultdict(list))
total_rows = 0
with open(INPUT) as f:
    r = csv.DictReader(f)
    for row in r:
        total_rows += 1
        try:
            t_rel = float(row['request_seconds_relative_to_decision'])
            lat = float(row['latency_ms'])
        except (KeyError, ValueError):
            continue
        if t_rel < T_MIN or t_rel > T_MAX:
            continue
        pattern = row['pattern']
        if pattern not in PATTERN_COLORS:
            continue
        # Find bin center
        bin_index = int(t_rel // BIN_SECONDS)
        bin_center = bin_index * BIN_SECONDS + BIN_SECONDS / 2
        bins[pattern][bin_center].append(lat)
print(f"Processed {total_rows:,} rows")

# Aggregate: for each (pattern, bin) compute median, p25, p75
agg = {}
for pattern, bin_dict in bins.items():
    bin_centers = sorted(bin_dict.keys())
    median_vals = []
    p25_vals = []
    p75_vals = []
    for bc in bin_centers:
        vals = np.array(bin_dict[bc])
        median_vals.append(np.median(vals))
        p25_vals.append(np.percentile(vals, 25))
        p75_vals.append(np.percentile(vals, 75))
    agg[pattern] = {
        'centers': np.array(bin_centers),
        'median': np.array(median_vals),
        'p25': np.array(p25_vals),
        'p75': np.array(p75_vals),
    }

# ===========================================================================
# PLOT 1: Overlay — all four patterns on the same axes
# ===========================================================================
fig, ax = plt.subplots(figsize=(12, 6))
for pattern in ['step', 'burst', 'ramp', 'noisy']:
    if pattern not in agg:
        continue
    d = agg[pattern]
    color = PATTERN_COLORS[pattern]
    ax.fill_between(d['centers'], d['p25'], d['p75'], color=color, alpha=0.15)
    ax.plot(d['centers'], d['median'], color=color, linewidth=2,
            label=f"{pattern.capitalize()}")
ax.axvline(0, color='black', linestyle='--', alpha=0.6,
           label='HPA decision moment (t=0)')
ax.axvspan(0, 30, color='gray', alpha=0.1)
ax.text(15, ax.get_ylim()[1]*0.95, '30s offset\n(skipped)',
        ha='center', va='top', fontsize=8, color='gray')
ax.set_xlabel("Seconds relative to HPA decision")
ax.set_ylabel("Request latency (ms) — median across all decisions")
ax.set_title("Aggregated Latency Profile Around HPA Decisions\n"
             "Shaded band = inter-quartile range (25th–75th percentile)")
ax.legend(loc='upper left')
ax.grid(True, alpha=0.3)
plt.tight_layout()
overlay_path = PLOTS_DIR / "all_decisions_per_pattern_overlay.png"
plt.savefig(overlay_path, dpi=150)
print(f"Saved {overlay_path.name}")
plt.close()

# ===========================================================================
# PLOT 2: 2x2 grid — separate panel per pattern
# ===========================================================================
fig, axes = plt.subplots(2, 2, figsize=(13, 8), sharex=True, sharey=False)
axes = axes.flatten()
for ax, pattern in zip(axes, ['step', 'burst', 'ramp', 'noisy']):
    if pattern not in agg:
        ax.set_title(f"{pattern.capitalize()} (no data)")
        continue
    d = agg[pattern]
    color = PATTERN_COLORS[pattern]
    ax.fill_between(d['centers'], d['p25'], d['p75'], color=color, alpha=0.25,
                    label='IQR (p25-p75)')
    ax.plot(d['centers'], d['median'], color=color, linewidth=2, label='Median')
    ax.axvline(0, color='black', linestyle='--', alpha=0.6)
    ax.axvspan(0, 30, color='gray', alpha=0.1)
    ax.set_title(f"{pattern.capitalize()} pattern")
    ax.set_xlabel("Seconds relative to HPA decision")
    ax.set_ylabel("Latency (ms)")
    ax.legend(loc='upper left', fontsize=8)
    ax.grid(True, alpha=0.3)
fig.suptitle("Latency Around HPA Decisions — by Workload Pattern", fontsize=13)
plt.tight_layout()
grid_path = PLOTS_DIR / "all_decisions_per_pattern_grid.png"
plt.savefig(grid_path, dpi=150)
print(f"Saved {grid_path.name}")
plt.close()

print("\nDone — two figures created in results/plots/")
