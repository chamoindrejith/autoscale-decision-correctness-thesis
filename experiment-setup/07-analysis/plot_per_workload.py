#!/usr/bin/env python3
"""
plot_per_workload.py — Generate per-workload versions of the main plots.

For each of the four workload patterns (Step, Burst, Ramp, Noisy),
produces a separate PNG for each of the following plot types. Useful
for thesis chapter figures where each workload gets its own subsection
and a dedicated figure rather than a multi-pattern grid.

Plots generated (per pattern):
  1. bucket_distribution_{pattern}.png
       Bar chart of the 4 correctness buckets for this pattern only.
  2. latency_around_hpa_decisions_{pattern}.png
       Median + p25-p75 band of rolling 30-s p95 latency around
       T_decision, in [-60 s, +180 s], for this pattern's scale-ups.
  3. srd_histogram_{pattern}.png
       Distribution of SRD (seconds) for scale-ups with a defined SRD.
  4. ses_histogram_{pattern}.png
       Distribution of SES for scale-ups with a defined SES.
  5. srd_vs_ses_scatter_{pattern}.png
       Joint SRD x SES scatter (one dot per scale-up).

Reads:
  - results/decisions_with_ses.csv        (canonical dataset)
  - results/run_index.csv                 (run_label -> k6 file mapping)
  - results/{pattern}-run-*.json          (k6 raw latency streams for
                                           the latency-around-decisions plot)

Writes:
  - results/plots/{plot_name}_{pattern}.png  (5 plots x 4 patterns = 20 files)
"""
from __future__ import annotations

import bisect
import csv
import re
import statistics
import time
from collections import Counter, defaultdict
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

PATTERNS = ["step", "burst", "ramp", "noisy"]
PATTERN_COLORS = {
    "step":  "#1f77b4",
    "burst": "#ff7f0e",
    "ramp":  "#2ca02c",
    "noisy": "#d62728",
}
WARMUP_LAST_RUN_NUM = 3

SLO_THRESHOLD_MS = 500
SLO_WINDOW_SECONDS = 30
MIN_SAMPLES_FOR_P95 = 20

# Latency-around-decisions window
BEFORE_SECONDS = 60
AFTER_SECONDS = 180
STEP_SECONDS = 3

BUCKETS = ["Correct & Timely", "Correct but Late",
           "Unnecessary", "Ineffective", "Undefined"]
BUCKET_COLORS = {
    "Correct & Timely":  "#2ca02c",
    "Correct but Late":  "#ff7f0e",
    "Unnecessary":       "#7f7f7f",
    "Ineffective":       "#d62728",
    "Undefined":         "#cccccc",
}


# =====================================================================
# HELPERS
# =====================================================================

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


def load_decisions() -> list[dict]:
    with open(DECISIONS_CSV) as f:
        return list(csv.DictReader(f))


def load_run_index() -> dict[str, str]:
    idx = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            idx[r["run_label"]] = r["file_path"]
    return idx


def is_counted(r: dict) -> bool:
    try:
        rn = int(r.get("run_num") or 0)
    except ValueError:
        return False
    if rn <= WARMUP_LAST_RUN_NUM:
        return False
    return (r.get("pattern") or "").strip() in PATTERNS


def rolling_p95_at_times(points, timestamps, sample_times):
    """For each sample_time (datetime), return p95 in
    [sample_time - SLO_WINDOW_SECONDS, sample_time]."""
    out = []
    for st in sample_times:
        window_start = st - timedelta(seconds=SLO_WINDOW_SECONDS)
        lo = bisect.bisect_left(timestamps, window_start)
        hi = bisect.bisect_right(timestamps, st)
        n = hi - lo
        if n < MIN_SAMPLES_FOR_P95:
            out.append(None)
            continue
        vals = sorted(points[j][1] for j in range(lo, hi))
        out.append(vals[int(0.95 * (n - 1))])
    return out


# =====================================================================
# PLOT 1: bucket distribution per workload
# =====================================================================

def plot_bucket_distribution_per_workload(decisions):
    counted = [d for d in decisions if is_counted(d)]
    by_pat = defaultdict(list)
    for d in counted:
        by_pat[d["pattern"]].append(d)

    for pattern in PATTERNS:
        rows = by_pat.get(pattern, [])
        counts = Counter((r.get("bucket_v3") or "").strip() for r in rows)
        total = len(rows)

        fig, ax = plt.subplots(figsize=(9, 6))
        bucket_names = [b for b in BUCKETS if counts.get(b, 0) > 0]
        if not bucket_names:
            bucket_names = ["(no data)"]
            values = [0]
            pcts = ["0 %"]
        else:
            values = [counts[b] for b in bucket_names]
            pcts = [f"{100 * v / total:.1f} %" if total else "0 %"
                    for v in values]

        colors = [BUCKET_COLORS.get(b, "#888888") for b in bucket_names]
        bars = ax.bar(bucket_names, values, color=colors,
                      edgecolor="black", linewidth=0.5)
        for bar, val, pct in zip(bars, values, pcts):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height(),
                    f"{val}\n({pct})",
                    ha="center", va="bottom", fontsize=10,
                    fontweight="bold")

        ax.set_ylabel("Number of decisions", fontsize=11)
        ax.set_xlabel("Bucket (proposal-aligned SRD × SES)", fontsize=11)
        ax.set_title(f"{pattern.upper()} — HPA Decision Correctness Buckets\n"
                     f"(n = {total} counted decisions; SLO = 500 ms)",
                     fontsize=13, fontweight="bold", loc="left",
                     color=PATTERN_COLORS[pattern])
        ax.grid(axis="y", alpha=0.3)
        if values:
            ax.set_ylim(top=max(values) * 1.20)
        plt.xticks(rotation=15, ha="right")
        plt.tight_layout()

        outpath = PLOTS_DIR / f"bucket_distribution_{pattern}.png"
        plt.savefig(outpath, dpi=140, bbox_inches="tight")
        print(f"Saved {outpath.name}")
        plt.close()


# =====================================================================
# PLOT 2: latency around HPA decisions per workload
# =====================================================================

def plot_latency_around_decisions_per_workload(decisions, run_index):
    counted_ups = [d for d in decisions
                   if is_counted(d)
                   and (d.get("direction") or "").lower() == "up"]
    for d in counted_ups:
        try:
            d["_ts"] = parse_iso(d["timestamp_utc"])
        except (KeyError, ValueError):
            d["_ts"] = None
    counted_ups = [d for d in counted_ups if d["_ts"] is not None]

    by_run = defaultdict(list)
    for d in counted_ups:
        rl = d.get("run_label") or ""
        if rl and rl != "between_runs":
            by_run[rl].append(d)

    anchored_grid = list(range(-BEFORE_SECONDS,
                               AFTER_SECONDS + 1, STEP_SECONDS))

    # Per-pattern trace matrices
    per_pattern_traces = defaultdict(list)

    tstart = time.time()
    for i, (run_label, decs) in enumerate(sorted(by_run.items()), 1):
        file_path = run_index.get(run_label)
        if not file_path:
            continue
        k6_file = RESULTS / file_path
        if not k6_file.exists():
            continue
        pts = load_k6(k6_file)
        if not pts:
            continue
        ts = [p[0] for p in pts]
        for d in decs:
            t_dec = d["_ts"]
            sample_times = [t_dec + timedelta(seconds=s) for s in anchored_grid]
            trace = rolling_p95_at_times(pts, ts, sample_times)
            per_pattern_traces[d["pattern"]].append(trace)
        print(f"  latency-around: [{i}/{len(by_run)}] {run_label} "
              f"({time.time() - tstart:.0f}s elapsed)", flush=True)

    for pattern in PATTERNS:
        traces = per_pattern_traces.get(pattern, [])
        colour = PATTERN_COLORS[pattern]

        fig, ax = plt.subplots(figsize=(11, 6.5))
        if not traces:
            ax.text(0.5, 0.5, f"no scale-up decisions for {pattern}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=12, color="grey")
        else:
            arr = np.array([[np.nan if v is None else float(v)
                             for v in tr] for tr in traces], dtype=float)
            median = np.nanmedian(arr, axis=0)
            p25 = np.nanpercentile(arr, 25, axis=0)
            p75 = np.nanpercentile(arr, 75, axis=0)
            xs = np.array(anchored_grid)

            # Individual traces
            for tr in traces:
                tr_arr = np.array([np.nan if v is None else float(v)
                                   for v in tr], dtype=float)
                ax.plot(xs, tr_arr, color=colour, alpha=0.18,
                        linewidth=0.8, zorder=1)

            # p25-p75 band
            ax.fill_between(xs, p25, p75, color=colour, alpha=0.30,
                            zorder=2, label="p25-p75 across decisions")
            # Median
            ax.plot(xs, median, color=colour, linewidth=2.5,
                    label="Median across decisions", zorder=4)

            # SLO breach counts (after t=0)
            after_mask = xs > 0
            after_arr = arr[:, after_mask]
            breach_after = int(np.sum(
                np.any(after_arr > SLO_THRESHOLD_MS, axis=1)))

            ax.set_title(
                f"{pattern.upper()} — latency around HPA scale-up decisions\n"
                f"({len(traces)} scale-ups; "
                f"{breach_after} exceeded {SLO_THRESHOLD_MS} ms after "
                f"T_decision)",
                fontsize=13, fontweight="bold", loc="left", color=colour,
            )

        ax.axvline(0, color="black", linestyle="-", linewidth=1.2,
                   alpha=0.7, label="T_decision (t = 0)", zorder=3)
        ax.axhline(SLO_THRESHOLD_MS, color="red", linestyle="--",
                   linewidth=1.2, alpha=0.75,
                   label=f"{SLO_THRESHOLD_MS} ms SLO", zorder=3)
        ax.set_xlabel("Seconds relative to T_decision "
                      "(negative = before, positive = after)", fontsize=10)
        ax.set_ylabel("p95 latency (ms)", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=9)
        ax.set_xlim(-BEFORE_SECONDS, AFTER_SECONDS)
        plt.tight_layout()

        outpath = PLOTS_DIR / f"latency_around_hpa_decisions_{pattern}.png"
        plt.savefig(outpath, dpi=140, bbox_inches="tight")
        print(f"Saved {outpath.name}")
        plt.close()


# =====================================================================
# PLOT 3: SRD histogram per workload
# =====================================================================

def plot_srd_histogram_per_workload(decisions):
    counted = [d for d in decisions if is_counted(d)]
    by_pat_srd = defaultdict(list)
    for d in counted:
        if (d.get("direction") or "").lower() != "up":
            continue
        src = (d.get("srd_source") or "").strip()
        if src not in ("late", "pre_emptive"):
            continue
        srd = safe_float(d.get("srd_seconds"))
        if srd is None:
            continue
        by_pat_srd[d["pattern"]].append(srd)

    for pattern in PATTERNS:
        vals = by_pat_srd.get(pattern, [])
        colour = PATTERN_COLORS[pattern]

        fig, ax = plt.subplots(figsize=(10, 6))
        if not vals:
            ax.text(0.5, 0.5, f"no defined SRDs for {pattern}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=12, color="grey")
        else:
            span = max(abs(min(vals)), abs(max(vals))) * 1.05 or 1.0
            bin_edges = np.linspace(-span, span, 26)
            ax.hist(vals, bins=bin_edges, color=colour, edgecolor="black",
                    alpha=0.85, linewidth=0.4)
            ax.axvline(0, color="black", linestyle="--", alpha=0.7,
                       label="SLO breach moment (SRD = 0)")
            mn = statistics.mean(vals)
            md = statistics.median(vals)
            ax.axvline(mn, color="red", linestyle=":", alpha=0.8,
                       linewidth=1.5, label=f"mean = {mn:.1f} s")
            ax.axvline(md, color="darkblue", linestyle=":", alpha=0.8,
                       linewidth=1.5, label=f"median = {md:.1f} s")

        n = len(vals)
        title = f"{pattern.upper()} — SRD Distribution  (n = {n} defined SRDs)"
        ax.set_title(title, fontsize=13, fontweight="bold",
                     loc="left", color=colour)
        ax.set_xlabel("SRD (seconds; negative = pre-emptive, "
                      "positive = late)", fontsize=10)
        ax.set_ylabel("Number of scale-up decisions", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=9)
        plt.tight_layout()

        outpath = PLOTS_DIR / f"srd_histogram_{pattern}.png"
        plt.savefig(outpath, dpi=140, bbox_inches="tight")
        print(f"Saved {outpath.name}")
        plt.close()


# =====================================================================
# PLOT 4: SES histogram per workload
# =====================================================================

def plot_ses_histogram_per_workload(decisions):
    counted = [d for d in decisions if is_counted(d)]
    by_pat_ses = defaultdict(list)
    for d in counted:
        if (d.get("direction") or "").lower() != "up":
            continue
        v = safe_float(d.get("ses"))
        if v is None:
            continue
        by_pat_ses[d["pattern"]].append(v)

    for pattern in PATTERNS:
        vals = by_pat_ses.get(pattern, [])
        colour = PATTERN_COLORS[pattern]

        fig, ax = plt.subplots(figsize=(10, 6))
        if not vals:
            ax.text(0.5, 0.5, f"no defined SES for {pattern}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=12, color="grey")
        else:
            span = max(abs(min(vals)), abs(max(vals))) * 1.05 or 1.0
            bin_edges = np.linspace(-span, span, 26)
            ax.hist(vals, bins=bin_edges, color=colour, edgecolor="black",
                    alpha=0.85, linewidth=0.4)
            ax.axvline(0, color="black", linestyle="--", alpha=0.7,
                       label="No change (SES = 0)")
            mn = statistics.mean(vals)
            md = statistics.median(vals)
            ax.axvline(mn, color="red", linestyle=":", alpha=0.8,
                       linewidth=1.5, label=f"mean = {mn:.3f}")
            ax.axvline(md, color="darkblue", linestyle=":", alpha=0.8,
                       linewidth=1.5, label=f"median = {md:.3f}")

        n = len(vals)
        pos = sum(1 for v in vals if v > 0)
        neg = sum(1 for v in vals if v < 0)
        title = (f"{pattern.upper()} — SES Distribution  "
                 f"(n = {n}, positive = {pos}, negative = {neg})")
        ax.set_title(title, fontsize=13, fontweight="bold",
                     loc="left", color=colour)
        ax.set_xlabel("SES  (positive = latency improved after scaling)",
                      fontsize=10)
        ax.set_ylabel("Number of scale-up decisions", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=9)
        plt.tight_layout()

        outpath = PLOTS_DIR / f"ses_histogram_{pattern}.png"
        plt.savefig(outpath, dpi=140, bbox_inches="tight")
        print(f"Saved {outpath.name}")
        plt.close()


# =====================================================================
# PLOT 5: SRD x SES scatter per workload
# =====================================================================

def plot_srd_vs_ses_scatter_per_workload(decisions):
    counted = [d for d in decisions if is_counted(d)]
    by_pat = defaultdict(list)
    for d in counted:
        if (d.get("direction") or "").lower() != "up":
            continue
        if (d.get("srd_source") or "") not in ("late", "pre_emptive"):
            continue
        srd = safe_float(d.get("srd_seconds"))
        ses = safe_float(d.get("ses"))
        if srd is None or ses is None:
            continue
        by_pat[d["pattern"]].append((srd, ses))

    for pattern in PATTERNS:
        pts = by_pat.get(pattern, [])
        colour = PATTERN_COLORS[pattern]

        fig, ax = plt.subplots(figsize=(10, 7))
        if not pts:
            ax.text(0.5, 0.5, f"no SRD × SES pairs for {pattern}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=12, color="grey")
            subtitle = "no data"
        else:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            ax.scatter(xs, ys, s=70, alpha=0.7, color=colour,
                       edgecolor="black", linewidth=0.5, zorder=3)

            srd_span = max(abs(min(xs)), abs(max(xs))) * 1.15 or 1.0
            ses_span = max(abs(min(ys)), abs(max(ys))) * 1.15 or 1.0

            ax.axhline(0, color="black", linestyle="--", alpha=0.55,
                       linewidth=1, zorder=1)
            ax.axvline(0, color="black", linestyle="--", alpha=0.55,
                       linewidth=1, zorder=1)

            # Quadrant labels
            ax.text(-srd_span * 0.95, ses_span * 0.9,
                    "Pre-emptive\n& effective", fontsize=9,
                    color="darkgreen", alpha=0.7, ha="left", va="top")
            ax.text(srd_span * 0.95, ses_span * 0.9,
                    "Late\nbut recovered", fontsize=9,
                    color="darkgoldenrod", alpha=0.7, ha="right", va="top")
            ax.text(-srd_span * 0.95, -ses_span * 0.9,
                    "Pre-emptive\nbut worsened", fontsize=9,
                    color="grey", alpha=0.7, ha="left", va="bottom")
            ax.text(srd_span * 0.95, -ses_span * 0.9,
                    "Late\n& ineffective", fontsize=9,
                    color="darkred", alpha=0.7, ha="right", va="bottom")

            ax.set_xlim(-srd_span, srd_span)
            ax.set_ylim(-ses_span, ses_span)
            med_srd = statistics.median(xs)
            med_ses = statistics.median(ys)
            subtitle = (f"n = {len(pts)}, "
                        f"median SRD = {med_srd:.1f} s, "
                        f"median SES = {med_ses:.3f}")

        ax.set_title(f"{pattern.upper()} — SRD × SES per scale-up "
                     f"decision\n({subtitle})",
                     fontsize=13, fontweight="bold", loc="left", color=colour)
        ax.set_xlabel("SRD (s)  — negative = pre-emptive, positive = late",
                      fontsize=10)
        ax.set_ylabel("SES  — positive = latency improved", fontsize=10)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()

        outpath = PLOTS_DIR / f"srd_vs_ses_scatter_{pattern}.png"
        plt.savefig(outpath, dpi=140, bbox_inches="tight")
        print(f"Saved {outpath.name}")
        plt.close()


# =====================================================================
# MAIN
# =====================================================================

def main():
    print("Loading decisions + run index...")
    decisions = load_decisions()
    run_index = load_run_index()
    print(f"  {len(decisions)} rows loaded")

    print("\n1/5: Bucket distribution per workload")
    plot_bucket_distribution_per_workload(decisions)

    print("\n2/5: SRD histogram per workload")
    plot_srd_histogram_per_workload(decisions)

    print("\n3/5: SES histogram per workload")
    plot_ses_histogram_per_workload(decisions)

    print("\n4/5: SRD × SES scatter per workload")
    plot_srd_vs_ses_scatter_per_workload(decisions)

    print("\n5/5: Latency around HPA decisions per workload (slow — "
          "parses k6 files)")
    plot_latency_around_decisions_per_workload(decisions, run_index)


if __name__ == "__main__":
    main()
