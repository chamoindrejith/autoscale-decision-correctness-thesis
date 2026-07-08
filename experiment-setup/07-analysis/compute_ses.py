#!/usr/bin/env python3
"""
compute_ses.py — Compute Scale Effectiveness Score (SES) per HPA decision.

Per Chamodi's research proposal (Section 7.2):
  SES = (Latency_before - Latency_after) / Latency_before

Where Latency is the p95 of http_req_duration in the window. Windows follow
analysis/slo_risk_and_ses_methodology.md §2:

  Latency_before := p95 over [T_decision - 60s, T_decision - 1s]
                   (60 s ending 1 s before the decision, so the decision
                    moment itself is not sampled)

  Latency_after  := p95 over [T_pod_Ready, T_pod_Ready + 60s]
                   (60 s starting from when the newly created pod is Ready,
                    NOT from T_decision — see methodology §3.5 for the
                    reasoning: anchoring at T_decision would sample during
                    the 20-40 s pod-startup phase and systematically bias
                    correct scale-ups into the Ineffective bucket)

For scale-down decisions there is no new pod. Following methodology §2, the
after-window is anchored at T_decision + 30 s so the terminating pod can
complete its preStop grace and drain in-flight requests.

T_pod_Ready comes from the v3 watcher's pod_ready events (per-run event
files in results/{pattern}-events-{TS}.json). Where no matching pod_ready is
found for a scale-up decision (edge case: pod never becomes ready, or event
file missing), the anchor falls back to T_decision + 60 s and the row is
flagged with t_after_source = "fallback" so the reader can see it happened.

Reads:
  - results/decisions_with_srd.csv          (decisions augmented with SRD)
  - results/run_index.csv                   (mapping run_label -> k6 file)
  - results/{pattern}-events-{TS}.json      (per-run watcher event captures)
  - results/{pattern}-run-{TS}.json         (per-request latency data)

Writes:
  - results/decisions_with_ses.csv          (decisions augmented with SES,
                                              t_pod_ready_utc, t_after_source,
                                              cold_start_delay_s — SRD columns
                                              from the input are passed through)
  - results/ses_summary.csv                 (per-pattern aggregates)

Note on pipeline order: this file reads from decisions_with_srd.csv (not
classified_decisions.csv directly) so the final decisions_with_ses.csv
contains all metrics — bucket, SRD, and SES — in one row per decision.
Run classify_decisions.py, then compute_srd.py, then this script.
"""
import json
import csv
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from pod_ready_lookup import (
    load_pod_ready_events,
    find_pod_ready_for_decision,
)

# ============================================================================
# CONFIG
# ============================================================================
ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
# Reads decisions_with_srd.csv (the output of compute_srd.py) so the final
# decisions_with_ses.csv inherits both bucket, SRD, and SES columns.
# Falls back to classified_decisions.csv if the SRD step wasn't run yet
# (backwards compatibility).
INPUT_CSV = RESULTS_DIR / "decisions_with_srd.csv"
if not INPUT_CSV.exists():
    INPUT_CSV = RESULTS_DIR / "classified_decisions.csv"
CLASSIFIED_CSV = INPUT_CSV  # legacy variable name kept for clarity below
RUN_INDEX_CSV = RESULTS_DIR / "run_index.csv"
OUTPUT_CSV = RESULTS_DIR / "decisions_with_ses.csv"
SUMMARY_CSV = RESULTS_DIR / "ses_summary.csv"

# Window sizes (in seconds). See methodology §2.
BEFORE_WINDOW_LOOKBACK = 60   # window starts this many seconds before T_decision
BEFORE_WINDOW_EXCLUSION = 1   # window ends this many seconds before T_decision
AFTER_WINDOW_DURATION = 60    # window duration starting from T_pod_Ready

# Latency statistic to use
LATENCY_PERCENTILE = 95    # p95


def parse_iso(s):
    """Parse ISO timestamp robustly (handles 5-digit microseconds + tz offsets)."""
    s = s.replace('Z', '+00:00')
    m = re.match(r'^(.+?\.)(\d+)(.+)$', s)
    if m:
        micros = m.group(2)[:6].ljust(6, '0')
        s = m.group(1) + micros + m.group(3)
    dt = datetime.fromisoformat(s)
    return dt.astimezone(timezone.utc)


def p95(values):
    """Compute the 95th percentile of a list of values. Returns None if empty."""
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = int(0.95 * (len(sorted_vals) - 1))
    return sorted_vals[idx]


def load_k6_latencies(k6_path):
    """
    Stream through a k6 JSONL file and extract (timestamp, latency_ms) pairs
    for all successful http_req_duration points.

    Optimised: uses cheap string pre-filters to skip the vast majority of
    lines (which are non-duration metrics) BEFORE invoking the JSON parser.
    The k6 files are 24 MB each but only ~14% of lines are http_req_duration
    points; pre-filtering gives ~7x speedup.

    Returns: sorted list of (datetime_utc, latency_ms) tuples.
    """
    points = []
    # Pre-compile a tight regex for fast extraction without full JSON parse.
    # Each relevant line looks like:
    #   {"type":"Point","data":{"time":"2026-06-...","value":104.799,"tags":{...,"expected_response":"true",...}},"metric":"http_req_duration"}
    # We extract time and value with regex (orders of magnitude faster than json.loads).
    pattern = re.compile(
        r'"time":"([^"]+)".*?"value":([0-9.eE+\-]+).*?"expected_response":"true"'
    )

    with open(k6_path, 'r') as f:
        for line in f:
            # Cheap byte-substring filters first
            if 'http_req_duration' not in line:
                continue
            m = pattern.search(line)
            if not m:
                continue
            time_str = m.group(1)
            value = m.group(2)
            try:
                ts = parse_iso(time_str)
                points.append((ts, float(value)))
            except (ValueError, TypeError):
                continue
    points.sort(key=lambda p: p[0])
    return points


def latency_in_window(points, timestamps, t_start, t_end):
    """
    Given a sorted list of (ts, value) tuples and a parallel sorted list of
    timestamps, return the values in [t_start, t_end] using binary search.
    O(log n) per query instead of O(n).
    """
    import bisect
    lo = bisect.bisect_left(timestamps, t_start)
    hi = bisect.bisect_right(timestamps, t_end)
    return [points[i][1] for i in range(lo, hi)]


def compute_ses(decision_ts, direction, pod_ready_events, points, timestamps):
    """
    Compute SES = (Latency_before - Latency_after) / Latency_before.

    Uses methodology §2 windowing:
      - Before window: [T_decision - 60s, T_decision - 1s]
      - After window: [T_pod_Ready, T_pod_Ready + 60s]
        (T_pod_Ready is resolved via pod_ready_lookup; scale-down and
         no-match cases use the documented fallbacks)

    Returns dict with both latencies, sample counts, SES, and metadata about
    which T_pod_Ready anchor was used (t_pod_ready_utc, t_after_source,
    t_after_pod_name). SES is None if either window is empty.
    """
    t = decision_ts

    # Before window is unchanged from previous code — anchored on T_decision.
    before_start = t - timedelta(seconds=BEFORE_WINDOW_LOOKBACK)
    before_end = t - timedelta(seconds=BEFORE_WINDOW_EXCLUSION)
    before_vals = latency_in_window(points, timestamps, before_start, before_end)

    # After window is anchored at T_pod_Ready, NOT T_decision. See
    # methodology §3.5 for reasoning; the exact rules for pod-ready lookup
    # (including fallbacks) live in pod_ready_lookup.py.
    t_after_anchor, t_after_source, t_after_pod_name = find_pod_ready_for_decision(
        decision_ts=t,
        direction=direction,
        pod_ready_events=pod_ready_events,
    )
    after_start = t_after_anchor
    after_end = t_after_anchor + timedelta(seconds=AFTER_WINDOW_DURATION)
    after_vals = latency_in_window(points, timestamps, after_start, after_end)

    p95_before = p95(before_vals)
    p95_after = p95(after_vals)

    result = {
        'p95_before_ms': p95_before,
        'p95_after_ms': p95_after,
        'n_before': len(before_vals),
        'n_after': len(after_vals),
        'ses': None,
        # New v3 metadata — makes the T_pod_Ready anchor auditable.
        't_pod_ready_utc': t_after_anchor.isoformat(),
        't_after_source': t_after_source,
        't_after_pod_name': t_after_pod_name,
        # Cold-start delay = T_pod_Ready - T_decision (seconds). Useful as
        # an auxiliary metric per Suvin's guidance; not part of SES.
        'cold_start_delay_s': round((t_after_anchor - t).total_seconds(), 3)
                              if t_after_source in ('pod_ready',) else None,
    }
    if p95_before is not None and p95_after is not None and p95_before > 0:
        result['ses'] = (p95_before - p95_after) / p95_before
    return result


def main():
    import sys
    # Optional CLI: filter to one pattern at a time to fit under timeouts.
    # python3 compute_ses.py [step|burst|ramp|noisy|all]
    filter_pattern = sys.argv[1] if len(sys.argv) > 1 else 'all'
    print(f"Filter: {filter_pattern}")

    # 1. Load run index → maps run_label to k6 file path
    print("Loading run index...")
    run_index = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            if filter_pattern != 'all' and r['pattern'] != filter_pattern:
                continue
            run_index[r['run_label']] = {
                'file_path': r['file_path'],
                'pattern': r['pattern'],
                'start': parse_iso(r['start_utc']),
                'end': parse_iso(r['end_utc']),
            }
    print(f"  Loaded {len(run_index)} runs (filter={filter_pattern})")

    # 2. Load classified decisions, group by run_label.
    # When filtering by pattern, only keep decisions for that pattern's runs.
    print("Loading classified decisions...")
    decisions_by_run = defaultdict(list)
    all_decisions = []
    with open(CLASSIFIED_CSV) as f:
        for r in csv.DictReader(f):
            r['_ts'] = parse_iso(r['timestamp_utc'])
            if filter_pattern != 'all' and r.get('pattern') != filter_pattern:
                continue
            all_decisions.append(r)
            if r['run_label'] and r['run_label'] != 'between_runs':
                decisions_by_run[r['run_label']].append(r)
    print(f"  Loaded {len(all_decisions)} decisions ({sum(len(v) for v in decisions_by_run.values())} tagged to runs)")

    # 2b. Load pod_ready events from all watcher-event files under results/.
    # These are per-run captures produced by run-campaign.sh's `kubectl logs
    # --since-time=...` step. pod_ready_lookup.load_pod_ready_events walks
    # the directory and picks up every {pattern}-events-*.json file.
    print("Loading pod_ready events from results/...")
    pod_ready_events = load_pod_ready_events(RESULTS_DIR)
    print(f"  Loaded {len(pod_ready_events)} pod_ready events")

    # 3. For each run, load its k6 latencies once, then compute SES for all its decisions
    # Progress-friendly: log each run as completed so we can monitor from another shell.
    print("Computing SES per decision...")
    n_ses_computed = 0
    n_ses_missing_window = 0
    n_pod_ready = 0
    n_fallback = 0
    n_scale_down = 0
    import time as _time
    t_start = _time.time()

    for i, (run_label, decisions) in enumerate(sorted(decisions_by_run.items()), 1):
        run = run_index.get(run_label)
        if not run:
            continue
        k6_file = RESULTS_DIR / run['file_path']
        if not k6_file.exists():
            print(f"  [{i}/{len(decisions_by_run)}] Skipping {run_label}: k6 file not found", flush=True)
            continue

        t0 = _time.time()
        points = load_k6_latencies(k6_file)
        if not points:
            print(f"  [{i}/{len(decisions_by_run)}] Skipping {run_label}: no latency points", flush=True)
            continue
        # Build parallel timestamps list once per run for fast binary search
        timestamps = [p[0] for p in points]

        for d in decisions:
            res = compute_ses(
                decision_ts=d['_ts'],
                direction=d['direction'],
                pod_ready_events=pod_ready_events,
                points=points,
                timestamps=timestamps,
            )
            d['p95_before_ms'] = res['p95_before_ms']
            d['p95_after_ms'] = res['p95_after_ms']
            d['n_before_requests'] = res['n_before']
            d['n_after_requests'] = res['n_after']
            d['ses'] = res['ses']
            # New: T_pod_Ready anchor metadata (auditable per methodology)
            d['t_pod_ready_utc'] = res['t_pod_ready_utc']
            d['t_after_source'] = res['t_after_source']
            d['t_after_pod_name'] = res['t_after_pod_name']
            d['cold_start_delay_s'] = res['cold_start_delay_s']
            if res['ses'] is not None:
                n_ses_computed += 1
            else:
                n_ses_missing_window += 1
            if res['t_after_source'] == 'pod_ready':
                n_pod_ready += 1
            elif res['t_after_source'] == 'fallback':
                n_fallback += 1
            elif res['t_after_source'] == 'scale_down':
                n_scale_down += 1
        print(f"  [{i}/{len(decisions_by_run)}] {run_label}: {len(decisions)} decisions, "
              f"loaded {len(points)} points in {_time.time()-t0:.1f}s "
              f"(elapsed: {_time.time()-t_start:.0f}s)", flush=True)

    print(f"  SES computed for {n_ses_computed} decisions")
    print(f"  Missing window data for {n_ses_missing_window} decisions (typically near run boundaries)")
    print(f"  T_after anchor source: {n_pod_ready} pod_ready, "
          f"{n_fallback} fallback, {n_scale_down} scale_down")

    # 4. Write augmented decisions CSV (per-pattern when filtered)
    output_csv = OUTPUT_CSV if filter_pattern == 'all' else \
                 RESULTS_DIR / f"decisions_with_ses_{filter_pattern}.csv"
    print(f"Writing {output_csv.name}...")
    sample = next((d for d in all_decisions if 'ses' in d), all_decisions[0])
    extra_fields = ['p95_before_ms', 'p95_after_ms', 'n_before_requests',
                    'n_after_requests', 'ses',
                    't_pod_ready_utc', 't_after_source', 't_after_pod_name',
                    'cold_start_delay_s']
    fieldnames = [k for k in all_decisions[0].keys() if k != '_ts' and k not in extra_fields] + extra_fields
    with open(output_csv, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        for d in all_decisions:
            # Format floats nicely; None stays None
            row = {k: d.get(k) for k in fieldnames}
            for fk in ['p95_before_ms', 'p95_after_ms', 'ses']:
                v = row.get(fk)
                if isinstance(v, float):
                    row[fk] = round(v, 4)
            w.writerow(row)
    print(f"  Wrote {len(all_decisions)} rows")

    # 5. Per-pattern × direction aggregates
    print("\n" + "=" * 78)
    print("SES SUMMARY — per pattern × direction")
    print("=" * 78)
    print(f"\n{'Pattern':<8} {'Dir':<6} {'N':>5} {'Mean SES':>10} {'Median SES':>11} {'Min':>8} {'Max':>8}")
    print("-" * 70)
    by_group = defaultdict(list)
    for d in all_decisions:
        if not d.get('pattern') or d.get('ses') is None:
            continue
        by_group[(d['pattern'], d['direction'])].append(d['ses'])

    summary_rows = []
    for pattern in ['step', 'burst', 'ramp', 'noisy']:
        for direction in ['up', 'down']:
            vals = by_group.get((pattern, direction), [])
            if not vals:
                continue
            mean_ses = statistics.mean(vals)
            median_ses = statistics.median(vals)
            print(f"{pattern:<8} {direction:<6} {len(vals):>5} {mean_ses:>10.4f} {median_ses:>11.4f} "
                  f"{min(vals):>8.4f} {max(vals):>8.4f}")
            summary_rows.append({
                'pattern': pattern,
                'direction': direction,
                'n_decisions': len(vals),
                'mean_ses': round(mean_ses, 4),
                'median_ses': round(median_ses, 4),
                'min_ses': round(min(vals), 4),
                'max_ses': round(max(vals), 4),
            })

    # Pattern-level aggregates (combining up + down)
    print(f"\n{'Pattern':<8} {'Combined':<7} {'N':>5} {'Mean SES':>10} {'Median SES':>11}")
    print("-" * 50)
    for pattern in ['step', 'burst', 'ramp', 'noisy']:
        vals = [v for (p, _), vs in by_group.items() if p == pattern for v in vs]
        if vals:
            mean_ses = statistics.mean(vals)
            median_ses = statistics.median(vals)
            print(f"{pattern:<8} {'all':<7} {len(vals):>5} {mean_ses:>10.4f} {median_ses:>11.4f}")
            summary_rows.append({
                'pattern': pattern,
                'direction': 'all',
                'n_decisions': len(vals),
                'mean_ses': round(mean_ses, 4),
                'median_ses': round(median_ses, 4),
                'min_ses': round(min(vals), 4),
                'max_ses': round(max(vals), 4),
            })

    # Write summary CSV
    with open(SUMMARY_CSV, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['pattern', 'direction', 'n_decisions',
                                          'mean_ses', 'median_ses', 'min_ses', 'max_ses'])
        w.writeheader()
        w.writerows(summary_rows)
    print(f"\nWrote per-pattern aggregates to {SUMMARY_CSV.name}")

    # Note on negative SES (latency got worse after scaling)
    negatives = [d for d in all_decisions if d.get('ses') is not None and d['ses'] < 0]
    print(f"\nDecisions with negative SES (latency worsened after action): {len(negatives)}")
    if negatives[:5]:
        print("Sample (first 5):")
        for d in negatives[:5]:
            print(f"  {d.get('run_label')} {d.get('direction')} "
                  f"p95: {d.get('p95_before_ms')}→{d.get('p95_after_ms')} ms, SES={d.get('ses'):.3f}")


if __name__ == '__main__':
    main()
