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
import argparse
import json
import os
import re
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

# ============================================================================
# CONFIG
# ============================================================================
# ROOT is the `experiment-setup/` directory. RESULTS_DIR resolves to
# `experiment-setup/results/`. If your results/ are at the repo root, create
# a symlink:
#   ln -s ../results experiment-setup/results
ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"

# The watcher log is captured at multiple checkpoints across the campaign
# (post-step, post-burst, post-ramp, post-noisy). Prefer the latest (most
# complete) snapshot; fall back to the earlier or the unsuffixed name.
_CANDIDATES = [
    RESULTS_DIR / "hpa-events-full-post-noisy.jsonl",
    RESULTS_DIR / "hpa-events-full-post-ramp.jsonl",
    RESULTS_DIR / "hpa-events-full-post-burst.jsonl",
    RESULTS_DIR / "hpa-events-full-post-step.jsonl",
    RESULTS_DIR / "hpa-events-full.jsonl",
]
JSONL_PATH = next((p for p in _CANDIDATES if p.exists()),
                  RESULTS_DIR / "hpa-events-full.jsonl")

MASTER_CSV = RESULTS_DIR / "master_decisions.csv"
RUN_INDEX_CSV = RESULTS_DIR / "run_index.csv"
EXCLUDED_DIR = RESULTS_DIR / "excluded"

# Campaign window — filter out pre-campaign setup, JIT calibration, pilot
# activity, and anything before the counted campaign started. Provided as
# a CLI argument (--campaign-start=YYYY-MM-DDTHH:MM:SSZ) or an env var
# (CAMPAIGN_START_UTC=...); falls back to the earliest run_index start
# minus 6 hours if neither is set. Historic value for the July 2026
# counted campaign was 2026-07-10T06:30:00Z.
CAMPAIGN_START_UTC_DEFAULT = None   # resolved in main() from CLI/env/data

# Target value during the actual campaign (filter). The counted campaign
# uses 75% on both CPU and memory (previous proposal-era 30% target is
# what earlier versions of this file filtered on). Also settable via
# --campaign-target-pct or CAMPAIGN_TARGET_PCT env var.
CAMPAIGN_TARGET_PCT_DEFAULT = "75%"

# These become module-level after CLI parsing so downstream code doesn't
# have to plumb them through.
CAMPAIGN_START_UTC: datetime | None = None
CAMPAIGN_TARGET_PCT: str = CAMPAIGN_TARGET_PCT_DEFAULT

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
    """From '69% (target 75%)' → (69, '75%').

    Accepts fractional percentages ('72.5% (target 75%)' → (72.5, '75%'))
    since HPA metrics-server can occasionally emit them. Integer-valued
    percentages remain integers so downstream `int(current_pct)` calls
    still work.
    """
    m = re.match(r'(\d+(?:\.\d+)?)%\s*\(target\s*(\d+%)\)', trigger_value or '')
    if m:
        raw = m.group(1)
        current = int(raw) if '.' not in raw else float(raw)
        return current, m.group(2)
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
    print(f"  Skipped {skipped_early} pre-campaign events "
          f"(before {CAMPAIGN_START_UTC.isoformat()})")
    print(f"  Skipped {skipped_target} non-campaign target events "
          f"(target != {CAMPAIGN_TARGET_PCT})")
    return decisions

# ============================================================================
# STEP 2: BUILD RUN INDEX FROM K6 FILES
# ============================================================================

def _parse_run_num_from_filename(fpath, pattern):
    """
    Post-audit: new k6 filenames embed the run number:
        {pattern}-run-{NN}-{TS}.json  (NN is two-digit run_num)
    Old (pilot / counted-campaign) filenames omit the run number:
        {pattern}-run-{TS}.json
    Prefer the embedded run_num when present; fall back to None so the
    caller assigns by directory order (matches old behaviour).
    """
    m = re.match(rf'{pattern}-run-(\d{{1,4}})-\d{{8}}-\d{{6}}\.json$',
                 fpath.name)
    if m:
        return int(m.group(1))
    return None


def build_run_index():
    """
    Scan k6 result files in results/ (NOT excluded/), extract start/end times
    from the JSON stream, and produce a list of (pattern, run_label, start, end).

    Run numbering:
      * Prefers the run_num embedded in the k6 filename (new orchestrator
        format {pattern}-run-{NN}-{TS}.json)
      * Falls back to enumerate-by-chronological-sort for filenames that
        omit the embedded run_num (pilot and counted-campaign data)
    """
    runs = []
    patterns = ['step', 'burst', 'ramp', 'noisy']

    for pattern in patterns:
        files = sorted(RESULTS_DIR.glob(f"{pattern}-run-*.json"))
        # Exclude any in /excluded subdir
        files = [f for f in files if EXCLUDED_DIR not in f.parents]

        fallback_counter = 0
        for fpath in files:
            start_ts, end_ts = extract_k6_window(fpath)
            if start_ts is None:
                print(f"  WARNING: could not extract time window from {fpath.name}")
                continue

            embedded_num = _parse_run_num_from_filename(fpath, pattern)
            if embedded_num is not None:
                run_num = embedded_num
            else:
                fallback_counter += 1
                run_num = fallback_counter

            run_label = f"{pattern}-{run_num:02d}"
            runs.append({
                'run_label': run_label,
                'pattern': pattern,
                'run_num': run_num,
                'file_path': fpath.name,
                'start_utc': start_ts,
                'end_utc': end_ts,
                # Extended tail window used ONLY for scale-down decisions
                # that fire after k6 ends but before HPA's 5-minute
                # scale-down stabilisation completes. Scale-ups can NEVER
                # match against this — they must fall inside the strict
                # [start, end] k6 window. See tag_decisions() below.
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
    Direction-aware tagging (post-audit fix for the [start, end + 15 min]
    first-match bug):

      * Scale-UP decisions are tagged ONLY when the timestamp falls
        strictly inside a run's k6 [start_utc, end_utc] window. If it
        doesn't fall inside any run's k6 window, the decision is
        'between_runs' — never assigned to a run's post-k6 tail. This
        prevents a scale-up that fires during run N+1's load phase from
        being attributed to run N whose [end + 15 min] extended window
        also happens to contain the timestamp.

      * Scale-DOWN decisions can additionally use a run's extended
        [end_utc, end_utc + 15 min] tail, but ONLY if no run's strict
        window contains them. This covers the legitimate case where
        HPA's 5-minute scale-down stabilisation fires after k6 ends but
        before the next run starts.

    On the current campaign data this reproduces the same tags the
    original code produced (empirical check confirmed 0 of 99 extended-
    window tags would be reassigned). But it prevents future mistags if
    the between-run gap ever shrinks below the HPA scale-down window.
    """
    tagged = 0
    untagged = 0
    for d in decisions:
        ts = d['timestamp_utc']
        direction = (d.get('direction') or '').lower()

        # 1. Try strict k6-window match first — this is the only path
        #    that scale-ups are allowed to take, and the preferred path
        #    for scale-downs too.
        strict = next(
            (r for r in runs if r['start_utc'] <= ts <= r['end_utc']),
            None,
        )
        if strict:
            match = strict
        elif direction == 'down':
            # 2. Scale-downs only: fall back to a run's extended tail,
            #    but only if no run's strict window contains ts (already
            #    checked above), so no ambiguity.
            match = next(
                (r for r in runs
                 if r['end_utc'] < ts <= r['capture_window_end_utc']),
                None,
            )
        else:
            match = None

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

def resolve_campaign_start(cli_value, runs_hint=None):
    """Resolve CAMPAIGN_START_UTC from (in priority order):
    1. --campaign-start CLI argument (or CAMPAIGN_START_UTC env var)
    2. Fallback: earliest run_index start minus 6 hours
    """
    if cli_value:
        try:
            s = cli_value.replace('Z', '+00:00')
            return datetime.fromisoformat(s).astimezone(timezone.utc)
        except ValueError:
            print(f"ERROR: could not parse --campaign-start={cli_value!r}",
                  file=sys.stderr)
            sys.exit(2)
    env_value = os.environ.get('CAMPAIGN_START_UTC', '').strip()
    if env_value:
        try:
            s = env_value.replace('Z', '+00:00')
            return datetime.fromisoformat(s).astimezone(timezone.utc)
        except ValueError:
            print(f"ERROR: could not parse env CAMPAIGN_START_UTC={env_value!r}",
                  file=sys.stderr)
            sys.exit(2)
    if runs_hint:
        earliest = min(r['start_utc'] for r in runs_hint)
        fallback = earliest - timedelta(hours=6)
        print(f"  (no --campaign-start given; falling back to "
              f"earliest run start - 6h = {fallback.isoformat()})")
        return fallback
    # No hint available yet; pick a very old date so nothing is filtered.
    return datetime(2000, 1, 1, tzinfo=timezone.utc)


def main():
    global CAMPAIGN_START_UTC, CAMPAIGN_TARGET_PCT

    ap = argparse.ArgumentParser(description=__doc__.strip() if __doc__ else "")
    ap.add_argument('--campaign-start',
                    help="ISO-8601 UTC timestamp — decisions before this "
                         "are treated as pre-campaign noise and excluded. "
                         "Defaults to CAMPAIGN_START_UTC env var, then to "
                         "the earliest run_index start minus 6 hours.")
    ap.add_argument('--campaign-target-pct',
                    default=os.environ.get('CAMPAIGN_TARGET_PCT',
                                           CAMPAIGN_TARGET_PCT_DEFAULT),
                    help=f"HPA target percentage string used to filter "
                         f"non-campaign events (default: "
                         f"{CAMPAIGN_TARGET_PCT_DEFAULT}).")
    args = ap.parse_args()

    CAMPAIGN_TARGET_PCT = args.campaign_target_pct

    # Build the run index first — we need it to resolve the campaign
    # start if no CLI argument or env var is provided.
    print("Step 2: Building run index from k6 result files...")
    runs = build_run_index()

    CAMPAIGN_START_UTC = resolve_campaign_start(args.campaign_start, runs)
    print(f"  Using CAMPAIGN_START_UTC = {CAMPAIGN_START_UTC.isoformat()}")
    print(f"  Using CAMPAIGN_TARGET_PCT = {CAMPAIGN_TARGET_PCT}")

    print("\nStep 1: Loading watcher decisions from JSONL...")
    decisions = load_decisions()

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
