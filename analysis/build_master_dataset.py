#!/usr/bin/env python3
"""
build_master_dataset.py — Parse the watcher JSONL and tag each HPA decision
with the run it belongs to, producing a master CSV ready for classification.

Strategy:
  1. Load all events from hpa-events-full.jsonl
  2. Filter out pre-campaign events (target=50%) and watcher_started events
  3. Load all k6 result files, extract their time windows
  4. For each HPA decision, find the matching run by timestamp containment
  5. Output: results/master_decisions.csv

Inputs:
  - results/hpa-events-full.jsonl    (the watcher's complete event log)
  - results/{pattern}-run-*.json     (k6 results — used for time windows)

Outputs:
  - results/master_decisions.csv     (one row per HPA decision, tagged)
  - results/run_index.csv            (one row per run, with metadata)
"""
import json
import os
import re
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

# ============================================================================
# CONFIG
# ============================================================================
ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
JSONL_PATH = RESULTS_DIR / "hpa-events-full.jsonl"
MASTER_CSV = RESULTS_DIR / "master_decisions.csv"
RUN_INDEX_CSV = RESULTS_DIR / "run_index.csv"
EXCLUDED_DIR = RESULTS_DIR / "excluded"

# Campaign window — anything before this is pre-campaign setup
CAMPAIGN_START_UTC = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
# Target value during the actual campaign (filter)
CAMPAIGN_TARGET_PCT = "30%"

# ============================================================================
# UTILITIES
# ============================================================================

def parse_iso(s):
    """
    Parse ISO 8601 timestamps, handling all the variants in our data:
      - Watcher: '2026-06-01T12:25:30.123Z' (UTC)
      - k6:      '2026-06-01T12:25:04.19544+05:30' (Sri Lanka time, non-standard microseconds)
      - Either:  '2026-06-01T12:25:04Z' (no microseconds)
    Always returns datetime in UTC.
    """
    s = s.replace('Z', '+00:00')
    # Normalize microseconds to exactly 6 digits (Python's fromisoformat requirement on <3.11)
    m = re.match(r'^(.+?\.)(\d+)(.+)$', s)
    if m:
        prefix, micros, suffix = m.group(1), m.group(2), m.group(3)
        micros = micros[:6].ljust(6, '0')  # truncate-or-pad to 6 digits
        s = prefix + micros + suffix
    dt = datetime.fromisoformat(s)
    return dt.astimezone(timezone.utc)

def extract_trigger_pct(trigger_value):
    """From '69% (target 30%)' → (69, '30%')."""
    m = re.match(r'(\d+)%\s*\(target\s*(\d+%)\)', trigger_value or '')
    if m:
        return int(m.group(1)), m.group(2)
    return None, None

# ============================================================================
# STEP 1: LOAD WATCHER EVENTS
# ============================================================================

def load_decisions():
    """Load all hpa_decision events, filtered to campaign window."""
    decisions = []
    skipped_target = 0
    skipped_early = 0

    with open(JSONL_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            if d.get('event_type') != 'hpa_decision':
                continue

            ts = parse_iso(d['detected_at'])
            if ts < CAMPAIGN_START_UTC:
                skipped_early += 1
                continue

            current_pct, target = extract_trigger_pct(d.get('trigger_value', ''))
            if target != CAMPAIGN_TARGET_PCT:
                skipped_target += 1
                continue

            decisions.append({
                'timestamp_utc': ts,
                'direction': d.get('direction'),
                'replicas_before': d['replicas']['before'],
                'replicas_after': d['replicas']['after'],
                'current_at_detection': d['replicas'].get('current_at_detection'),
                'trigger_metric': d.get('trigger_metric'),
                'trigger_value_raw': d.get('trigger_value'),
                'current_pct': current_pct,
                'target_pct': target,
                'min_replicas': d.get('min_replicas'),
                'max_replicas': d.get('max_replicas'),
                'scaling_limited': None,
                'scaling_limit_reason': None,
            })

            # Extract ScalingLimited condition for whether HPA hit a limit
            for cond in d.get('hpa_conditions', []):
                if cond.get('type') == 'ScalingLimited':
                    decisions[-1]['scaling_limited'] = cond.get('status')
                    decisions[-1]['scaling_limit_reason'] = cond.get('reason')

    print(f"  Loaded {len(decisions)} campaign decisions")
    print(f"  Skipped {skipped_early} pre-campaign events (before June 1)")
    print(f"  Skipped {skipped_target} non-campaign target events (target != 30%)")
    return decisions

# ============================================================================
# STEP 2: BUILD RUN INDEX FROM K6 FILES
# ============================================================================

def build_run_index():
    """
    Scan k6 result files in results/ (NOT excluded/), extract start/end times
    from the JSON stream, and produce a list of (pattern, run_label, start, end).
    """
    runs = []
    patterns = ['step', 'burst', 'ramp', 'noisy']

    # We'll number runs per pattern by file timestamp order.
    # Each k6 JSON has metric data with timestamps. The earliest is start, latest is end.
    for pattern in patterns:
        files = sorted(RESULTS_DIR.glob(f"{pattern}-run-*.json"))
        # Exclude any in /excluded subdir
        files = [f for f in files if EXCLUDED_DIR not in f.parents]

        for i, fpath in enumerate(files, start=1):
            start_ts, end_ts = extract_k6_window(fpath)
            if start_ts is None:
                print(f"  WARNING: could not extract time window from {fpath.name}")
                continue

            run_label = f"{pattern}-{i:02d}"
            runs.append({
                'run_label': run_label,
                'pattern': pattern,
                'run_num': i,
                'file_path': fpath.name,
                'start_utc': start_ts,
                'end_utc': end_ts,
                # Capture window: start of k6 to end + 15 min (HPA scale-down period)
                'capture_window_end_utc': end_ts + timedelta(minutes=15),
            })

    runs.sort(key=lambda r: r['start_utc'])
    print(f"  Built index of {len(runs)} runs")
    return runs

def _scan_for_timestamp(chunk, reverse=False):
    """From a text chunk, find the first (or last) k6 time field."""
    lines = chunk.split('\n')
    if reverse:
        lines = reversed(lines)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        data = obj.get('data')
        if isinstance(data, dict):
            t = data.get('time')
            if t:
                try:
                    return parse_iso(t)
                except ValueError:
                    continue
    return None

def extract_k6_window(fpath):
    """
    Efficient timestamp extraction from very large k6 JSONL files.
    Reads only the first ~8 KB (for start) and last ~16 KB (for end) of the file
    rather than streaming the whole thing — gives a 1000x speedup for 24 MB files.
    """
    try:
        with open(fpath, 'rb') as f:
            # First chunk → find start timestamp
            head_chunk = f.read(8192).decode('utf-8', errors='ignore')
            first_ts = _scan_for_timestamp(head_chunk, reverse=False)

            # Last chunk → find end timestamp
            f.seek(0, 2)  # seek to end
            size = f.tell()
            tail_size = min(16384, size)
            f.seek(size - tail_size)
            tail_chunk = f.read().decode('utf-8', errors='ignore')
            last_ts = _scan_for_timestamp(tail_chunk, reverse=True)
    except OSError as e:
        print(f"  ERROR reading {fpath}: {e}")
        return None, None
    return first_ts, last_ts

# ============================================================================
# STEP 3: MATCH DECISIONS TO RUNS
# ============================================================================

def tag_decisions(decisions, runs):
    """
    For each decision, find the run whose [start, capture_window_end] contains it.
    Decisions outside any run's window are tagged 'between_runs'.
    """
    tagged = 0
    untagged = 0
    for d in decisions:
        ts = d['timestamp_utc']
        match = None
        for r in runs:
            if r['start_utc'] <= ts <= r['capture_window_end_utc']:
                match = r
                break
        if match:
            d['run_label'] = match['run_label']
            d['pattern'] = match['pattern']
            d['run_num'] = match['run_num']
            tagged += 1
        else:
            d['run_label'] = 'between_runs'
            d['pattern'] = None
            d['run_num'] = None
            untagged += 1
    print(f"  Tagged {tagged} decisions to specific runs")
    print(f"  {untagged} decisions fell between runs (idle-period events)")
    return decisions

# ============================================================================
# STEP 4: WRITE OUTPUTS
# ============================================================================

def write_master_csv(decisions):
    fields = [
        'decision_id', 'timestamp_utc', 'pattern', 'run_label', 'run_num',
        'direction', 'replicas_before', 'replicas_after', 'current_at_detection',
        'trigger_metric', 'current_pct', 'target_pct',
        'min_replicas', 'max_replicas',
        'scaling_limited', 'scaling_limit_reason',
        'trigger_value_raw',
    ]
    with open(MASTER_CSV, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, d in enumerate(decisions, start=1):
            row = {k: d.get(k) for k in fields}
            row['decision_id'] = i
            row['timestamp_utc'] = d['timestamp_utc'].isoformat()
            w.writerow(row)
    print(f"  Wrote {len(decisions)} rows to {MASTER_CSV.name}")

def write_run_index(runs):
    fields = ['run_label', 'pattern', 'run_num', 'file_path', 'start_utc', 'end_utc']
    with open(RUN_INDEX_CSV, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in runs:
            row = {k: r[k] for k in fields}
            row['start_utc'] = r['start_utc'].isoformat()
            row['end_utc'] = r['end_utc'].isoformat()
            w.writerow(row)
    print(f"  Wrote {len(runs)} rows to {RUN_INDEX_CSV.name}")

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("Step 1: Loading watcher decisions from JSONL...")
    decisions = load_decisions()

    print("\nStep 2: Building run index from k6 result files...")
    runs = build_run_index()

    print("\nStep 3: Tagging decisions to runs...")
    decisions = tag_decisions(decisions, runs)

    print("\nStep 4: Writing output CSVs...")
    write_master_csv(decisions)
    write_run_index(runs)

    print("\nDone.")
    print(f"\nSummary:")
    print(f"  - Total campaign decisions: {len(decisions)}")
    print(f"  - Decisions tagged to runs: {sum(1 for d in decisions if d['pattern'])}")
    print(f"  - Decisions between runs: {sum(1 for d in decisions if not d['pattern'])}")
    print(f"  - Runs indexed: {len(runs)}")

    # Distribution by pattern
    print(f"\nDecisions per pattern:")
    by_pattern = defaultdict(int)
    for d in decisions:
        if d['pattern']:
            by_pattern[d['pattern']] += 1
    for p in ['step', 'burst', 'ramp', 'noisy']:
        print(f"  {p:6s}: {by_pattern[p]}")

if __name__ == '__main__':
    main()
