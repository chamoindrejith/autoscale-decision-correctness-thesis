#!/usr/bin/env python3
"""
extract_ses_input_dataset.py — Output the RAW per-request latency points that
were used to compute each SES value. One row per request, tagged with which
decision and which window it belongs to.

This lets reviewers (and you) verify the p95 calculation, try alternative
statistics, or re-window without re-running the full SES script.

Windowing follows analysis/slo_risk_and_ses_methodology.md §2 and must stay
in sync with compute_ses.py:

  Before window: [T_decision - 60s, T_decision - 1s]
  After window:  [T_pod_Ready, T_pod_Ready + 60s]
                 (T_pod_Ready comes from the v3 watcher's pod_ready events;
                  falls back to T_decision + 60 s if no matching event; uses
                  T_decision + 30 s for scale-down decisions)

Reads:
  - results/decisions_with_ses.csv     (decisions + computed SES)
  - results/run_index.csv               (run_label -> k6 file)
  - results/{pattern}-events-{TS}.json  (per-run watcher event captures)
  - results/{pattern}-run-{TS}.json     (per-request latency data)

Writes:
  - results/ses_input_dataset.csv       (long format; one row per request used)

Run pattern-by-pattern via CLI:
  python3 extract_ses_input_dataset.py step
  python3 extract_ses_input_dataset.py burst
  ... etc.
  Or 'all' to do all four (may exceed 45s on small workspaces).
"""
import sys
import csv
import json
import bisect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
import re

from pod_ready_lookup import (
    load_pod_ready_events,
    find_pod_ready_for_decision,
)

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
DECISIONS_CSV = RESULTS_DIR / "decisions_with_ses.csv"
RUN_INDEX_CSV = RESULTS_DIR / "run_index.csv"

# Same windows as compute_ses.py — kept in sync manually.
BEFORE_WINDOW_LOOKBACK = 60   # window starts this many seconds before T_decision
BEFORE_WINDOW_EXCLUSION = 1   # window ends this many seconds before T_decision
AFTER_WINDOW_DURATION = 60    # window duration starting from T_pod_Ready


def parse_iso(s):
    s = s.replace('Z', '+00:00')
    m = re.match(r'^(.+?\.)(\d+)(.+)$', s)
    if m:
        micros = m.group(2)[:6].ljust(6, '0')
        s = m.group(1) + micros + m.group(3)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_k6_latencies(k6_path):
    """Load (timestamp, latency_ms) tuples for successful http_req_duration points."""
    points = []
    pattern = re.compile(
        r'"time":"([^"]+)".*?"value":([0-9.eE+\-]+).*?"expected_response":"true"'
    )
    with open(k6_path) as f:
        for line in f:
            if 'http_req_duration' not in line:
                continue
            m = pattern.search(line)
            if not m:
                continue
            try:
                ts = parse_iso(m.group(1))
                points.append((ts, float(m.group(2))))
            except (ValueError, TypeError):
                continue
    points.sort(key=lambda p: p[0])
    return points


def main():
    filter_pattern = sys.argv[1] if len(sys.argv) > 1 else 'all'
    print(f"Filter: {filter_pattern}")

    # Load run index
    run_index = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            if filter_pattern != 'all' and r['pattern'] != filter_pattern:
                continue
            run_index[r['run_label']] = r['file_path']

    # Load decisions, group by run
    decisions_by_run = defaultdict(list)
    with open(DECISIONS_CSV) as f:
        for r in csv.DictReader(f):
            if not r['run_label'] or r['run_label'] == 'between_runs':
                continue
            if filter_pattern != 'all' and r['pattern'] != filter_pattern:
                continue
            r['_ts'] = parse_iso(r['timestamp_utc'])
            decisions_by_run[r['run_label']].append(r)

    # Load pod_ready events so the after-window anchor matches compute_ses.py
    print("Loading pod_ready events...")
    pod_ready_events = load_pod_ready_events(RESULTS_DIR)
    print(f"  Loaded {len(pod_ready_events)} pod_ready events")

    # Output file — per pattern (or 'all')
    output_file = RESULTS_DIR / (
        f"ses_input_dataset_{filter_pattern}.csv" if filter_pattern != 'all'
        else "ses_input_dataset.csv"
    )

    n_rows = 0
    with open(output_file, 'w', newline='') as out:
        w = csv.writer(out)
        w.writerow([
            'decision_id', 'pattern', 'run_label', 'direction',
            'decision_timestamp_utc', 'window',
            'window_anchor_utc', 't_after_source',
            'request_timestamp_utc', 'request_seconds_relative_to_anchor',
            'latency_ms',
        ])

        for i, (run_label, decisions) in enumerate(sorted(decisions_by_run.items()), 1):
            file_path = run_index.get(run_label)
            if not file_path:
                continue
            k6_file = RESULTS_DIR / file_path
            if not k6_file.exists():
                print(f"  [{i}/{len(decisions_by_run)}] skip {run_label}: file missing")
                continue

            points = load_k6_latencies(k6_file)
            timestamps = [p[0] for p in points]
            if not points:
                continue

            for d in decisions:
                t = d['_ts']
                # Before window — anchored at T_decision, matches compute_ses.py
                bstart = t - timedelta(seconds=BEFORE_WINDOW_LOOKBACK)
                bend = t - timedelta(seconds=BEFORE_WINDOW_EXCLUSION)
                # After window — anchored at T_pod_Ready (or documented fallback)
                t_after_anchor, t_after_source, _ = find_pod_ready_for_decision(
                    decision_ts=t,
                    direction=d['direction'],
                    pod_ready_events=pod_ready_events,
                )
                astart = t_after_anchor
                aend = t_after_anchor + timedelta(seconds=AFTER_WINDOW_DURATION)

                for window, ws, we, anchor, src in [
                    ('before', bstart, bend, t, 'decision'),
                    ('after',  astart, aend, t_after_anchor, t_after_source),
                ]:
                    lo = bisect.bisect_left(timestamps, ws)
                    hi = bisect.bisect_right(timestamps, we)
                    for j in range(lo, hi):
                        rts, lat = points[j]
                        # Report each request's offset RELATIVE TO THE WINDOW
                        # ANCHOR, not always the decision. For 'before' this is
                        # relative to T_decision (same as before). For 'after'
                        # this is relative to T_pod_Ready, which is more
                        # interpretable than "relative to T_decision" when the
                        # cold-start gap varied across decisions.
                        relative = (rts - anchor).total_seconds()
                        w.writerow([
                            d['decision_id'], d['pattern'], d['run_label'], d['direction'],
                            t.isoformat(), window,
                            anchor.isoformat(), src,
                            rts.isoformat(), f"{relative:.3f}", f"{lat:.3f}",
                        ])
                        n_rows += 1
            print(f"  [{i}/{len(decisions_by_run)}] {run_label}: "
                  f"{len(decisions)} decisions processed (rows so far: {n_rows})", flush=True)

    print(f"\nWrote {n_rows} rows to {output_file.name}")
    print(f"\nColumns:")
    print(f"  decision_id                     — joins back to decisions_with_ses.csv")
    print(f"  pattern, run_label, direction   — context")
    print(f"  decision_timestamp_utc          — when HPA fired")
    print(f"  window                          — 'before' or 'after'")
    print(f"  window_anchor_utc               — T_decision for 'before', T_pod_Ready (or fallback) for 'after'")
    print(f"  t_after_source                  — how the after anchor was chosen: pod_ready | fallback | scale_down | decision")
    print(f"  request_timestamp_utc           — when this request was made")
    print(f"  request_seconds_relative_to_anchor  — signed offset from the window anchor")
    print(f"  latency_ms                      — this request's http_req_duration")


if __name__ == '__main__':
    main()
