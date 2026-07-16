#!/usr/bin/env python3
"""
plot_discussion.py — Chapter 5 discussion figures and tables.

Generates two items suitable for Chapter 5 (Discussion / Threats to
Validity) of the thesis and the viva-voce presentation:

  1. correlation_matrix.png       Heatmap of Pearson correlations
                                   between SRD, SES, cold-start delay,
                                   replica-count delta, current_pct, and
                                   time-within-run per pattern (5 panels).
  2. quality_flags_summary.csv    Per-pattern table of operational
                                   quality signals: number of runs,
                                   total dropped k6 iterations, total
                                   failed checks, per-run averages,
                                   worst run per metric.

Reads:
  - results/decisions_with_ses.csv
  - results/run_index.csv
  - results/logs/*-batch-*.log       (for k6 quality flags)

Writes:
  - results/plots/correlation_matrix.png
  - results/quality_flags_summary.csv
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

# The orchestrator writes per-batch logs to 06-load-tests/logs/
LOGS_DIR = ROOT / "06-load-tests" / "logs"

PATTERNS = ["step", "burst", "ramp", "noisy"]
WARMUP_LAST_RUN_NUM = 3


def parse_iso(s):
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.+?\.)(\d+)(.+)$", s)
    if m:
        micros = m.group(2)[:6].ljust(6, "0")
        s = m.group(1) + micros + m.group(3)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def safe_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def safe_int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def is_counted(r):
    rn = safe_int(r.get("run_num"))
    if rn is None or rn <= WARMUP_LAST_RUN_NUM:
        return False
    return (r.get("pattern") or "").strip() in PATTERNS


def load_decisions():
    with open(DECISIONS_CSV) as f:
        return list(csv.DictReader(f))


def load_run_starts():
    starts = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            try:
                starts[r["run_label"]] = parse_iso(r["start_utc"])
            except (KeyError, ValueError):
                continue
    return starts


# =====================================================================
# 1. CORRELATION MATRIX HEATMAP
# =====================================================================

def _pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx2 = sum((x - mx) ** 2 for x in xs)
    dy2 = sum((y - my) ** 2 for y in ys)
    if dx2 == 0 or dy2 == 0:
        return None
    return num / (dx2 ** 0.5 * dy2 ** 0.5)


def plot_correlation_matrix(decisions, run_starts):
    """One heatmap per pattern showing Pearson r between key metrics."""
    metrics_labels = [
        "SRD (s)", "SES", "cold_start_delay (s)", "Δreplicas",
        "CPU % at decision", "time within run (s)",
    ]

    # Per-pattern rows of (SRD, SES, cold_start, delta_replicas, cpu_pct,
    # time_in_run) for scale-ups only
    per_pat = defaultdict(list)
    for d in decisions:
        if not is_counted(d):
            continue
        if (d.get("direction") or "").lower() != "up":
            continue

        srd = safe_float(d.get("srd_seconds"))
        ses = safe_float(d.get("ses"))
        csd = safe_float(d.get("cold_start_delay_s"))
        before = safe_int(d.get("replicas_before"))
        after = safe_int(d.get("replicas_after"))
        delta = (after - before) if (before is not None and after is not None) else None
        cpu = safe_float(d.get("current_pct"))
        rl = d.get("run_label") or ""
        start = run_starts.get(rl)
        t_within = None
        if start:
            try:
                ts = parse_iso(d["timestamp_utc"])
                t_within = (ts - start).total_seconds()
            except (KeyError, ValueError):
                pass

        row = [srd, ses, csd, delta, cpu, t_within]
        per_pat[d["pattern"]].append(row)

    fig, axes = plt.subplots(1, 4, figsize=(20, 5.5))
    for ax, pattern in zip(axes, PATTERNS):
        rows = per_pat.get(pattern, [])
        n_metrics = len(metrics_labels)
        matrix = np.full((n_metrics, n_metrics), np.nan)
        counts = np.zeros((n_metrics, n_metrics), dtype=int)

        for i in range(n_metrics):
            for j in range(n_metrics):
                if i == j:
                    matrix[i, j] = 1.0
                    counts[i, j] = sum(1 for r in rows
                                       if r[i] is not None)
                    continue
                xs = [r[i] for r in rows
                      if r[i] is not None and r[j] is not None]
                ys = [r[j] for r in rows
                      if r[i] is not None and r[j] is not None]
                if len(xs) < 3:
                    matrix[i, j] = np.nan
                    counts[i, j] = len(xs)
                    continue
                r = _pearson(xs, ys)
                matrix[i, j] = r if r is not None else np.nan
                counts[i, j] = len(xs)

        im = ax.imshow(matrix, cmap="RdBu_r", vmin=-1, vmax=1,
                       aspect="auto")
        ax.set_xticks(range(n_metrics))
        ax.set_yticks(range(n_metrics))
        ax.set_xticklabels(metrics_labels, rotation=45, ha="right",
                           fontsize=8.5)
        ax.set_yticklabels(metrics_labels, fontsize=8.5)

        for i in range(n_metrics):
            for j in range(n_metrics):
                v = matrix[i, j]
                c = counts[i, j]
                if not np.isnan(v):
                    text = f"{v:+.2f}\n(n={c})" if i != j else "1.00"
                    color = ("white" if abs(v) > 0.55 else "black")
                    ax.text(j, i, text, ha="center", va="center",
                            fontsize=7.5, color=color)
                else:
                    ax.text(j, i, "—", ha="center", va="center",
                            fontsize=8, color="grey")

        ax.set_title(f"{pattern.upper()}  ({len(rows)} scale-ups)",
                     fontsize=11, fontweight="bold")

    cbar_ax = fig.add_axes([0.92, 0.15, 0.015, 0.7])
    fig.colorbar(im, cax=cbar_ax, label="Pearson r")

    fig.suptitle(
        "Pearson Correlation Matrix per Workload Pattern — Scale-Up Decisions Only\n"
        "Each cell shows the correlation coefficient between two metrics, "
        "computed over the counted scale-ups of that pattern",
        fontsize=13, y=1.02,
    )
    plt.tight_layout(rect=[0, 0, 0.9, 1])
    outpath = PLOTS_DIR / "correlation_matrix.png"
    plt.savefig(outpath, dpi=140, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# 2. QUALITY FLAGS SUMMARY (from batch logs)
# =====================================================================

def parse_batch_logs():
    """Parse orchestrator batch logs for per-run quality signals.

    Recognises lines of the form:
      "Completed step-run-04: events=4 *** QUALITY FLAG: dropped=1986, failed=0.00% ***"
      "Completed step-run-04: events=4"
    """
    per_pattern = defaultdict(list)   # pattern -> [{run_num, dropped, failed}]

    if not LOGS_DIR.exists():
        print(f"WARN: log directory {LOGS_DIR} not found")
        return per_pattern

    log_files = sorted(LOGS_DIR.glob("*-batch-*.log"))
    completed_re = re.compile(
        r'Completed (\w+)-run-(\d+): events=(\d+)(.*)$'
    )
    quality_re = re.compile(
        r'QUALITY FLAG: dropped=(\d+), failed=([\d.]+)%'
    )

    for lf in log_files:
        try:
            content = lf.read_text(errors="ignore")
        except OSError:
            continue
        for m in completed_re.finditer(content):
            pattern = m.group(1)
            run_num = int(m.group(2))
            events = int(m.group(3))
            trail = m.group(4) or ""
            q = quality_re.search(trail)
            dropped = int(q.group(1)) if q else 0
            failed_pct = float(q.group(2)) if q else 0.0
            per_pattern[pattern].append({
                "run_num": run_num,
                "dropped_iterations": dropped,
                "failed_checks_pct": failed_pct,
                "events_captured": events,
                "source_log": lf.name,
            })

    return per_pattern


def build_quality_summary():
    per_pat = parse_batch_logs()
    outpath = RESULTS / "quality_flags_summary.csv"
    with open(outpath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Quality Flags Summary — Rerun (July 2026)"])
        w.writerow(["Parsed from 06-load-tests/logs/*-batch-*.log"])
        w.writerow([])

        # Per-pattern aggregate
        w.writerow(["### Aggregate per pattern"])
        w.writerow([
            "pattern", "runs_completed",
            "total_dropped_iterations", "mean_dropped_per_run",
            "max_dropped_single_run",
            "runs_with_dropped>0", "runs_with_failed>0",
            "total_events_captured",
        ])
        for pattern in PATTERNS:
            runs = per_pat.get(pattern, [])
            if not runs:
                w.writerow([pattern, 0, 0, 0, 0, 0, 0, 0])
                continue
            total_dropped = sum(r["dropped_iterations"] for r in runs)
            mean_dropped = total_dropped / len(runs)
            max_dropped = max(r["dropped_iterations"] for r in runs)
            n_dropped_runs = sum(1 for r in runs
                                 if r["dropped_iterations"] > 0)
            n_failed_runs = sum(1 for r in runs
                                if r["failed_checks_pct"] > 0)
            total_events = sum(r["events_captured"] for r in runs)
            w.writerow([
                pattern, len(runs),
                total_dropped, round(mean_dropped, 1),
                max_dropped,
                n_dropped_runs, n_failed_runs,
                total_events,
            ])
        w.writerow([])

        # Per-run detail (dropped > 0 only)
        w.writerow(["### Per-run detail — runs that triggered quality flags"])
        w.writerow([
            "pattern", "run_num", "dropped_iterations",
            "failed_checks_pct", "events_captured", "source_log",
        ])
        for pattern in PATTERNS:
            runs = per_pat.get(pattern, [])
            flagged = sorted(
                (r for r in runs
                 if r["dropped_iterations"] > 0 or r["failed_checks_pct"] > 0),
                key=lambda r: r["run_num"],
            )
            for r in flagged:
                w.writerow([
                    pattern, r["run_num"], r["dropped_iterations"],
                    r["failed_checks_pct"], r["events_captured"],
                    r["source_log"],
                ])
    print(f"Saved {outpath.name}")


# =====================================================================
# MAIN
# =====================================================================

def main():
    print("Loading data...")
    decisions = load_decisions()
    run_starts = load_run_starts()
    print(f"  {len(decisions)} decisions, {len(run_starts)} runs")

    print("\n1/2 correlation_matrix.png")
    plot_correlation_matrix(decisions, run_starts)

    print("\n2/2 quality_flags_summary.csv")
    build_quality_summary()


if __name__ == "__main__":
    main()
