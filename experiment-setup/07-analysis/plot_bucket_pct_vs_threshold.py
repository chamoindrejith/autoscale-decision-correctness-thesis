#!/usr/bin/env python3
"""
plot_bucket_pct_vs_threshold.py — Line-chart view of bucket composition
as a function of the SLO threshold.

Reads:
  - results/srd_sensitivity_summary.csv   (produced by plot_srd_sensitivity.py)

Writes:
  - results/plots/bucket_pct_vs_threshold.png            (2x2 grid, one panel per pattern)
  - results/plots/bucket_pct_vs_threshold_overall.png    (aggregate across patterns)

Story this figure tells
-----------------------
At tight thresholds (250 ms), sustained breaches are frequent, so most
scale-ups become "Correct but Late". As the threshold loosens, breaches
disappear and decisions collapse into "Unnecessary" (HPA fired without
an SLO breach following) or "Correct & Timely" (pre-emptive scale-up or
a scale-down). This makes the reframed 500 ms result visible in a single
image: the "Late" line vanishes near the industry-standard 500 ms mark
because the 75 % HPA CPU target prevents most sustained breaches on this
workload.

Run:
    python3 plot_bucket_pct_vs_threshold.py
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "results" / "srd_sensitivity_summary.csv"
PLOTS_DIR = ROOT / "results" / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

PATTERNS = ["step", "burst", "ramp", "noisy"]
BUCKETS = [
    ("bucket_correct_timely",   "Correct & Timely", "#2ca02c"),
    ("bucket_correct_but_late", "Correct but Late", "#ff7f0e"),
    ("bucket_unnecessary",      "Unnecessary",      "#7f7f7f"),
    ("bucket_ineffective",      "Ineffective",      "#d62728"),
]


def load() -> dict[tuple[int, str], dict[str, int]]:
    if not INPUT.exists():
        print(f"ERROR: {INPUT} missing — run plot_srd_sensitivity.py first",
              file=sys.stderr)
        sys.exit(1)
    out: dict[tuple[int, str], dict[str, int]] = {}
    with open(INPUT) as f:
        for r in csv.DictReader(f):
            key = (int(r["threshold_ms"]), r["pattern"])
            out[key] = {k: int(v) for k, v in r.items()
                        if k not in {"threshold_ms", "pattern"}}
    return out


def main() -> None:
    data = load()
    thresholds = sorted({t for t, _ in data.keys()})
    print(f"Loaded {len(data)} rows across {len(thresholds)} thresholds "
          f"({thresholds}) x {len(PATTERNS)} patterns")

    # ------------------------------------------------------------------
    # Figure 1: 2x2 grid, one panel per pattern
    # ------------------------------------------------------------------
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5),
                             sharex=True, sharey=True)
    axes = axes.flatten()
    for ax, pattern in zip(axes, PATTERNS):
        # collect y-values per bucket
        for col, label, colour in BUCKETS:
            ys = []
            for thr in thresholds:
                row = data.get((thr, pattern), {})
                n = row.get("n_decisions", 0)
                pct = (100.0 * row.get(col, 0) / n) if n else 0.0
                ys.append(pct)
            # skip ineffective if always 0
            if all(y == 0 for y in ys) and label == "Ineffective":
                continue
            ax.plot(thresholds, ys, marker="o", linewidth=2,
                    color=colour, label=label)

        # annotate n per threshold underneath
        n_row = [data.get((thr, pattern), {}).get("n_decisions", 0)
                 for thr in thresholds]
        ax.set_title(f"{pattern.capitalize()}  "
                     f"(n per threshold: {', '.join(map(str, n_row))})",
                     fontsize=10)
        ax.set_xlabel("SLO threshold (ms)")
        ax.set_ylabel("% of decisions")
        ax.set_xticks(thresholds)
        ax.set_ylim(-3, 103)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5),
                  fontsize=8, framealpha=0.95)

    fig.suptitle("Bucket composition vs SLO threshold — one panel per workload pattern\n"
                 "As the threshold loosens, breach-driven 'Correct but Late' collapses "
                 "and 'Unnecessary' rises",
                 fontsize=12)
    plt.tight_layout()
    outpath = PLOTS_DIR / "bucket_pct_vs_threshold.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()

    # ------------------------------------------------------------------
    # Figure 2: aggregate across all patterns
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for col, label, colour in BUCKETS:
        ys = []
        for thr in thresholds:
            total = sum(data.get((thr, p), {}).get("n_decisions", 0)
                        for p in PATTERNS)
            hit = sum(data.get((thr, p), {}).get(col, 0) for p in PATTERNS)
            ys.append((100.0 * hit / total) if total else 0.0)
        if all(y == 0 for y in ys) and label == "Ineffective":
            continue
        ax.plot(thresholds, ys, marker="o", linewidth=2.4,
                color=colour, label=label)

    ax.set_xlabel("SLO threshold (ms)", fontsize=11)
    ax.set_ylabel("% of decisions", fontsize=11)
    ax.set_title("Bucket composition vs SLO threshold — all workload patterns aggregated\n"
                 "The 'Correct but Late' bucket vanishes near 500 ms because the 75 % HPA "
                 "target prevents most sustained breaches",
                 fontsize=12)
    ax.set_xticks(thresholds)
    ax.set_ylim(-3, 103)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=10, framealpha=0.95)
    # annotate the primary 500 ms line for reader anchoring
    ax.axvline(500, color="black", linestyle=":", alpha=0.5, linewidth=1)
    ax.text(505, 96, "Primary threshold\n(500 ms)", fontsize=8, alpha=0.7)
    plt.tight_layout()
    outpath = PLOTS_DIR / "bucket_pct_vs_threshold_overall.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


if __name__ == "__main__":
    main()
