#!/usr/bin/env python3
"""
plot_srd_sensitivity.py — Threshold-sensitivity analysis for the SLO / SRD
computation (methodology §4).

Recomputes SRD for every scale-up decision at several candidate SLO
thresholds ({250, 400, 500, 750, 1000} ms by default). Produces:

  1. results/plots/srd_sensitivity_by_threshold.png
        5 panels (one per threshold) overlaying SRD histograms per pattern.
        Lets you see at a glance whether the qualitative pattern signature
        (Burst → Late, Ramp → Timely, Step in between, Noisy → empty) is
        stable across threshold choices.

  2. results/plots/bucket_sensitivity_by_threshold.png
        Stacked bar chart per (threshold, pattern) showing the SRD-based
        bucket distribution. Visualises how many decisions shift from
        Unnecessary (no_slo_breach) to Correct but Late as the threshold
        tightens.

  3. results/srd_sensitivity_summary.csv
        Long-format table with columns
            threshold_ms, pattern, srd_source_count_late,
            srd_source_count_pre_emptive, srd_source_count_no_slo_breach,
            bucket_correct_timely, bucket_correct_but_late,
            bucket_unnecessary, bucket_ineffective
        Ready for inclusion as a supplementary thesis table.

Reads:
  - results/decisions_with_ses.csv
  - results/run_index.csv
  - results/{pattern}-run-*.json    (k6 raw latency streams)

The k6 files are loaded ONCE per run and shared across all thresholds,
so total wall time is comparable to a single compute_srd.py invocation.
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
import numpy as np

# =====================================================================
# CONFIG
# =====================================================================
ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
DECISIONS_CSV = RESULTS_DIR / "decisions_with_ses.csv"
RUN_INDEX_CSV = RESULTS_DIR / "run_index.csv"
PLOTS_DIR = RESULTS_DIR / "plots"
PLOTS_DIR.mkdir(exist_ok=True)
SUMMARY_CSV = RESULTS_DIR / "srd_sensitivity_summary.csv"

# Thresholds to test (in ms). Add or remove values as needed.
THRESHOLDS_MS = [250, 400, 500, 750, 1000]

# Same window as compute_srd.py
SLO_WINDOW_SECONDS = 30
MIN_SAMPLES_FOR_P95 = 20

# Warm-up run cutoff — methodology says discard first 3 runs of each pattern.
WARMUP_LAST_RUN_NUM = 3

PATTERNS = ["step", "burst", "ramp", "noisy"]
PATTERN_COLORS = {
    "step":  "#1f77b4",
    "burst": "#ff7f0e",
    "ramp":  "#2ca02c",
    "noisy": "#d62728",
}
BUCKETS = ["Correct & Timely", "Correct but Late", "Unnecessary", "Ineffective"]
BUCKET_COLORS = {
    "Correct & Timely":  "#2ca02c",
    "Correct but Late":  "#ff7f0e",
    "Unnecessary":       "#7f7f7f",
    "Ineffective":       "#d62728",
}


# =====================================================================
# TIMESTAMP + K6 HELPERS  (mirrors compute_srd.py)
# =====================================================================

def parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.+?\.)(\d+)(.+)$", s)
    if m:
        micros = m.group(2)[:6].ljust(6, "0")
        s = m.group(1) + micros + m.group(3)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_k6_latencies(path: Path) -> list[tuple[datetime, float]]:
    points: list[tuple[datetime, float]] = []
    pattern = re.compile(
        r'"time":"([^"]+)".*?"value":([0-9.eE+\-]+).*?"expected_response":"true"'
    )
    with open(path) as f:
        for line in f:
            if "http_req_duration" not in line:
                continue
            m = pattern.search(line)
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
    idx = int(0.95 * (len(s) - 1))
    return s[idx]


def find_t_slo_risk(points, timestamps, threshold_ms: float):
    """Return the earliest timestamp where the rolling 30 s p95 exceeds
    `threshold_ms`. Returns None if never breached."""
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


# =====================================================================
# BUCKET RULES (same as classify_decisions_v2.py)
# =====================================================================

def srd_bucket(direction: str, srd_source: str, srd_seconds: float | None,
               scaling_limited: bool, limit_reason: str) -> str:
    if scaling_limited:
        if direction == "up" and limit_reason == "TooManyReplicas":
            return "Ineffective"
        if direction == "down" and limit_reason == "TooFewReplicas":
            return "Ineffective"
    if direction == "down":
        return "Correct & Timely"
    if srd_source == "no_slo_breach":
        return "Unnecessary"
    if srd_source in ("late", "pre_emptive") and srd_seconds is not None:
        return "Correct but Late" if srd_seconds > 0 else "Correct & Timely"
    return "Unnecessary"


# =====================================================================
# MAIN
# =====================================================================

def main() -> None:
    # 1. Load decisions
    print("Loading decisions...")
    decisions: list[dict] = []
    with open(DECISIONS_CSV) as f:
        for r in csv.DictReader(f):
            pat = (r.get("pattern") or "").strip()
            if pat not in PATTERNS:
                continue
            try:
                run_num = int(r.get("run_num") or 0)
            except ValueError:
                continue
            if run_num <= WARMUP_LAST_RUN_NUM:
                continue
            r["_ts"] = parse_iso(r["timestamp_utc"])
            r["_run_num"] = run_num
            decisions.append(r)
    print(f"  {len(decisions)} counted decisions loaded")

    # 2. Load run index — maps run_label -> k6 file path
    print("Loading run index...")
    run_index: dict[str, str] = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            run_index[r["run_label"]] = r["file_path"]

    # 3. Group decisions by run_label so we load each k6 file exactly once.
    by_run: dict[str, list[dict]] = defaultdict(list)
    for d in decisions:
        rl = d.get("run_label") or ""
        if not rl or rl == "between_runs":
            continue
        by_run[rl].append(d)

    # 4. For every run, compute T_SLO_risk at every threshold and stash
    #    it, then compute per-decision SRD for each threshold.
    print(f"Recomputing SRD at thresholds {THRESHOLDS_MS} ms...")
    tstart = time.time()

    # results[threshold_ms][pattern] = list of (srd_source, srd_seconds,
    #                                            direction, scaling_limited,
    #                                            limit_reason)
    results: dict[int, dict[str, list[dict]]] = {
        thr: defaultdict(list) for thr in THRESHOLDS_MS
    }

    for i, (run_label, run_decisions) in enumerate(sorted(by_run.items()), 1):
        file_path = run_index.get(run_label)
        if not file_path:
            continue
        k6_file = RESULTS_DIR / file_path
        if not k6_file.exists():
            print(f"  [{i}/{len(by_run)}] skip {run_label}: file missing")
            continue

        t0 = time.time()
        points = load_k6_latencies(k6_file)
        if not points:
            continue
        timestamps = [p[0] for p in points]

        # Find T_SLO_risk once per threshold (one pass per threshold, but
        # cheap since points/timestamps are pre-loaded).
        t_slo_risk_by_threshold: dict[int, datetime | None] = {}
        for thr in THRESHOLDS_MS:
            t_slo_risk_by_threshold[thr] = find_t_slo_risk(points, timestamps, thr)

        # Score each decision at each threshold
        for d in run_decisions:
            direction = (d.get("direction") or "").lower()
            scaling_limited = (str(d.get("scaling_limited", "")).strip() == "True")
            limit_reason = (d.get("scaling_limit_reason") or "").strip()
            pattern = d["pattern"]

            for thr in THRESHOLDS_MS:
                t_slo_risk = t_slo_risk_by_threshold[thr]
                if direction == "down":
                    src = "scale_down"
                    srd = None
                elif t_slo_risk is None:
                    src = "no_slo_breach"
                    srd = None
                else:
                    srd = (d["_ts"] - t_slo_risk).total_seconds()
                    src = "pre_emptive" if srd < 0 else "late"

                bucket = srd_bucket(direction, src, srd,
                                    scaling_limited, limit_reason)
                results[thr][pattern].append({
                    "src": src,
                    "srd": srd,
                    "bucket": bucket,
                    "direction": direction,
                })

        print(f"  [{i}/{len(by_run)}] {run_label}: "
              f"{len(points)} points, {len(run_decisions)} decisions "
              f"({time.time()-t0:.1f}s)", flush=True)

    print(f"Done in {time.time() - tstart:.0f}s")

    # =================================================================
    # WRITE SUMMARY CSV
    # =================================================================
    print(f"Writing {SUMMARY_CSV.name}...")
    summary_rows: list[dict] = []
    for thr in THRESHOLDS_MS:
        for pattern in PATTERNS:
            rows = results[thr].get(pattern, [])
            src_counts = defaultdict(int)
            bkt_counts = defaultdict(int)
            for r in rows:
                src_counts[r["src"]] += 1
                bkt_counts[r["bucket"]] += 1
            summary_rows.append({
                "threshold_ms": thr,
                "pattern": pattern,
                "n_decisions": len(rows),
                "count_late":               src_counts.get("late", 0),
                "count_pre_emptive":        src_counts.get("pre_emptive", 0),
                "count_no_slo_breach":      src_counts.get("no_slo_breach", 0),
                "count_scale_down":         src_counts.get("scale_down", 0),
                "bucket_correct_timely":    bkt_counts.get("Correct & Timely", 0),
                "bucket_correct_but_late":  bkt_counts.get("Correct but Late", 0),
                "bucket_unnecessary":       bkt_counts.get("Unnecessary", 0),
                "bucket_ineffective":       bkt_counts.get("Ineffective", 0),
            })
    with open(SUMMARY_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)
    print(f"  Wrote {len(summary_rows)} rows")

    # =================================================================
    # FIGURE 1: SRD histogram per threshold (overlay of patterns)
    # =================================================================
    print("Rendering srd_sensitivity_by_threshold.png...")
    n_thr = len(THRESHOLDS_MS)
    fig, axes = plt.subplots(1, n_thr, figsize=(4 * n_thr, 5.5),
                             sharey=True, squeeze=False)
    all_srds = [r["srd"] for thr in THRESHOLDS_MS
                for pat in PATTERNS
                for r in results[thr].get(pat, [])
                if r["srd"] is not None]
    if all_srds:
        span = max(abs(min(all_srds)), abs(max(all_srds))) * 1.05
    else:
        span = 60.0
    bin_edges = np.linspace(-span, span, 26)

    for ax_col, thr in zip(axes[0], THRESHOLDS_MS):
        for pattern in PATTERNS:
            vals = [r["srd"] for r in results[thr].get(pattern, [])
                    if r["srd"] is not None]
            if not vals:
                continue
            ax_col.hist(vals, bins=bin_edges,
                        alpha=0.55, color=PATTERN_COLORS[pattern],
                        edgecolor="black", linewidth=0.3,
                        label=f"{pattern.capitalize()} (n={len(vals)})")
        ax_col.axvline(0, color="black", linestyle="--", alpha=0.6, linewidth=1)
        ax_col.set_title(f"SLO threshold = {thr} ms")
        ax_col.set_xlabel("SRD (s)")
        ax_col.grid(True, alpha=0.3)
        ax_col.legend(loc="upper right", fontsize=8)

    axes[0][0].set_ylabel("Number of scale-up decisions")
    fig.suptitle("SRD Distribution — SLO Threshold Sensitivity Analysis\n"
                 "Same campaign data, re-scored at different SLO thresholds",
                 fontsize=12)
    plt.tight_layout()
    outpath = PLOTS_DIR / "srd_sensitivity_by_threshold.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"  Saved {outpath.name}")
    plt.close()

    # =================================================================
    # FIGURE 2: bucket distribution per threshold (grouped bars)
    # =================================================================
    print("Rendering bucket_sensitivity_by_threshold.png...")
    fig, axes = plt.subplots(1, n_thr, figsize=(4.5 * n_thr, 5.5),
                             sharey=True, squeeze=False)
    x = np.arange(len(PATTERNS))
    bar_width = 0.19

    for ax_col, thr in zip(axes[0], THRESHOLDS_MS):
        # Build the (pattern → bucket → count) grid for this threshold
        bkt_by_pat: dict[str, dict[str, int]] = {
            p: {b: 0 for b in BUCKETS} for p in PATTERNS
        }
        for pattern in PATTERNS:
            for r in results[thr].get(pattern, []):
                bkt_by_pat[pattern][r["bucket"]] += 1
        for k, bucket in enumerate(BUCKETS):
            values = [bkt_by_pat[p][bucket] for p in PATTERNS]
            offset = (k - (len(BUCKETS) - 1) / 2) * bar_width
            bars = ax_col.bar(x + offset, values, bar_width,
                              color=BUCKET_COLORS[bucket],
                              edgecolor="black", linewidth=0.4,
                              label=bucket)
            for b, v in zip(bars, values):
                if v > 0:
                    ax_col.text(b.get_x() + b.get_width() / 2,
                                b.get_height() + 0.5,
                                str(v), ha="center", va="bottom", fontsize=7)
        ax_col.set_xticks(x)
        ax_col.set_xticklabels([p.capitalize() for p in PATTERNS], fontsize=9)
        ax_col.set_title(f"SLO threshold = {thr} ms")
        ax_col.grid(axis="y", linestyle=":", alpha=0.5)
        if ax_col is axes[0][0]:
            ax_col.set_ylabel("Number of decisions")
            ax_col.legend(loc="upper right", fontsize=8, framealpha=0.95)

    fig.suptitle("SRD-Based Bucket Distribution — SLO Threshold Sensitivity",
                 fontsize=12)
    plt.tight_layout()
    outpath = PLOTS_DIR / "bucket_sensitivity_by_threshold.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"  Saved {outpath.name}")
    plt.close()

    # =================================================================
    # Console summary
    # =================================================================
    print()
    print("=" * 84)
    print(f"{'Threshold':>10}  {'Pattern':<8}  "
          f"{'C&T':>5}  {'CbL':>5}  {'Unn':>5}  {'Ineff':>5}  "
          f"{'late':>5}  {'preemp':>7}  {'nobrch':>7}  {'scdn':>5}")
    print("=" * 84)
    for thr in THRESHOLDS_MS:
        for pattern in PATTERNS:
            rows = results[thr].get(pattern, [])
            if not rows:
                print(f"{thr:>10}  {pattern:<8}  (no decisions)")
                continue
            src_counts = defaultdict(int)
            bkt_counts = defaultdict(int)
            for r in rows:
                src_counts[r["src"]] += 1
                bkt_counts[r["bucket"]] += 1
            print(f"{thr:>10}  {pattern:<8}  "
                  f"{bkt_counts.get('Correct & Timely', 0):>5}  "
                  f"{bkt_counts.get('Correct but Late', 0):>5}  "
                  f"{bkt_counts.get('Unnecessary', 0):>5}  "
                  f"{bkt_counts.get('Ineffective', 0):>5}  "
                  f"{src_counts.get('late', 0):>5}  "
                  f"{src_counts.get('pre_emptive', 0):>7}  "
                  f"{src_counts.get('no_slo_breach', 0):>7}  "
                  f"{src_counts.get('scale_down', 0):>5}")
        print("-" * 84)


if __name__ == "__main__":
    main()
