#!/usr/bin/env python3
"""
plot_decision_latency.py — Plot the latency time-series around a specific
HPA decision, showing requests before and after.

Usage:
    python3 plot_decision_latency.py 25            # plot decision_id=25
    python3 plot_decision_latency.py 25 --save     # save PNG instead of showing

The plot shows:
  - Each blue dot: a request in the "before" window
  - Each orange dot: a request in the "after" window
  - Red dashed line: the moment the HPA decision fired
  - Horizontal grey lines: the p95 latency in each window
"""
import sys
import argparse
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "results" / "ses_input_dataset.csv"
DECISIONS = ROOT / "results" / "decisions_with_ses.csv"
PLOTS_DIR = ROOT / "results" / "plots"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("decision_id", type=int, help="Which HPA decision to plot")
    ap.add_argument("--save", action="store_true",
                    help="Save to PNG instead of showing interactively")
    args = ap.parse_args()

    # Stream through the file collecting only rows for the target decision_id.
    # This avoids loading 91 MB into memory.
    # We can stop early once we've seen the target AND the current row is from
    # a different decision (because within each pattern the rows are contiguous).
    print(f"Streaming data for decision {args.decision_id}...")
    target = str(args.decision_id)
    import csv as csvmod
    rows = []
    with open(INPUT) as f:
        reader = csvmod.DictReader(f)
        in_block = False
        for row in reader:
            if row['decision_id'] == target:
                rows.append(row)
                in_block = True
            elif in_block:
                # Just exited the contiguous block for this decision.
                # The file is built per-pattern, with decisions contiguous within
                # each pattern, so we can break here.
                break
    if not rows:
        print(f"ERROR: no rows found for decision_id={args.decision_id}")
        sys.exit(1)
    d = pd.DataFrame(rows)
    d['request_seconds_relative_to_decision'] = d['request_seconds_relative_to_decision'].astype(float)
    d['latency_ms'] = d['latency_ms'].astype(float)
    print(f"  Collected {len(d)} rows for this decision")

    # Sort by time relative to decision
    d = d.sort_values('request_seconds_relative_to_decision')

    # Split into before/after for plotting
    before = d[d['window'] == 'before']
    after = d[d['window'] == 'after']

    # Look up the computed SES + p95s from decisions_with_ses.csv
    decisions = pd.read_csv(DECISIONS)
    info = decisions[decisions['decision_id'] == args.decision_id]
    if not info.empty:
        info = info.iloc[0]
        ses = info.get('ses')
        p95_before = info.get('p95_before_ms')
        p95_after = info.get('p95_after_ms')
    else:
        ses = p95_before = p95_after = None

    # Get context from the first row
    pattern = d.iloc[0]['pattern']
    run_label = d.iloc[0]['run_label']
    direction = d.iloc[0]['direction']

    # Build the plot
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.scatter(before['request_seconds_relative_to_decision'], before['latency_ms'],
               s=6, alpha=0.5, color='steelblue', label=f'Before (n={len(before)})')
    ax.scatter(after['request_seconds_relative_to_decision'], after['latency_ms'],
               s=6, alpha=0.5, color='darkorange', label=f'After (n={len(after)})')

    # Mark the decision moment with a red dashed line
    ax.axvline(0, color='red', linestyle='--', alpha=0.8,
               label='HPA decision (t=0)')

    # Optionally overlay the computed p95 levels
    if p95_before is not None and not pd.isna(p95_before):
        ax.axhline(p95_before, color='steelblue', linestyle=':', alpha=0.7,
                   label=f'p95 before = {p95_before:.1f} ms')
    if p95_after is not None and not pd.isna(p95_after):
        ax.axhline(p95_after, color='darkorange', linestyle=':', alpha=0.7,
                   label=f'p95 after = {p95_after:.1f} ms')

    # Labels
    ax.set_xlabel("Seconds relative to HPA decision")
    ax.set_ylabel("Request latency (ms)")
    title = f"Decision {args.decision_id} — {run_label} ({direction}-scaling)"
    if ses is not None and not pd.isna(ses):
        title += f"\nSES = {ses:.3f}"
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper right', fontsize=9)
    plt.tight_layout()

    if args.save:
        PLOTS_DIR.mkdir(exist_ok=True)
        outpath = PLOTS_DIR / f"decision_{args.decision_id:04d}_latency.png"
        plt.savefig(outpath, dpi=150)
        print(f"Saved {outpath}")
    else:
        plt.show()


if __name__ == '__main__':
    main()
