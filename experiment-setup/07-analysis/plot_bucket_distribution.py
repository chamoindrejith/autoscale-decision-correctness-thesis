#!/usr/bin/env python3
"""
plot_bucket_distribution.py — Bar chart of the 4-bucket correctness
classification counts per workload pattern.

Uses the proposal-aligned SRD × SES classification (per research
proposal Section 9), applied by classify_decisions_v3.py:

  Scale-UP decisions (evaluated in priority order):
    srd_source = "no_slo_breach"                   → Unnecessary
    ses is null                                    → Undefined
    ses < -tau                                     → Ineffective
    |ses| ≤ tau (near zero)                        → Unnecessary
    ses > +tau AND srd ≤ 0                         → Correct & Timely
    ses > +tau AND srd > 0                         → Correct but Late

  Scale-DOWN decisions:
    otherwise                                      → Correct & Timely
    (SES not applicable to scale-downs; a premature scale-down would
     surface as a subsequent scale-up whose SRD > 0)

where tau (SES_NEAR_ZERO_TAU) is the "near-zero" threshold for SES,
default 0.05 — SES changes below 5 % treated as effectively no change.

This is the CANONICAL classifier for Chapter 4 results. The earlier
CPU-threshold classifier (30/60, calibrated for the pilot 30 % HPA
target) and the SRD-only classifier (v2) are preserved only as legacy
files (classify_decisions_v1_pilot.py, classify_decisions_v2_srd_only.py)
and are not part of the primary pipeline.

Produces two figures:

  1. All decisions (including warm-up runs 1-3) — the raw campaign view
  2. Counted decisions only (warm-up excluded, runs 4-23) — the analysis view

Reads:
  - results/decisions_with_ses.csv (has bucket, srd_seconds, srd_source,
                                     scaling_limited, scaling_limit_reason)

Writes:
  - results/plots/bucket_distribution.png (side-by-side)
  - results/plots/bucket_distribution_counted_only.png (single panel)
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "results" / "decisions_with_ses.csv"
PLOTS_DIR = ROOT / "results" / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

# Order matters — bars will be drawn left-to-right in this order.
PATTERNS = ["step", "burst", "ramp", "noisy"]

# Same order and colours as the classification_rules.pdf.
BUCKETS = [
    "Correct & Timely",
    "Correct but Late",
    "Unnecessary",
    "Ineffective",
]
BUCKET_COLORS = {
    "Correct & Timely":  "#2ca02c",   # green — HPA did the right thing on time
    "Correct but Late":  "#ff7f0e",   # orange — HPA did the right thing, late
    "Unnecessary":       "#7f7f7f",   # grey — HPA scaled when it didn't need to
    "Ineffective":       "#d62728",   # red — HPA hit a hard ceiling
}

# Warm-up run threshold (per methodology): discard runs 1-3 of each pattern.
WARMUP_LAST_RUN_NUM = 3


def srd_bucket(row: dict) -> str:
    """Assign a bucket using the SRD-based rules (Option B — see module docstring).

    Priority order:
      1. If HPA hit a hard cap (scaling_limited=True with matching reason)
         → Ineffective, regardless of direction.
      2. For scale-DOWN decisions (SRD not meaningful) → Correct & Timely.
      3. For scale-UP decisions, dispatch on srd_source / srd_seconds.
    """
    direction = (row.get("direction") or "").lower()
    scaling_limited = (row.get("scaling_limited", "").strip() == "True")
    limit_reason = (row.get("scaling_limit_reason") or "").strip()

    # Rule 1: hard cap
    if scaling_limited:
        if (direction == "up" and limit_reason == "TooManyReplicas") or \
           (direction == "down" and limit_reason == "TooFewReplicas"):
            return "Ineffective"

    # Rule 2: scale-DOWN — SRD not meaningful
    if direction == "down":
        return "Correct & Timely"

    # Rule 3: scale-UP — dispatch on SRD outcome
    src = (row.get("srd_source") or "").strip()
    if src == "no_slo_breach":
        return "Unnecessary"

    if src in ("late", "pre_emptive"):
        try:
            srd = float(row.get("srd_seconds") or "nan")
        except ValueError:
            srd = float("nan")
        if srd != srd:  # NaN
            return "Unnecessary"
        return "Correct but Late" if srd > 0 else "Correct & Timely"

    # Fallback — should not happen, but classify defensively.
    return "Unnecessary"


def load_decisions(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path) as f:
        for r in csv.DictReader(f):
            # Skip decisions that never got tagged to a run (between_runs)
            if not r.get("pattern") or r["pattern"] not in PATTERNS:
                continue
            # Convert run_num to int; leave None if blank
            run_num_raw = r.get("run_num") or ""
            try:
                r["_run_num"] = int(run_num_raw) if run_num_raw else None
            except ValueError:
                r["_run_num"] = None
            # Prefer the persisted SRD-based bucket produced by
            # classify_decisions_v2.py. If that column is missing (because
            # the v2 classifier hasn't been run), compute the same rule
            # inline as a fallback.
            r["bucket"] = (r.get("bucket_v3") or srd_bucket(r))
            rows.append(r)
    return rows


def count_by_pattern_bucket(rows: list[dict]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        counts[(r["pattern"], r["bucket"])] += 1
    return counts


def render_bar_chart(ax, counts: dict[tuple[str, str], int], title: str) -> None:
    """Draw a grouped bar chart onto the given matplotlib axes."""
    x = np.arange(len(PATTERNS))
    bar_width = 0.19

    for i, bucket in enumerate(BUCKETS):
        values = [counts.get((p, bucket), 0) for p in PATTERNS]
        offset = (i - (len(BUCKETS) - 1) / 2) * bar_width
        bars = ax.bar(
            x + offset,
            values,
            bar_width,
            label=bucket,
            color=BUCKET_COLORS[bucket],
            edgecolor="black",
            linewidth=0.5,
        )
        # Value labels above each bar (only if > 0 to avoid clutter)
        for bar, v in zip(bars, values):
            if v > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.5,
                    str(v),
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )

    ax.set_xticks(x)
    ax.set_xticklabels([p.capitalize() for p in PATTERNS])
    ax.set_ylabel("Number of HPA decisions")
    ax.set_title(title)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend(loc="upper right", fontsize=9, framealpha=0.95)


def main() -> None:
    if not INPUT.exists():
        print(f"ERROR: input file not found: {INPUT}", file=sys.stderr)
        sys.exit(1)

    all_rows = load_decisions(INPUT)
    counted_rows = [
        r for r in all_rows
        if r["_run_num"] is not None and r["_run_num"] > WARMUP_LAST_RUN_NUM
    ]

    all_counts = count_by_pattern_bucket(all_rows)
    counted_counts = count_by_pattern_bucket(counted_rows)

    print(f"Loaded {len(all_rows)} decisions across all runs")
    print(f"  of which {len(counted_rows)} are in counted runs "
          f"(run > {WARMUP_LAST_RUN_NUM})")
    print()
    print("=== Bucket counts (all runs) ===")
    for p in PATTERNS:
        for b in BUCKETS:
            n = all_counts.get((p, b), 0)
            if n:
                print(f"  {p:<6} {b:<20} {n}")
    print()
    print("=== Bucket counts (counted runs only) ===")
    for p in PATTERNS:
        for b in BUCKETS:
            n = counted_counts.get((p, b), 0)
            if n:
                print(f"  {p:<6} {b:<20} {n}")

    # ---------------------------------------------------------------
    # Figure 1: side-by-side comparison (all runs vs counted runs)
    # ---------------------------------------------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5.8), sharey=False)

    render_bar_chart(
        ax1,
        all_counts,
        f"All decisions (n = {len(all_rows)}) — includes warm-up runs 1-3",
    )
    render_bar_chart(
        ax2,
        counted_counts,
        f"Counted decisions only (n = {len(counted_rows)}) — warm-up excluded",
    )

    fig.suptitle(
        "HPA Decision Correctness Buckets by Workload Pattern\n"
        "(Proposal-aligned SRD × SES classification: 'Late' = HPA fired "
        "after the 500 ms SLO breach AND latency improved after scaling)",
        fontsize=12,
        y=1.03,
    )
    plt.tight_layout()

    outpath = PLOTS_DIR / "bucket_distribution.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"\nSaved {outpath.name}")
    plt.close()

    # ---------------------------------------------------------------
    # Figure 2: counted-only single panel (thesis-figure clean version)
    # ---------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(10, 6))
    render_bar_chart(
        ax,
        counted_counts,
        f"HPA Decision Correctness Buckets by Workload Pattern "
        f"(n = {len(counted_rows)} counted decisions; "
        f"proposal-aligned SRD × SES classifier)",
    )
    ax.set_xlabel("Workload pattern")
    plt.tight_layout()

    outpath = PLOTS_DIR / "bucket_distribution_counted_only.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


if __name__ == "__main__":
    main()
