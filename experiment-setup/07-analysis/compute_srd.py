#!/usr/bin/env python3
"""
compute_srd.py — Compute Scale Reaction Delay (SRD) per HPA scale-up decision.

Per analysis/slo_risk_and_ses_methodology.md §2:

    T_SLO_risk := first t in a run such that
                  p95( http_req_duration over [t - 30s, t] ) > 500 ms

    SRD := T_decision − T_SLO_risk
           (positive → HPA reacted after SLO breach; late)
           (negative → HPA reacted before SLO breach; pre-emptive)

SRD is defined only for scale-up decisions. Per the methodology, scale-down
decisions do not measure "reaction to SLO risk" and are excluded (srd is
NULL, srd_source = "scale_down").

If a run never observes p95 > 500 ms, T_SLO_risk is undefined for that run;
scale-up decisions in that run get srd = NULL, srd_source = "no_slo_breach".

This step is NOT part of the bucket classifier (buckets are decided by
classify_decisions.py using CPU thresholds per classification_rules.pdf).
SRD is an auxiliary quantitative metric that lives alongside the buckets
and drives the timeliness sensitivity analysis in the thesis.

Pipeline position:
  build_master_dataset.py → master_decisions.csv
  classify_decisions.py   → classified_decisions.csv
  compute_srd.py          → decisions_with_srd.csv   ← this file
  compute_ses.py          → decisions_with_ses.csv   (reads from _srd.csv)

Reads:
  - results/classified_decisions.csv
  - results/run_index.csv
  - results/{pattern}-run-{TS}.json  (per-run k6 raw output)

Writes:
  - results/decisions_with_srd.csv

Added columns:
  - t_slo_risk_utc      — timestamp of first SLO breach for the decision's run,
                          or NULL if never breached / N/A
  - srd_seconds         — signed float (positive = late, negative = pre-emptive)
                          or NULL if not applicable
  - srd_source          — one of 'pre_emptive' | 'late' | 'no_slo_breach' | 'scale_down'
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

# ============================================================================
# CONFIG — mirrors methodology doc §2
# ============================================================================
ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
INPUT_CSV = RESULTS_DIR / "classified_decisions.csv"
RUN_INDEX_CSV = RESULTS_DIR / "run_index.csv"
OUTPUT_CSV = RESULTS_DIR / "decisions_with_srd.csv"

# SLO detection parameters. Kept as module constants so the sensitivity
# analysis in the thesis (methodology §4) can vary them.
# Override via environment variables if you want to try a different value
# without editing this file:
#     SLO_THRESHOLD_MS=300 SLO_SUSTAINED_SAMPLES=5 python3 compute_srd.py all
import os as _os
SLO_THRESHOLD_MS = int(_os.environ.get("SLO_THRESHOLD_MS", "500"))
SLO_WINDOW_SECONDS = int(_os.environ.get("SLO_WINDOW_SECONDS", "30"))
MIN_SAMPLES_FOR_P95 = 20        # skip windows with too few requests to trust p95

# SUSTAINED requirement: T_SLO_risk fires only when p95 has been above
# threshold for at least SLO_SUSTAINED_SAMPLES consecutive request-timestamp
# checks. This suppresses spurious early-run breaches from cold-start
# spikes or single-request outliers.
# Default 3 corresponds to ~1-3 seconds of continuous breach at burst
# throughput. Set to 1 to reproduce the original "first-hit" behaviour.
SLO_SUSTAINED_SAMPLES = int(_os.environ.get("SLO_SUSTAINED_SAMPLES", "3"))


# ============================================================================
# Timestamp parsing (matches compute_ses.py's parse_iso)
# ============================================================================
def parse_iso(s):
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.+?\.)(\d+)(.+)$", s)
    if m:
        micros = m.group(2)[:6].ljust(6, "0")
        s = m.group(1) + micros + m.group(3)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


# ============================================================================
# k6 loader (identical to compute_ses.py — filters to expected_response=true)
# ============================================================================
def load_k6_latencies(k6_path):
    """
    Return sorted [(datetime_utc, latency_ms), ...] for successful requests
    only. See compute_ses.py.load_k6_latencies for design notes.
    """
    points = []
    pattern = re.compile(
        r'"time":"([^"]+)".*?"value":([0-9.eE+\-]+).*?"expected_response":"true"'
    )
    with open(k6_path) as f:
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


# ============================================================================
# T_SLO_risk computation
# ============================================================================
def p95(values):
    if not values:
        return None
    s = sorted(values)
    idx = int(0.95 * (len(s) - 1))
    return s[idx]


def find_t_slo_risk(points, timestamps):
    """
    Find T_SLO_risk = first request-timestamp t such that the p95 of
    http_req_duration in [t - SLO_WINDOW_SECONDS, t] has been above
    SLO_THRESHOLD_MS for SLO_SUSTAINED_SAMPLES consecutive checks.

    Uses request timestamps as the sampling grid. Windows with fewer
    than MIN_SAMPLES_FOR_P95 requests are skipped to avoid spurious
    breaches from single slow outliers early in the run.

    The "sustained" requirement (SLO_SUSTAINED_SAMPLES > 1) is what
    suppresses cold-start blips: a single 30-second window trip is not
    enough — the p95 must stay above threshold for multiple
    consecutive samples. Returns the timestamp of the FIRST sample in
    the sustained-breach run so downstream SRD reflects the actual
    onset of the SLO breach, not the moment of confirmation.

    Returns a datetime, or None if no sustained SLO breach is found.
    """
    consecutive_breaches = 0
    first_breach_ts = None
    for i, (t, _) in enumerate(points):
        window_start = t - timedelta(seconds=SLO_WINDOW_SECONDS)
        lo = bisect.bisect_left(timestamps, window_start)
        hi = i + 1  # include this request in its own window
        if hi - lo < MIN_SAMPLES_FOR_P95:
            # window too sparse to trust — reset any running streak
            consecutive_breaches = 0
            first_breach_ts = None
            continue
        window_vals = [points[j][1] for j in range(lo, hi)]
        if p95(window_vals) > SLO_THRESHOLD_MS:
            if consecutive_breaches == 0:
                first_breach_ts = t
            consecutive_breaches += 1
            if consecutive_breaches >= SLO_SUSTAINED_SAMPLES:
                return first_breach_ts
        else:
            # streak broken — reset
            consecutive_breaches = 0
            first_breach_ts = None
    return None


# ============================================================================
# Main
# ============================================================================
def main():
    filter_pattern = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"Filter: {filter_pattern}")
    print(f"SLO threshold: p95 > {SLO_THRESHOLD_MS} ms")
    print(f"SLO detection window: {SLO_WINDOW_SECONDS} s rolling")
    print(f"Minimum samples per window: {MIN_SAMPLES_FOR_P95}")
    print(f"Sustained-breach requirement: {SLO_SUSTAINED_SAMPLES} "
          f"consecutive windows above threshold")
    print()

    # 1. Load run index → maps run_label → k6 file path
    print("Loading run index...")
    run_index = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            if filter_pattern != "all" and r["pattern"] != filter_pattern:
                continue
            run_index[r["run_label"]] = {
                "file_path": r["file_path"],
                "pattern": r["pattern"],
            }
    print(f"  Loaded {len(run_index)} runs")

    # 2. Load classified decisions
    print("Loading classified decisions...")
    decisions_by_run = defaultdict(list)
    all_decisions = []
    with open(INPUT_CSV) as f:
        for r in csv.DictReader(f):
            r["_ts"] = parse_iso(r["timestamp_utc"])
            if filter_pattern != "all" and r.get("pattern") != filter_pattern:
                continue
            all_decisions.append(r)
            if r["run_label"] and r["run_label"] != "between_runs":
                decisions_by_run[r["run_label"]].append(r)
    print(f"  Loaded {len(all_decisions)} decisions "
          f"({sum(len(v) for v in decisions_by_run.values())} tagged to runs)")

    # 3. Per-run: compute T_SLO_risk, then SRD for each decision
    print("Computing T_SLO_risk and SRD per decision...")
    n_pre_emptive = 0
    n_late = 0
    n_no_breach = 0
    n_scale_down = 0
    t_start = time.time()

    # Persistent per-run cache so we compute T_SLO_risk only once per run.
    t_slo_risk_by_run = {}

    for i, (run_label, decisions) in enumerate(sorted(decisions_by_run.items()), 1):
        run = run_index.get(run_label)
        if not run:
            for d in decisions:
                d["t_slo_risk_utc"] = None
                d["srd_seconds"] = None
                d["srd_source"] = "no_slo_breach"
                n_no_breach += 1
            continue

        k6_file = RESULTS_DIR / run["file_path"]
        if not k6_file.exists():
            print(f"  [{i}/{len(decisions_by_run)}] Skipping {run_label}: "
                  f"k6 file missing", flush=True)
            for d in decisions:
                d["t_slo_risk_utc"] = None
                d["srd_seconds"] = None
                d["srd_source"] = "no_slo_breach"
                n_no_breach += 1
            continue

        t0 = time.time()
        points = load_k6_latencies(k6_file)
        timestamps = [p[0] for p in points]
        t_slo_risk = find_t_slo_risk(points, timestamps) if points else None
        t_slo_risk_by_run[run_label] = t_slo_risk

        for d in decisions:
            direction = d.get("direction", "up")
            if direction == "down":
                d["t_slo_risk_utc"] = None
                d["srd_seconds"] = None
                d["srd_source"] = "scale_down"
                n_scale_down += 1
                continue

            if t_slo_risk is None:
                d["t_slo_risk_utc"] = None
                d["srd_seconds"] = None
                d["srd_source"] = "no_slo_breach"
                n_no_breach += 1
                continue

            srd_s = (d["_ts"] - t_slo_risk).total_seconds()
            d["t_slo_risk_utc"] = t_slo_risk.isoformat()
            d["srd_seconds"] = round(srd_s, 3)
            if srd_s < 0:
                d["srd_source"] = "pre_emptive"
                n_pre_emptive += 1
            else:
                d["srd_source"] = "late"
                n_late += 1

        slo_msg = (f"T_SLO_risk={t_slo_risk.isoformat()}"
                   if t_slo_risk else "no SLO breach")
        print(f"  [{i}/{len(decisions_by_run)}] {run_label}: "
              f"{len(decisions)} decisions, {slo_msg} "
              f"(loaded {len(points)} points in {time.time()-t0:.1f}s, "
              f"elapsed {time.time()-t_start:.0f}s)", flush=True)

    print(f"\n  SRD source breakdown:")
    print(f"    late         : {n_late}")
    print(f"    pre_emptive  : {n_pre_emptive}")
    print(f"    no_slo_breach: {n_no_breach}")
    print(f"    scale_down   : {n_scale_down}")

    # 4. Write augmented CSV
    output_csv = (
        OUTPUT_CSV if filter_pattern == "all"
        else RESULTS_DIR / f"decisions_with_srd_{filter_pattern}.csv"
    )
    print(f"\nWriting {output_csv.name}...")

    extra_fields = ["t_slo_risk_utc", "srd_seconds", "srd_source"]
    base_fields = [k for k in all_decisions[0].keys()
                   if k != "_ts" and k not in extra_fields]
    fieldnames = base_fields + extra_fields

    with open(output_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for d in all_decisions:
            row = {k: d.get(k) for k in fieldnames}
            w.writerow(row)
    print(f"  Wrote {len(all_decisions)} rows")

    # 5. Per-pattern SRD aggregates
    print()
    print("=" * 78)
    print("SRD SUMMARY — per pattern (scale-up decisions with SLO breach only)")
    print("=" * 78)
    by_pattern = defaultdict(list)
    for d in all_decisions:
        if d.get("srd_source") in ("late", "pre_emptive") \
                and d.get("srd_seconds") is not None:
            by_pattern[d["pattern"]].append(float(d["srd_seconds"]))

    print(f"\n{'Pattern':<8} {'N':>5} {'Mean SRD':>10} {'Median':>10} "
          f"{'Min':>10} {'Max':>10}")
    print("-" * 62)
    for pattern in ["step", "burst", "ramp", "noisy"]:
        vals = by_pattern.get(pattern, [])
        if not vals:
            print(f"{pattern:<8} {'0':>5} {'n/a':>10} {'n/a':>10} "
                  f"{'n/a':>10} {'n/a':>10}")
            continue
        print(f"{pattern:<8} {len(vals):>5} "
              f"{statistics.mean(vals):>10.3f} "
              f"{statistics.median(vals):>10.3f} "
              f"{min(vals):>10.3f} {max(vals):>10.3f}")

    print()
    print(f"Interpretation: positive SRD = HPA reacted AFTER SLO breach (late);")
    print(f"                negative SRD = HPA reacted BEFORE SLO breach (pre-emptive).")


if __name__ == "__main__":
    main()
