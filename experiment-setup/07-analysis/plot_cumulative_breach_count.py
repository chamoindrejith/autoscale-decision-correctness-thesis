#!/usr/bin/env python3
"""
plot_cumulative_breach_count.py — Cumulative breach-second count vs
seconds-since-run-start, one line per workload pattern.

For every counted run of each pattern, the rolling 30-second p95 is
computed at every second of the run. Each second where p95 exceeds the
500 ms SLO is counted as one "breach-second". These are then summed
cumulatively across all counted runs of the same pattern and plotted as
a function of seconds-since-run-start.

Reads:
  - results/decisions_with_ses.csv   (only to enumerate counted run_labels)
  - results/run_index.csv
  - results/{pattern}-run-*.json     (raw k6 latency streams)

Writes:
  - results/plots/cumulative_breach_by_pattern.png

Story this figure tells
-----------------------
This shows *when* in a run each pattern tends to breach the SLO. Bursts
usually spike early; Ramps breach monotonically as load rises; Steps
breach at a plateau; Noisy essentially never sustains a breach. Comple-
ments the bucket line-chart by showing the temporal shape rather than
the aggregate outcome.
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

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
DECISIONS_CSV = RESULTS / "decisions_with_ses.csv"
RUN_INDEX_CSV = RESULTS / "run_index.csv"
PLOTS_DIR = RESULTS / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

SLO_THRESHOLD_MS = 500
SLO_WINDOW_SECONDS = 30
MIN_SAMPLES_FOR_P95 = 20
STEP_SECONDS = 1
WARMUP_LAST_RUN_NUM = 3

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


def breach_seconds(points, timestamps) -> list[int]:
    """Return list of seconds-since-start where rolling p95 > SLO."""
    out: list[int] = []
    if not points:
        return out
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
            if vals[int(0.95 * (n - 1))] > SLO_THRESHOLD_MS:
                out.append(int((cur - t0).total_seconds()))
        cur += timedelta(seconds=STEP_SECONDS)
    return out


def main() -> None:
    # Enumerate counted run_labels from run_index.csv, discarding warm-ups
    counted_runs: dict[str, list[dict]] = defaultdict(list)
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            try:
                rn = int(r["run_num"])
            except ValueError:
                continue
            if rn <= WARMUP_LAST_RUN_NUM:
                continue
            pat = r["pattern"]
            if pat in PATTERNS:
                counted_runs[pat].append(r)

    total_runs = sum(len(v) for v in counted_runs.values())
    print(f"Processing {total_runs} counted runs across "
          f"{len(counted_runs)} patterns...")

    tstart = time.time()
    # cumulative_breach_seconds[pattern] = dict{second_since_start: count}
    per_second: dict[str, dict[int, int]] = {p: defaultdict(int)
                                             for p in PATTERNS}
    max_seconds: dict[str, int] = {p: 0 for p in PATTERNS}

    processed = 0
    for pattern in PATTERNS:
        for row in counted_runs[pattern]:
            fp = RESULTS / row["file_path"]
            if not fp.exists():
                continue
            processed += 1
            pts = load_k6(fp)
            if not pts:
                continue
            ts = [p[0] for p in pts]
            secs = breach_seconds(pts, ts)
            for s in secs:
                per_second[pattern][s] += 1
            run_len = int((pts[-1][0] - pts[0][0]).total_seconds())
            max_seconds[pattern] = max(max_seconds[pattern], run_len)
            print(f"  [{processed}/{total_runs}] "
                  f"{row['run_label']}: {len(secs)} breach seconds",
                  flush=True)

    print(f"Done in {time.time() - tstart:.0f}s")

    # ------------------------------------------------------------------
    # Plot: cumulative breach seconds vs run-relative time, per pattern
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for pattern in PATTERNS:
        d = per_second[pattern]
        end = max_seconds[pattern]
        if end == 0:
            continue
        xs = list(range(0, end + 1))
        ys = []
        cum = 0
        for x in xs:
            cum += d.get(x, 0)
            ys.append(cum)
        n_runs = len(counted_runs[pattern])
        ax.plot(xs, ys, linewidth=2.2, color=PATTERN_COLORS[pattern],
                label=f"{pattern.capitalize()}  (across {n_runs} counted runs; "
                      f"total breach-seconds = {ys[-1]})")

    ax.set_xlabel("Seconds since run start", fontsize=11)
    ax.set_ylabel(f"Cumulative breach-seconds  "
                  f"(rolling 30 s p95 > {SLO_THRESHOLD_MS} ms)",
                  fontsize=11)
    ax.set_title("Cumulative SLO-breach count vs seconds-since-run-start, "
                 "per workload pattern\n"
                 "Steeper slope = SLO breaching sustains at that time of the "
                 "run; flat = HPA and workload cooperate to stay under SLO",
                 fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9, framealpha=0.95)
    plt.tight_layout()
    outpath = PLOTS_DIR / "cumulative_breach_by_pattern.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


if __name__ == "__main__":
    main()
