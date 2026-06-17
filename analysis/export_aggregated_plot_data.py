#!/usr/bin/env python3
"""
export_aggregated_plot_data.py — Save the aggregated time-binned latency data
that was used to build all_decisions_per_pattern_grid.png (and the overlay).

This produces the underlying CSV so reviewers can reproduce the figure or
re-plot it in Excel / R / matplotlib without re-running the heavy aggregation.

Reads:
  - results/ses_input_dataset.csv

Writes:
  - results/aggregated_latency_per_pattern.csv

CSV schema (one row per pattern × time bin):
  pattern             — step | burst | ramp | noisy
  time_bin_seconds    — bin center, seconds relative to HPA decision
  n_requests          — number of requests aggregated in this bin
  median_ms           — median latency in this bin (the solid line in plot)
  p25_ms              — 25th percentile (lower edge of shaded band)
  p75_ms              — 75th percentile (upper edge of shaded band)
  iqr_ms              — p75 - p25 (width of shaded band)
  p95_ms              — also useful for SLO-style analysis
  mean_ms             — for users who prefer mean
"""
import csv
from pathlib import Path
from collections import defaultdict
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "results" / "ses_input_dataset.csv"
OUTPUT = ROOT / "results" / "aggregated_latency_per_pattern.csv"

BIN_SECONDS = 2          # same as in plot script
T_MIN = -60
T_MAX = 90
PATTERNS = ['step', 'burst', 'ramp', 'noisy']

# bins[pattern][bin_center] = list of latencies
bins = defaultdict(lambda: defaultdict(list))
total = 0
kept = 0
print("Streaming ses_input_dataset.csv...")
with open(INPUT) as f:
    r = csv.DictReader(f)
    for row in r:
        total += 1
        try:
            t_rel = float(row['request_seconds_relative_to_decision'])
            lat = float(row['latency_ms'])
        except (KeyError, ValueError):
            continue
        if t_rel < T_MIN or t_rel > T_MAX:
            continue
        pattern = row['pattern']
        if pattern not in PATTERNS:
            continue
        bin_index = int(t_rel // BIN_SECONDS)
        bin_center = bin_index * BIN_SECONDS + BIN_SECONDS / 2
        bins[pattern][bin_center].append(lat)
        kept += 1
print(f"Streamed {total:,} rows, kept {kept:,} in plot range")

# Aggregate
rows_out = []
for pattern in PATTERNS:
    if pattern not in bins:
        continue
    for bc in sorted(bins[pattern].keys()):
        vals = np.array(bins[pattern][bc])
        rows_out.append({
            'pattern':          pattern,
            'time_bin_seconds': bc,
            'n_requests':       len(vals),
            'median_ms':        round(float(np.median(vals)), 3),
            'p25_ms':           round(float(np.percentile(vals, 25)), 3),
            'p75_ms':           round(float(np.percentile(vals, 75)), 3),
            'iqr_ms':           round(float(np.percentile(vals, 75) - np.percentile(vals, 25)), 3),
            'p95_ms':           round(float(np.percentile(vals, 95)), 3),
            'mean_ms':          round(float(np.mean(vals)), 3),
        })

with open(OUTPUT, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
    w.writeheader()
    w.writerows(rows_out)

print(f"\nWrote {len(rows_out)} rows to {OUTPUT.name}")
print(f"Breakdown:")
counts = defaultdict(int)
for r in rows_out:
    counts[r['pattern']] += 1
for pat in PATTERNS:
    if pat in counts:
        print(f"  {pat}: {counts[pat]} bins")
