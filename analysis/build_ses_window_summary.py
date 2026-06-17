#!/usr/bin/env python3
"""
build_ses_window_summary.py — Create an Excel-friendly summary CSV of the SES
window data: one row per (decision_id, window) showing aggregate statistics
of the latency values in that window rather than every individual request.

Reads:
  - results/ses_input_dataset.csv

Writes:
  - results/ses_window_summary.csv  (~1000 rows, ~100 KB; opens instantly in Excel)
"""
import csv
import statistics
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "results" / "ses_input_dataset.csv"
OUTPUT = ROOT / "results" / "ses_window_summary.csv"

def q(values, percentile):
    if not values:
        return None
    s = sorted(values)
    idx = int(percentile * (len(s) - 1))
    return s[idx]

# Group rows by (decision_id, window)
groups = defaultdict(list)
context = {}  # decision_id -> (pattern, run_label, direction, decision_ts)

with open(INPUT) as f:
    for row in csv.DictReader(f):
        key = (int(row['decision_id']), row['window'])
        groups[key].append(float(row['latency_ms']))
        context[int(row['decision_id'])] = (
            row['pattern'], row['run_label'], row['direction'],
            row['decision_timestamp_utc'],
        )

print(f"Grouped {sum(len(v) for v in groups.values()):,} requests into "
      f"{len(groups):,} (decision_id, window) groups")

# Write summary
with open(OUTPUT, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow([
        'decision_id', 'pattern', 'run_label', 'direction',
        'decision_timestamp_utc', 'window',
        'n_requests', 'mean_ms', 'median_ms', 'p90_ms', 'p95_ms', 'p99_ms',
        'min_ms', 'max_ms', 'stddev_ms',
    ])
    for (decision_id, window), values in sorted(groups.items()):
        pattern, run_label, direction, ts = context[decision_id]
        n = len(values)
        w.writerow([
            decision_id, pattern, run_label, direction, ts, window,
            n,
            round(statistics.mean(values), 3),
            round(statistics.median(values), 3),
            round(q(values, 0.90), 3),
            round(q(values, 0.95), 3),
            round(q(values, 0.99), 3),
            round(min(values), 3),
            round(max(values), 3),
            round(statistics.stdev(values), 3) if n > 1 else 0,
        ])

import os
size_kb = os.path.getsize(OUTPUT) / 1024
print(f"Wrote {OUTPUT.name} ({size_kb:.1f} KB)")
