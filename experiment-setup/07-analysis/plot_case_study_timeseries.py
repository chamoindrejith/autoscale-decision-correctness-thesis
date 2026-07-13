#!/usr/bin/env python3
"""
plot_case_study_timeseries.py — Per-pattern exemplar-run time series.

For one counted run per workload pattern, plots:
  - Rolling 30-second p95 latency on the left y-axis (blue)
  - Replica count on the right y-axis (grey step function)
  - Horizontal reference line at the 500 ms SLO threshold
  - Vertical reference line at T_SLO_risk (if defined) — red
  - Vertical reference lines at every T_decision (scale-up) — green dashed

This is the "what SRD actually measures" figure: readers can see with
their own eyes the gap between when p95 latency crossed threshold and
when HPA reacted.

Reads:
  - results/decisions_with_ses.csv
  - results/run_index.csv
  - results/{pattern}-run-*.json   (raw k6 latency streams)

Writes:
  - results/plots/case_study_timeseries.png

Configuration
-------------
By default the exemplar run per pattern is the first counted run
(run_num == 4). Override any pattern via env vars, e.g.:

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
import matplotlib.dates as mdates

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
DEFAULT_RUN_NUM = 4    # first counted run (warm-up = 1..3)


def parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.+?\.)(\d+)(.+)$", s)
    if m:
        micros = m.group(2)[:6].ljust(6, "0")
        s = m.group(1) + micros + m.group(3)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_k6(path: Path) -> list[tuple[datetime, float]]:
    points: list[tuple[datetime, float]] = []
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


def rolling_p95(points, timestamps, step_seconds: int = 5):
    """Yield (sec_since_start, p95_ms) at every `step_seconds` seconds
    across the run."""
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


def load_run_index() -> dict[str, dict]:
    idx: dict[str, dict] = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            idx[r["run_label"]] = r
    return idx


def load_decisions_for_run(run_label: str) -> list[dict]:
    out: list[dict] = []
    with open(DECISIONS_CSV) as f:
        for r in csv.DictReader(f):
            if r.get("run_label") == run_label:
                out.append(r)
    return out


def pick_exemplar(pattern: str, idx: dict[str, dict]) -> str | None:
    """Env-var override; otherwise the run whose run_num == DEFAULT_RUN_NUM."""
    override = os.environ.get(f"CASE_{pattern.upper()}")
    if override:
        return override
    for label, row in idx.items():
        if row["pattern"] == pattern and int(row["run_num"]) == DEFAULT_RUN_NUM:
            return label
    return None


def render_panel(ax, pattern: str, run_label: str, idx_row: dict) -> None:
    k6_file = RESULTS / idx_row["file_path"]
    if not k6_file.exists():
        ax.text(0.5, 0.5, f"k6 file missing:\n{idx_row['file_path']}",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=9, color="grey")
        ax.set_title(f"{pattern.capitalize()} — {run_label}  (no data)",
                     fontsize=10)
        return

    print(f"  {pattern}: {run_label} ({idx_row['file_path']})")
    pts = load_k6(k6_file)
    if not pts:
        ax.text(0.5, 0.5, "empty k6 file", ha="center", va="center",
                transform=ax.transAxes, color="grey")
        return
    ts = [p[0] for p in pts]

    xs, ys = [], []
    for sec, pv in rolling_p95(pts, ts, step_seconds=5):
        xs.append(sec)
        ys.append(pv)
    ax.plot(xs, ys, color="#1f77b4", linewidth=1.6,
            label=f"Rolling 30 s p95 (ms)")
    ax.axhline(SLO_THRESHOLD_MS, color="red", linestyle="--",
               alpha=0.7, linewidth=1,
               label=f"{SLO_THRESHOLD_MS} ms SLO")
    ax.set_ylabel("p95 latency (ms)", color="#1f77b4", fontsize=9)
    ax.tick_params(axis="y", labelcolor="#1f77b4")

    # replicas on right axis
    ax2 = ax.twinx()
    decs = load_decisions_for_run(run_label)
    run_start = pts[0][0]
    rep_xs: list[float] = []
    rep_ys: list[int] = []
    if decs:
        first = decs[0]
        rep_xs.append(0.0)
        rep_ys.append(int(first["replicas_before"]))
        for d in decs:
            t = parse_iso(d["timestamp_utc"])
            sec = (t - run_start).total_seconds()
            try:
                after = int(d["replicas_after"])
            except (KeyError, ValueError):
                continue
            rep_xs.append(sec)
            rep_ys.append(rep_ys[-1])   # step: previous value at t-
            rep_xs.append(sec)
            rep_ys.append(after)        # then jump to new value
    if rep_xs:
        ax2.plot(rep_xs, rep_ys, color="grey", linewidth=1.5, alpha=0.85,
                 label="Replica count")
        ax2.set_ylim(0, max(rep_ys) + 2)
    ax2.set_ylabel("Replica count", color="grey", fontsize=9)
    ax2.tick_params(axis="y", labelcolor="grey")

    # T_SLO_risk marker (single per run, take the first defined)
    for d in decs:
        risk = (d.get("t_slo_risk_utc") or "").strip()
        if risk:
            try:
                risk_sec = (parse_iso(risk) - run_start).total_seconds()
                ax.axvline(risk_sec, color="red", linestyle="-",
                           alpha=0.6, linewidth=1.2,
                           label="T_SLO_risk")
                break
            except ValueError:
                pass

    # T_decision markers (scale-ups only, dashed green)
    seen_label = False
    for d in decs:
        if (d.get("direction") or "").lower() != "up":
            continue
        try:
            t = parse_iso(d["timestamp_utc"])
            sec = (t - run_start).total_seconds()
            ax.axvline(sec, color="green", linestyle="--",
                       alpha=0.55, linewidth=1,
                       label=None if seen_label else "T_decision (scale-up)")
            seen_label = True
        except (ValueError, KeyError):
            continue

    n_decs = sum(1 for d in decs if (d.get("direction") or "").lower() == "up")
    ax.set_title(f"{pattern.capitalize()} — {run_label}  "
                 f"(scale-ups: {n_decs})", fontsize=10)
    ax.set_xlabel("seconds since run start")
    ax.grid(True, alpha=0.3)
    # combined legend
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=7, framealpha=0.9)


def main() -> None:
    idx = load_run_index()

    fig, axes = plt.subplots(2, 2, figsize=(15, 9))
    axes = axes.flatten()
    for ax, pattern in zip(axes, PATTERNS):
        label = pick_exemplar(pattern, idx)
        if not label or label not in idx:
            ax.text(0.5, 0.5, f"no exemplar run for {pattern}",
                    ha="center", va="center", transform=ax.transAxes,
                    color="grey")
            ax.set_title(f"{pattern.capitalize()} — (no data)", fontsize=10)
            continue
        render_panel(ax, pattern, label, idx[label])

    fig.suptitle("Exemplar-run time series — p95 latency, replica count, and "
                 "SRD reference lines\n"
                 "Blue = rolling 30 s p95; grey step = replicas; "
                 "red solid = T_SLO_risk; green dashed = T_decision(scale-up); "
                 f"red dashed = {SLO_THRESHOLD_MS} ms SLO",
                 fontsize=12)
    plt.tight_layout()
    outpath = PLOTS_DIR / "case_study_timeseries.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


if __name__ == "__main__":
    main()
