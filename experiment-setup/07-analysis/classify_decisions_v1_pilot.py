#!/usr/bin/env python3
"""
classify_decisions_v1_pilot.py — LEGACY CPU-based classifier.

╔══════════════════════════════════════════════════════════════════════════╗
║  LEGACY — DO NOT USE FOR THE COUNTED CAMPAIGN OR THE RERUN.              ║
║                                                                          ║
║  Thresholds (30% target, 60% "late") are calibrated for the PILOT at    ║
║  30% HPA target on the earlier 2 vCPU droplet. On the 75% HPA target    ║
║  campaign, these rules do not discriminate: every scale-up would be     ║
║  auto-classified "Correct but Late" and every scale-down "Unnecessary". ║
║                                                                          ║
║  Use classify_decisions_v2.py (SRD-based) as the primary classifier    ║
║  for all counted-campaign and rerun data. This file is preserved for    ║
║  pilot-comparability reproduction — see Chapter 5 Threats to Validity.  ║
╚══════════════════════════════════════════════════════════════════════════╝

Applies a 4-bucket correctness classification to each HPA decision in
master_decisions.csv. Produces classified_decisions.csv plus summary
tables for the pilot's thesis chapter.

Buckets:
  - Correct & Timely
  - Correct but Late
  - Unnecessary
  - Ineffective

Classification Rules:
  ┌────────────────────────────────────────────────────────────────────┐
  │ DIRECTION: UP (scale-up)                                           │
  ├────────────────────────────────────────────────────────────────────┤
  │ scaling_limited=True + reason=TooManyReplicas → Ineffective        │
  │ current_pct < 30% (target)                    → Unnecessary        │
  │ 30% <= current_pct <= 60% (target ≤ x ≤ 2x)   → Correct & Timely   │
  │ current_pct > 60%                             → Correct but Late   │
  ├────────────────────────────────────────────────────────────────────┤
  │ DIRECTION: DOWN (scale-down)                                       │
  ├────────────────────────────────────────────────────────────────────┤
  │ scaling_limited=True + reason=TooFewReplicas  → Ineffective        │
  │ current_pct > 30% (still above target)        → Unnecessary        │
  │ current_pct <= 30%                            → Correct & Timely   │
  └────────────────────────────────────────────────────────────────────┘

Note: ScaleUpLimit (HPA-internal max scale rate) is NOT treated as
"Ineffective" — it just means the HPA scaled up by more than one step
in a single decision. That's normal aggressive scale-up behavior.
"""
import csv
import sys
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
INPUT_CSV = RESULTS_DIR / "master_decisions.csv"
OUTPUT_CSV = RESULTS_DIR / "classified_decisions.csv"
SUMMARY_CSV = RESULTS_DIR / "classification_summary.csv"

# Thresholds (in percentage points)
TARGET_PCT = 30   # HPA target CPU utilization
LATE_PCT = 60     # CPU above this on scale-up → "Correct but Late"

def classify(row):
    """Returns (bucket, reason_code, reason_text) for a single decision row."""
    direction = row['direction']
    try:
        current = int(row['current_pct']) if row['current_pct'] else None
    except (ValueError, TypeError):
        current = None

    scaling_limited = (row.get('scaling_limited') == 'True')
    limit_reason = row.get('scaling_limit_reason', '')

    if direction == 'up':
        # Rule 1: Ineffective — HPA wanted more pods but capped
        if scaling_limited and limit_reason == 'TooManyReplicas':
            return ('Ineffective', 'UP_INEFFECTIVE_MAX',
                    'Scale-up blocked by maxReplicas cap')
        # Rule 2: Unnecessary — CPU was below target
        if current is not None and current < TARGET_PCT:
            return ('Unnecessary', 'UP_UNNECESSARY',
                    f'Scaled up at CPU={current}% below target={TARGET_PCT}%')
        # Rule 3: Correct but Late — CPU significantly above target
        if current is not None and current > LATE_PCT:
            return ('Correct but Late', 'UP_LATE',
                    f'Scaled up at CPU={current}% > {LATE_PCT}% (2x target)')
        # Rule 4: Correct & Timely — CPU just above target
        if current is not None and TARGET_PCT <= current <= LATE_PCT:
            return ('Correct & Timely', 'UP_TIMELY',
                    f'Scaled up at CPU={current}% (within target..2x target range)')
        # Edge case: no CPU value
        return ('Correct & Timely', 'UP_DEFAULT',
                'Scale-up with no parseable CPU value (defaulted timely)')

    elif direction == 'down':
        # Rule 1: Ineffective — wanted to scale below min
        if scaling_limited and limit_reason == 'TooFewReplicas':
            return ('Ineffective', 'DOWN_INEFFECTIVE_MIN',
                    'Scale-down blocked by minReplicas floor')
        # Rule 2: Unnecessary — CPU still above target
        if current is not None and current > TARGET_PCT:
            return ('Unnecessary', 'DOWN_UNNECESSARY',
                    f'Scaled down at CPU={current}% still above target={TARGET_PCT}%')
        # Rule 3: Correct & Timely — CPU below target
        if current is not None and current <= TARGET_PCT:
            return ('Correct & Timely', 'DOWN_TIMELY',
                    f'Scaled down at CPU={current}% below target={TARGET_PCT}%')
        # Edge case: no CPU value
        return ('Correct & Timely', 'DOWN_DEFAULT',
                'Scale-down with no parseable CPU value (defaulted timely)')

    return ('Unknown', 'UNKNOWN_DIRECTION', f'Direction={direction}')


def main():
    # Read master CSV
    with open(INPUT_CSV) as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} decisions from {INPUT_CSV.name}")

    # Classify each row
    for row in rows:
        bucket, reason_code, reason_text = classify(row)
        row['bucket'] = bucket
        row['reason_code'] = reason_code
        row['reason_text'] = reason_text

    # Write classified output
    fieldnames = list(rows[0].keys())
    with open(OUTPUT_CSV, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} classified rows to {OUTPUT_CSV.name}")

    # Build per-pattern × bucket summary
    summary = defaultdict(lambda: Counter())
    untagged_by_bucket = Counter()
    for row in rows:
        pattern = row['pattern'] or 'between_runs'
        bucket = row['bucket']
        if row['pattern']:
            summary[pattern][bucket] += 1
        else:
            untagged_by_bucket[bucket] += 1

    # Print formatted distribution
    buckets = ['Correct & Timely', 'Correct but Late', 'Unnecessary', 'Ineffective']
    print(f"\n{'='*78}")
    print(f"4-BUCKET CLASSIFICATION DISTRIBUTION")
    print(f"{'='*78}")
    print(f"\n{'Pattern':<10} | {'C&T':>6} | {'C-Late':>6} | {'Unnec':>6} | {'Ineff':>6} | {'Total':>6}")
    print('-' * 60)
    pattern_totals = {}
    for pattern in ['step', 'burst', 'ramp', 'noisy']:
        counts = summary.get(pattern, Counter())
        total = sum(counts.values())
        pattern_totals[pattern] = total
        print(f"{pattern:<10} | "
              f"{counts['Correct & Timely']:>6} | "
              f"{counts['Correct but Late']:>6} | "
              f"{counts['Unnecessary']:>6} | "
              f"{counts['Ineffective']:>6} | "
              f"{total:>6}")
    print('-' * 60)
    # Totals row
    grand = Counter()
    for p in summary.values():
        grand.update(p)
    print(f"{'TOTAL':<10} | "
          f"{grand['Correct & Timely']:>6} | "
          f"{grand['Correct but Late']:>6} | "
          f"{grand['Unnecessary']:>6} | "
          f"{grand['Ineffective']:>6} | "
          f"{sum(grand.values()):>6}")

    # Per-pattern percentages
    print(f"\n{'='*78}")
    print(f"DISTRIBUTION AS PERCENTAGES (per pattern)")
    print(f"{'='*78}")
    print(f"\n{'Pattern':<10} | {'C&T':>7} | {'C-Late':>7} | {'Unnec':>7} | {'Ineff':>7}")
    print('-' * 60)
    for pattern in ['step', 'burst', 'ramp', 'noisy']:
        counts = summary.get(pattern, Counter())
        total = sum(counts.values()) or 1
        print(f"{pattern:<10} | "
              f"{100*counts['Correct & Timely']/total:>6.1f}% | "
              f"{100*counts['Correct but Late']/total:>6.1f}% | "
              f"{100*counts['Unnecessary']/total:>6.1f}% | "
              f"{100*counts['Ineffective']/total:>6.1f}%")

    # Direction split for context
    print(f"\n{'='*78}")
    print(f"CLASSIFICATION BY DIRECTION (Pattern × Direction × Bucket)")
    print(f"{'='*78}")
    dir_summary = defaultdict(lambda: Counter())
    for row in rows:
        if not row['pattern']:
            continue
        key = (row['pattern'], row['direction'])
        dir_summary[key][row['bucket']] += 1

    print(f"\n{'Pattern':<8} {'Dir':<6} | {'C&T':>6} | {'C-Late':>7} | {'Unnec':>6} | {'Ineff':>6} | {'Total':>6}")
    print('-' * 70)
    for pattern in ['step', 'burst', 'ramp', 'noisy']:
        for direction in ['up', 'down']:
            counts = dir_summary.get((pattern, direction), Counter())
            total = sum(counts.values())
            print(f"{pattern:<8} {direction:<6} | "
                  f"{counts['Correct & Timely']:>6} | "
                  f"{counts['Correct but Late']:>7} | "
                  f"{counts['Unnecessary']:>6} | "
                  f"{counts['Ineffective']:>6} | "
                  f"{total:>6}")

    # Write machine-readable summary
    with open(SUMMARY_CSV, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['pattern', 'direction', 'bucket', 'count'])
        for (pattern, direction), counts in sorted(dir_summary.items()):
            for bucket in buckets:
                w.writerow([pattern, direction, bucket, counts.get(bucket, 0)])
    print(f"\nWrote per-pattern × direction × bucket counts to {SUMMARY_CSV.name}")

    # Untagged stats
    if untagged_by_bucket:
        print(f"\n{'='*78}")
        print(f"DECISIONS BETWEEN RUNS (not tagged to a specific run)")
        print(f"{'='*78}")
        for bucket, count in untagged_by_bucket.most_common():
            print(f"  {bucket}: {count}")
        print(f"  (These are HPA decisions during idle periods between runs)")

if __name__ == '__main__':
    main()
