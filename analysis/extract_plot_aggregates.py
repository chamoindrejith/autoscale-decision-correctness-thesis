#!/usr/bin/env python3
"""
extract_plot_aggregates.py — Output the aggregated per-pattern × time-bin
data used to plot all_decisions_per_pattern_grid.png and the overlay version.

For each (pattern, time_bin), compute:
  - n_requests:  how many k6 requests contributed
  - p25_ms, median_ms (p50), p75_ms, p95_ms
  - mean_ms (for comparison)

Time bins: 2-second wide, from -60s (60 seconds before decision)
to +90s (90 seconds after decision).

Reads:
  - results/ses_input_dataset.csv

Writes:
  - results/plot_aggregates_by_pattern.csv  (~300 rows, ~25 KB)
"""
import csv
from pathlib import Path
from collections import defaultdict
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "results" / "ses_input_dataset.csv"
OUTPUT = ROOT / "results" / "plot_aggregates_by_pattern.csv"

BIN_SECONDS = 2
T_MIN = -60
T_MAX = 90
PATTERNS = ('step', 'burst', 'ramp', 'noisy')

print("Streaming ses_input_dataset.csv...")
# bins[pattern][bin_center] = list of latencies
bins = defaultdict(lambda: defaultdict(list))
total_rows = 0
included_rows = 0
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
        if pattern not in PATTERNS:
            continue
        bin_index = int(t_rel // BIN_SECONDS)
        bin_center = bin_index * BIN_SECONDS + BIN_SECONDS / 2
        bins[pattern][bin_center].append(lat)
        included_rows += 1

print(f"Processed {total_rows:,} rows, included {included_rows:,} in window")

# Write CSV
rows = []
for pattern in PATTERNS:
    bin_dict = bins.get(pattern, {})
    for bin_center in sorted(bin_dict.keys()):
        vals = np.array(bin_dict[bin_center])
        rows.append({
            'pattern': pattern,
            'time_bin_center_seconds': bin_center,
            'n_requests': len(vals),
            'mean_ms': round(float(np.mean(vals)), 3),
            'p25_ms': round(float(np.percentile(vals, 25)), 3),
            'median_ms': round(float(np.median(vals)), 3),
            'p75_ms': round(float(np.percentile(vals, 75)), 3),
            'p95_ms': round(float(np.percentile(vals, 95)), 3),
        })

with open(OUTPUT, 'w', newline='') as f:
    fieldnames = ['pattern', 'time_bin_center_seconds', 'n_requests',
                  'mean_ms', 'p25_ms', 'median_ms', 'p75_ms', 'p95_ms']
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)

print(f"Wrote {len(rows)} rows to {OUTPUT.name}")
print(f"File size: {OUTPUT.stat().st_size / 1024:.1f} KB")

# Preview
print("\nFirst 5 rows:")
for r in rows[:5]:
    print(f"  {r}")
