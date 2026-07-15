#!/usr/bin/env python3
"""
plot_case_study_timeseries.py — Per-pattern exemplar-run time series.

For one counted run per workload pattern, plots the run's full timeline
showing:
  - Rolling 30-second p95 latency (top row of each pattern's block)
  - Replica count over time (bottom row of each pattern's block)
  - Horizontal SLO threshold at 500 ms (dashed red)
  - Shaded regions when p95 > SLO threshold (light red band)
  - Vertical markers for T_SLO_risk (solid red) and T_decision (green
    dashed for scale-up, blue dashed for scale-down)

Layout: 4 patterns × 2 rows each = 8 panels stacked vertically. Larger
panels than the previous 2×2 grid so time series details are readable.

Reads:
  - results/decisions_with_ses.csv
  - results/run_index.csv
  - results/{pattern}-run-*.json   (raw k6 latency streams)

Writes:
  - results/plots/case_study_timeseries.png

Configuration:
  Default exemplar per pattern = first counted run (run_num == 4).
  Override via env vars, e.g.:
    CASE_STEP=step-07 CASE_BURST=burst-05 python3 plot_case_study_timeseries.py
"""
from __future__ import annotations

import bisect
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
DECISIONS_CSV = RESULTS / "decisions_with_ses.csv"
RUN_INDEX_CSV = RESULTS / "run_index.csv"
PLOTS_DIR = RESULTS / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

SLO_THRESHOLD_MS = 500
SLO_WINDOW_SECONDS = 30
MIN_SAMPLES_FOR_P95 = 20

PATTERNS = ["step", "burst", "ramp", "noisy"]
PATTERN_COLORS = {
    "step":  "#1f77b4",
    "burst": "#ff7f0e",
    "ramp":  "#2ca02c",
    "noisy": "#d62728",
}
DEFAULT_RUN_NUM = 4    # first counted run (warm-up = 1..3)


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


def rolling_p95(points, timestamps, step_seconds: int = 3):
    """Yield (sec_since_start, p95_ms) at every step_seconds seconds."""
    if not points:
        return
    t0 = points[0][0]
    t_end = points[-1][0]
    cur = t0
    while cur <= t_end:
        window_start = cur - timedelta(seconds=SLO_WINDOW_SECONDS)
        lo = bisect.bisect_left(timestamps, window_start)
        hi = bisect.bisect_right(timestamps, cur)
        n = hi - lo
        if n >= MIN_SAMPLES_FOR_P95:
            vals = sorted(points[j][1] for j in range(lo, hi))
            yield (cur - t0).total_seconds(), vals[int(0.95 * (n - 1))]
        cur += timedelta(seconds=step_seconds)


def load_run_index():
    idx = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            idx[r["run_label"]] = r
    return idx


def load_decisions_for_run(run_label: str):
    out = []
    with open(DECISIONS_CSV) as f:
        for r in csv.DictReader(f):
            if r.get("run_label") == run_label:
                out.append(r)
    return out


def pick_exemplar(pattern: str, idx: dict) -> str | None:
    override = os.environ.get(f"CASE_{pattern.upper()}")
    if override:
        return override
    for label, row in idx.items():
        try:
            rn = int(row["run_num"])
        except (KeyError, ValueError):
            continue
        if row["pattern"] == pattern and rn == DEFAULT_RUN_NUM:
            return label
    return None


def find_slo_breach_ranges(latency_series, threshold=SLO_THRESHOLD_MS):
    """Return list of (start_sec, end_sec) tuples where p95 > threshold."""
    ranges = []
    in_breach = False
    breach_start = None
    for sec, p95 in latency_series:
        if p95 > threshold:
            if not in_breach:
                in_breach = True
                breach_start = sec
        else:
            if in_breach:
                ranges.append((breach_start, sec))
                in_breach = False
                breach_start = None
    if in_breach and latency_series:
        ranges.append((breach_start, latency_series[-1][0]))
    return ranges


def render_pattern_block(ax_lat, ax_rep, pattern: str,
                         run_label: str, idx_row: dict):
    """Render one pattern's two-panel block (latency top, replicas bottom)."""
    k6_file = RESULTS / idx_row["file_path"]
    pattern_color = PATTERN_COLORS[pattern]

    if not k6_file.exists():
        ax_lat.text(0.5, 0.5, f"k6 file missing:\n{idx_row['file_path']}",
                    ha="center", va="center", transform=ax_lat.transAxes,
                    fontsize=10, color="grey")
        ax_lat.set_title(f"{pattern.upper()}  —  {run_label}  (no data)",
                         fontsize=12, fontweight="bold",
                         loc="left", color=pattern_color)
        return

    print(f"  {pattern}: {run_label} ({idx_row['file_path']})")
    pts = load_k6(k6_file)
    if not pts:
        ax_lat.text(0.5, 0.5, "empty k6 file", ha="center", va="center",
                    transform=ax_lat.transAxes, color="grey")
        return
    ts = [p[0] for p in pts]

    # Latency series
    latency_series = list(rolling_p95(pts, ts, step_seconds=3))
    xs = [s for s, _ in latency_series]
    ys = [p95 for _, p95 in latency_series]

    # Highlight SLO breach regions FIRST so latency line draws on top
    breach_ranges = find_slo_breach_ranges(latency_series)
    for br_start, br_end in breach_ranges:
        ax_lat.axvspan(br_start, br_end,
                       color="#ffcccc", alpha=0.6, zorder=0,
                       label="p95 > SLO" if br_start == breach_ranges[0][0] else None)

    # Latency line + light fill
    ax_lat.plot(xs, ys, color=pattern_color, linewidth=2.0,
                label="Rolling 30 s p95 latency", zorder=3)
    ax_lat.fill_between(xs, 0, ys, color=pattern_color, alpha=0.15, zorder=1)

    # SLO threshold horizontal line
    ax_lat.axhline(SLO_THRESHOLD_MS, color="red", linestyle="--",
                   alpha=0.8, linewidth=1.3,
                   label=f"{SLO_THRESHOLD_MS} ms SLO threshold", zorder=2)

    ax_lat.set_ylabel("p95 latency (ms)", fontsize=10)
    ax_lat.set_ylim(bottom=0)
    if ys:
        ax_lat.set_ylim(top=max(max(ys) * 1.15, SLO_THRESHOLD_MS * 1.2))

    # Decisions overlay (T_SLO_risk + T_decision markers)
    decs = load_decisions_for_run(run_label)
    run_start = pts[0][0]

    seen_slo_risk_label = False
    seen_scale_up_label = False
    seen_scale_down_label = False

    for d in decs:
        # T_SLO_risk (one per run, take first)
        if not seen_slo_risk_label:
            risk_ts = (d.get("t_slo_risk_utc") or "").strip()
            if risk_ts:
                try:
                    risk_sec = (parse_iso(risk_ts) - run_start).total_seconds()
                    ax_lat.axvline(risk_sec, color="red", linestyle="-",
                                   alpha=0.7, linewidth=1.5,
                                   label="T_SLO_risk (breach onset)",
                                   zorder=4)
                    seen_slo_risk_label = True
                except ValueError:
                    pass

        # T_decision (per scale event)
        direction = (d.get("direction") or "").lower()
        try:
            t = parse_iso(d["timestamp_utc"])
            sec = (t - run_start).total_seconds()
        except (KeyError, ValueError):
            continue

        if direction == "up":
            label = None
            if not seen_scale_up_label:
                label = "T_decision (scale-up)"
                seen_scale_up_label = True
            ax_lat.axvline(sec, color="green", linestyle="--",
                           alpha=0.65, linewidth=1.2,
                           label=label, zorder=4)
        elif direction == "down":
            label = None
            if not seen_scale_down_label:
                label = "T_decision (scale-down)"
                seen_scale_down_label = True
            ax_lat.axvline(sec, color="blue", linestyle="--",
                           alpha=0.5, linewidth=1.0,
                           label=label, zorder=4)

    # Title & legend on latency panel
    n_ups = sum(1 for d in decs if (d.get("direction") or "").lower() == "up")
    n_downs = sum(1 for d in decs if (d.get("direction") or "").lower() == "down")
    ax_lat.set_title(f"{pattern.upper()}  —  {run_label}  "
                     f"(scale-ups: {n_ups}, scale-downs: {n_downs})",
                     fontsize=12, fontweight="bold",
                     loc="left", color=pattern_color)
    ax_lat.grid(True, alpha=0.3)
    ax_lat.legend(loc="upper left", fontsize=8, framealpha=0.95, ncol=2)

    # Replica count on bottom panel
    rep_xs, rep_ys = [], []
    if decs:
        try:
            initial_replicas = int(decs[0]["replicas_before"])
        except (KeyError, ValueError):
            initial_replicas = 2
        rep_xs.append(0.0)
        rep_ys.append(initial_replicas)
        for d in decs:
            try:
                t = parse_iso(d["timestamp_utc"])
                sec = (t - run_start).total_seconds()
                after = int(d["replicas_after"])
            except (KeyError, ValueError):
                continue
            rep_xs.append(sec)
            rep_ys.append(rep_ys[-1])   # hold previous value at t-
            rep_xs.append(sec)
            rep_ys.append(after)        # jump to new value at t
        rep_xs.append((pts[-1][0] - run_start).total_seconds())
        rep_ys.append(rep_ys[-1])

    if rep_xs:
        ax_rep.plot(rep_xs, rep_ys, color="grey", linewidth=2.0,
                    drawstyle="steps-post", alpha=0.85)
        ax_rep.fill_between(rep_xs, 0, rep_ys, color="grey", alpha=0.15,
                            step="post")
        ax_rep.set_ylim(0, max(max(rep_ys) + 2, 12))
    ax_rep.set_ylabel("Replicas", fontsize=10)
    ax_rep.set_xlabel("Seconds since run start", fontsize=10)
    ax_rep.grid(True, alpha=0.3)
    ax_rep.axhline(10, color="grey", linestyle=":", alpha=0.5, linewidth=1)
    ax_rep.text(0.99, 0.90, "maxReplicas = 10", transform=ax_rep.transAxes,
                ha="right", fontsize=7, color="grey", alpha=0.9)


def main():
    idx = load_run_index()

    # 4 patterns × 2 rows each (latency + replicas)
    # Latency row: height 4, replicas row: height 1.3
    fig = plt.figure(figsize=(15, 20))
    gs = GridSpec(
        nrows=8, ncols=1, figure=fig,
        height_ratios=[4, 1.3, 4, 1.3, 4, 1.3, 4, 1.3],
        hspace=0.15,
    )
    axes_pairs = []
    for i in range(4):
        ax_lat = fig.add_subplot(gs[i * 2, 0])
        ax_rep = fig.add_subplot(gs[i * 2 + 1, 0], sharex=ax_lat)
        axes_pairs.append((ax_lat, ax_rep))
        # Hide x-tick labels on latency panel (shares with replicas below)
        plt.setp(ax_lat.get_xticklabels(), visible=False)

    for (ax_lat, ax_rep), pattern in zip(axes_pairs, PATTERNS):
        label = pick_exemplar(pattern, idx)
        if not label or label not in idx:
            ax_lat.text(0.5, 0.5, f"no exemplar run for {pattern}",
                        ha="center", va="center",
                        transform=ax_lat.transAxes, color="grey")
            ax_lat.set_title(f"{pattern.upper()}  —  (no data)",
                             fontsize=12, fontweight="bold", loc="left")
            continue
        render_pattern_block(ax_lat, ax_rep, pattern, label, idx[label])

    fig.suptitle("Exemplar-Run Time Series per Workload Pattern\n"
                 "For one counted run per pattern: p95 latency (top of each "
                 "block) + replica count (bottom of each block). "
                 f"Red band = p95 > {SLO_THRESHOLD_MS} ms SLO; "
                 "vertical markers = T_SLO_risk, T_decision events.",
                 fontsize=13, y=0.995)
    plt.tight_layout(rect=[0, 0, 1, 0.985])
    outpath = PLOTS_DIR / "case_study_timeseries.png"
    plt.savefig(outpath, dpi=140, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


if __name__ == "__main__":
    main()
