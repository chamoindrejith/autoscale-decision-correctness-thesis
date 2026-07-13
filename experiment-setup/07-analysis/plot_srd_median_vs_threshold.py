#!/usr/bin/env python3
"""
plot_srd_median_vs_threshold.py — Median SRD (in seconds) as a function
of the SLO threshold, one line per workload pattern.

This script recomputes SRD at each threshold in-memory using the same
logic as compute_srd.py / plot_srd_sensitivity.py, then plots the
median-across-decisions per (threshold, pattern). Only decisions whose
SRD is defined (srd_source in {'late','pre_emptive'}) contribute; scale-
downs and 'no_slo_breach' rows are excluded.

Reads:
  - results/decisions_with_ses.csv
  - results/run_index.csv
  - results/{pattern}-run-*.json   (raw k6 latency streams)

Writes:
  - results/plots/median_srd_vs_threshold.png

Story this figure tells
-----------------------
At tight thresholds (250 ms) the workload actually breaches the SLO, so
SRD becomes measurable and pattern signatures emerge (Burst reacts late,
Ramp reacts pre-emptively). As the threshold loosens toward the primary
500 ms, defined SRDs become scarce and any residual medians reflect only
one or two data points.
"""
from __future__ import annotations

import bisect
import csv
import re
import statistics
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

THRESHOLDS_MS = [250, 400, 500, 750, 1000]
SLO_WINDOW_SECONDS = 30
MIN_SAMPLES_FOR_P95 = 20
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


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[int(0.95 * (len(s) - 1))]


def find_t_slo_risk(points, timestamps, threshold_ms: float):
    for i, (t, _) in enumerate(points):
        window_start = t - timedelta(seconds=SLO_WINDOW_SECONDS)
        lo = bisect.bisect_left(timestamps, window_start)
        hi = i + 1
        if hi - lo < MIN_SAMPLES_FOR_P95:
            continue
        vals = [points[j][1] for j in range(lo, hi)]
        pv = p95(vals)
        if pv is not None and pv > threshold_ms:
            return t
    return None


def main() -> None:
    # Load decisions & run index
    decisions: list[dict] = []
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
            r["_ts"] = parse_iso(r["timestamp_utc"])
            decisions.append(r)
    run_index: dict[str, str] = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            run_index[r["run_label"]] = r["file_path"]

    # Group by run
    by_run: dict[str, list[dict]] = defaultdict(list)
    for d in decisions:
        rl = d.get("run_label") or ""
        if rl and rl != "between_runs":
            by_run[rl].append(d)

    print(f"Recomputing SRD medians at thresholds {THRESHOLDS_MS}...")
    tstart = time.time()

    # srds[threshold][pattern] = [srd, ...]
    srds: dict[int, dict[str, list[float]]] = {
        thr: defaultdict(list) for thr in THRESHOLDS_MS
    }
    for i, (run_label, run_decs) in enumerate(sorted(by_run.items()), 1):
        file_path = run_index.get(run_label)
        if not file_path:
            continue
        k6 = RESULTS / file_path
        if not k6.exists():
            continue
        pts = load_k6(k6)
        if not pts:
            continue
        ts = [p[0] for p in pts]
        risk_at: dict[int, datetime | None] = {
            thr: find_t_slo_risk(pts, ts, thr) for thr in THRESHOLDS_MS
        }
        for d in run_decs:
            if (d.get("direction") or "").lower() != "up":
                continue
            pat = d["pattern"]
            for thr in THRESHOLDS_MS:
                risk = risk_at[thr]
                if risk is None:
                    continue  # no_slo_breach — excluded from median
                srds[thr][pat].append(
                    (d["_ts"] - risk).total_seconds()
                )
        print(f"  [{i}/{len(by_run)}] {run_label}", flush=True)
    print(f"Done in {time.time() - tstart:.0f}s")

    # ------------------------------------------------------------------
    # Plot
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for pat in PATTERNS:
        xs, ys, ns = [], [], []
        for thr in THRESHOLDS_MS:
            vals = srds[thr].get(pat, [])
            if not vals:
                continue
            xs.append(thr)
            ys.append(statistics.median(vals))
            ns.append(len(vals))
        if not xs:
            continue
        ax.plot(xs, ys, marker="o", linewidth=2.2,
                color=PATTERN_COLORS[pat],
                label=f"{pat.capitalize()}  (n at each threshold: "
                      f"{', '.join(f'{t}={n}' for t, n in zip(xs, ns))})")

    ax.axhline(0, color="black", linestyle="--", alpha=0.6, linewidth=1,
               label="SLO-breach moment (SRD = 0)")
    ax.axvline(500, color="black", linestyle=":", alpha=0.5, linewidth=1)
    ax.set_xlabel("SLO threshold (ms)", fontsize=11)
    ax.set_ylabel("Median SRD (seconds) — negative = pre-emptive, positive = late",
                  fontsize=11)
    ax.set_title("Median Scale Reaction Delay vs SLO threshold, per workload pattern\n"
                 "Only decisions with a defined SRD are counted; sparse thresholds "
                 "reflect the workload not sustaining SLO breaches",
                 fontsize=12)
    ax.set_xticks(THRESHOLDS_MS)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9, framealpha=0.95)
    plt.tight_layout()
    outpath = PLOTS_DIR / "median_srd_vs_threshold.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


if __name__ == "__main__":
    main()
