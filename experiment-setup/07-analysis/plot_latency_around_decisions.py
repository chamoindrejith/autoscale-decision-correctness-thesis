#!/usr/bin/env python3
"""
plot_latency_around_decisions.py — Latency envelope around HPA scale-up
decisions, aggregated per workload pattern.

For every scale-up decision in the counted runs, computes the rolling
30-second p95 latency in a window [T_decision - 60 s, T_decision + 180 s],
anchored at t = 0 (the moment HPA fired the scaling decision). Then per
workload pattern:

  - Plots each individual decision's latency trace as a thin, semi-
    transparent line
  - Overlays the median latency across all decisions of that pattern
    (thick coloured line)
  - Shades the 25th-75th percentile band across decisions

This shows the "typical" latency behaviour around HPA firing:
  * Steady latency before, drop after  → HPA fired late, resolved quickly
  * Rising latency before, drop after  → HPA reacted appropriately
  * Rising latency before AND after    → HPA didn't help (Ineffective or
                                          Unnecessary at the observed load)

Reads:
  - results/decisions_with_ses.csv
  - results/run_index.csv
  - results/{pattern}-run-*.json

Writes:
  - results/plots/latency_around_hpa_decisions.png
"""
from __future__ import annotations

import bisect
import csv
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
DECISIONS_CSV = RESULTS / "decisions_with_ses.csv"
RUN_INDEX_CSV = RESULTS / "run_index.csv"
PLOTS_DIR = RESULTS / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

SLO_THRESHOLD_MS = 500
SLO_WINDOW_SECONDS = 30
MIN_SAMPLES_FOR_P95 = 20
WARMUP_LAST_RUN_NUM = 3

# Time window around each T_decision (relative seconds)
BEFORE_SECONDS = 60
AFTER_SECONDS = 180
STEP_SECONDS = 3        # sample rate on the anchored timeline

PATTERNS = ["step", "burst", "ramp", "noisy"]
PATTERN_COLORS = {
    "step":  "#1f77b4",
    "burst": "#ff7f0e",
    "ramp":  "#2ca02c",
    "noisy": "#d62728",
}


def parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.+?\.)(\d+)(.+)$", s)
    if m:
        micros = m.group(2)[:6].ljust(6, "0")
        s = m.group(1) + micros + m.group(3)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_k6(path: Path):
    points = []
    patt = re.compile(
        r'"time":"([^"]+)".*?"value":([0-9.eE+\-]+).*?"expected_response":"true"'
    )
    with open(path) as f:
        for line in f:
            if "http_req_duration" not in line:
                continue
            m = patt.search(line)
            if not m:
                continue
            try:
                points.append((parse_iso(m.group(1)), float(m.group(2))))
            except (ValueError, TypeError):
                continue
    points.sort(key=lambda p: p[0])
    return points


def p95(values):
    if not values:
        return None
    s = sorted(values)
    return s[int(0.95 * (len(s) - 1))]


def rolling_p95_at_times(points, timestamps, sample_times):
    """For each sample_time (datetime), return p95 of latency in
    [sample_time - SLO_WINDOW_SECONDS, sample_time]. Returns list of
    (sample_time, p95_or_None)."""
    out = []
    for st in sample_times:
        window_start = st - timedelta(seconds=SLO_WINDOW_SECONDS)
        lo = bisect.bisect_left(timestamps, window_start)
        hi = bisect.bisect_right(timestamps, st)
        n = hi - lo
        if n < MIN_SAMPLES_FOR_P95:
            out.append((st, None))
            continue
        vals = [points[j][1] for j in range(lo, hi)]
        out.append((st, p95(vals)))
    return out


def load_decisions():
    """Return counted-run scale-up decisions with parsed timestamps."""
    decs = []
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
            if (r.get("direction") or "").lower() != "up":
                continue
            try:
                r["_ts"] = parse_iso(r["timestamp_utc"])
            except (KeyError, ValueError):
                continue
            decs.append(r)
    return decs


def load_run_index():
    idx = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            idx[r["run_label"]] = r["file_path"]
    return idx


def main():
    print("Loading decisions + run index...")
    decisions = load_decisions()
    run_index = load_run_index()
    print(f"  {len(decisions)} counted scale-up decisions across "
          f"{len(set(d.get('run_label') for d in decisions))} runs")

    # Group decisions by run so each k6 file is loaded once
    decs_by_run = defaultdict(list)
    for d in decisions:
        rl = d.get("run_label") or ""
        if rl and rl != "between_runs":
            decs_by_run[rl].append(d)

    # Anchored time grid: -60s to +180s in 3s steps → 81 samples
    anchored_grid = list(range(-BEFORE_SECONDS, AFTER_SECONDS + 1, STEP_SECONDS))
    n_samples = len(anchored_grid)
    print(f"  Anchored grid: {n_samples} samples "
          f"({-BEFORE_SECONDS}s to +{AFTER_SECONDS}s, step {STEP_SECONDS}s)")

    # Per pattern, accumulate a matrix of shape (n_decisions, n_samples)
    per_pattern_traces: dict[str, list[list[float | None]]] = {p: []
                                                                for p in PATTERNS}

    tstart = time.time()
    processed = 0
    for i, (run_label, decs) in enumerate(sorted(decs_by_run.items()), 1):
        file_path = run_index.get(run_label)
        if not file_path:
            continue
        k6_file = RESULTS / file_path
        if not k6_file.exists():
            print(f"  [{i}/{len(decs_by_run)}] skip {run_label}: file missing")
            continue
        pts = load_k6(k6_file)
        if not pts:
            continue
        ts = [p[0] for p in pts]
        for d in decs:
            t_dec = d["_ts"]
            sample_times = [t_dec + timedelta(seconds=s) for s in anchored_grid]
            series = rolling_p95_at_times(pts, ts, sample_times)
            per_pattern_traces[d["pattern"]].append([v for _, v in series])
            processed += 1
        print(f"  [{i}/{len(decs_by_run)}] {run_label}: "
              f"{len(decs)} decisions ({time.time() - tstart:.0f}s elapsed)",
              flush=True)
    print(f"Processed {processed} decisions in {time.time() - tstart:.0f}s")

    # -----------------------------------------------------------------
    # Render 2×2 grid, one panel per pattern
    # -----------------------------------------------------------------
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), sharey=True)
    axes = axes.flatten()

    global_max_y = 0
    for pattern, ax in zip(PATTERNS, axes):
        traces = per_pattern_traces.get(pattern, [])
        colour = PATTERN_COLORS[pattern]

        if not traces:
            ax.text(0.5, 0.5, f"no scale-up decisions\nfor {pattern}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=11, color="grey")
            ax.set_title(f"{pattern.upper()}", fontsize=13,
                         fontweight="bold", loc="left", color=colour)
            continue

        # Convert traces to numpy array; use NaN for missing values
        arr = np.array([[np.nan if v is None else float(v)
                         for v in trace]
                        for trace in traces], dtype=float)
        # For each column, compute median and p25/p75 ignoring NaN
        median = np.nanmedian(arr, axis=0)
        p25 = np.nanpercentile(arr, 25, axis=0)
        p75 = np.nanpercentile(arr, 75, axis=0)

        xs = np.array(anchored_grid)

        # Individual traces — thin, semi-transparent
        for trace in traces:
            trace_arr = np.array([np.nan if v is None else float(v)
                                  for v in trace], dtype=float)
            ax.plot(xs, trace_arr, color=colour, alpha=0.15, linewidth=0.8,
                    zorder=1)

        # p25-p75 band
        ax.fill_between(xs, p25, p75, color=colour, alpha=0.28, zorder=2,
                        label="p25-p75 band across decisions")

        # Median line — thick
        ax.plot(xs, median, color=colour, linewidth=2.5,
                label="Median across decisions", zorder=4)

        # Reference lines
        ax.axvline(0, color="black", linestyle="-", linewidth=1.2, alpha=0.7,
                   label="T_decision (t = 0)", zorder=3)
        ax.axhline(SLO_THRESHOLD_MS, color="red", linestyle="--",
                   linewidth=1.2, alpha=0.75,
                   label=f"{SLO_THRESHOLD_MS} ms SLO", zorder=3)

        # Compute how many decisions ever crossed SLO in the after-window
        after_mask = xs > 0
        after_arr = arr[:, after_mask]
        # A decision is "SLO-breach after" if any post-decision sample > SLO
        breach_after = int(np.sum(np.any(after_arr > SLO_THRESHOLD_MS,
                                          axis=1)))

        ax.set_title(
            f"{pattern.upper()}  "
            f"({len(traces)} scale-ups; "
            f"{breach_after} exceeded {SLO_THRESHOLD_MS} ms after T_decision)",
            fontsize=12, fontweight="bold", loc="left", color=colour,
        )
        ax.set_xlabel("Seconds relative to T_decision  "
                      "(negative = before, positive = after)",
                      fontsize=10)
        ax.set_ylabel("p95 latency (ms)", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=8, framealpha=0.95)
        ax.set_xlim(-BEFORE_SECONDS, AFTER_SECONDS)

        finite = arr[np.isfinite(arr)]
        if finite.size:
            global_max_y = max(global_max_y,
                               float(np.percentile(finite, 99)))

    # Common y-axis: cap at 99th percentile to avoid outliers dominating
    y_top = max(global_max_y * 1.1, SLO_THRESHOLD_MS * 1.3)
    for ax in axes:
        ax.set_ylim(bottom=0, top=y_top)

    fig.suptitle(
        "Latency around HPA scale-up decisions, by workload pattern\n"
        "Each panel: thin lines = individual decision latency traces "
        "(anchored at T_decision, ±window). "
        "Thick line = median across decisions. Shaded band = 25th-75th "
        f"percentile. Red dashed = {SLO_THRESHOLD_MS} ms SLO threshold.",
        fontsize=13, y=0.998,
    )
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    outpath = PLOTS_DIR / "latency_around_hpa_decisions.png"
    plt.savefig(outpath, dpi=140, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


if __name__ == "__main__":
    main()
