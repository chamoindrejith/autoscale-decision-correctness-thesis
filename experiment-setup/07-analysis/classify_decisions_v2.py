#!/usr/bin/env python3
"""
classify_decisions_v2.py — SRD-based reclassification of HPA decisions.

Produces a parallel bucket assignment that ties "Late" to the actual SLO
breach (p95 > 500 ms sustained 30 s) rather than to a CPU proxy. This is
the classification approach the methodology doc (§2 + §5) points to in
principle, and the approach reviewer guidance leaned toward when the
CPU-based bucket-boundary problem was raised at the 75 % HPA target.

Rules
-----
Scale-UP decisions
  scaling_limited=True AND reason=TooManyReplicas   → Ineffective
  srd_source = "no_slo_breach"                       → Unnecessary
  srd_seconds ≤ 0 (SRD ≤ 0, pre_emptive or on-time) → Correct & Timely
  srd_seconds > 0 (SRD > 0, late reaction)           → Correct but Late

Scale-DOWN decisions  (SRD is not defined for scale-down; methodology §2)
  scaling_limited=True AND reason=TooFewReplicas    → Ineffective
  otherwise                                          → Correct & Timely
  (rationale: scale-down happens after HPA's own stabilisation window;
   a premature scale-down would surface as a subsequent scale-up with
   SRD > 0)

What changes vs classify_decisions.py
-------------------------------------
- Original file's rules used the CPU utilisation at decision time to
  decide "Late" vs "Timely" vs "Unnecessary". With the campaign's 75 %
  HPA target and requests==limits (CPU capped ~100 %), the original
  target=30/late>60 thresholds compressed every scale-up to "Correct
  but Late" and every scale-down to "Unnecessary". See discussion in
  campaign_log.md notes-for-thesis-writeup.
- This v2 file keeps the ORIGINAL bucket column untouched (in-CSV) and
  writes NEW columns with the SRD-based verdict alongside so the two
  can be compared in the thesis's Threats-to-Validity and Results
  sections.

Pipeline position
-----------------
Runs AFTER compute_srd.py because it needs the srd_seconds and
srd_source columns. Recommended order:

  build_master_dataset.py    → master_decisions.csv
  classify_decisions.py      → classified_decisions.csv      (CPU-based bucket)
  compute_srd.py             → decisions_with_srd.csv        (adds SRD)
  classify_decisions_v2.py   → decisions_with_srd_v2.csv     ← THIS FILE
  compute_ses.py             → decisions_with_ses.csv        (adds SES)

Or, if you have already produced decisions_with_ses.csv, this file will
read from it directly and update it in place — see IO section below.

Reads (auto-detects which one exists)
  - results/decisions_with_ses.csv                (preferred)
  - results/decisions_with_srd.csv                (fallback)

Writes
  - Same file, with three new columns appended:
      bucket_srd         Correct & Timely | Correct but Late | Unnecessary | Ineffective
      reason_code_srd    e.g. UP_LATE_SRD, UP_TIMELY_SRD, UP_UNNECESSARY_NOBREACH,
                              UP_INEFFECTIVE_MAX, DOWN_TIMELY_SRD,
                              DOWN_INEFFECTIVE_MIN
      reason_text_srd    human-readable one-line rationale
  - results/classification_summary_srd.csv        (pattern × bucket_srd counts)
"""
from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"

# Prefer the fullest CSV — decisions_with_ses.csv already has bucket, SRD,
# SES, and pod-ready metadata. Fall back to decisions_with_srd.csv if SES
# has not been computed yet.
_CANDIDATE_INPUTS = [
    RESULTS_DIR / "decisions_with_ses.csv",
    RESULTS_DIR / "decisions_with_srd.csv",
]
INPUT_CSV = next((p for p in _CANDIDATE_INPUTS if p.exists()),
                 RESULTS_DIR / "decisions_with_ses.csv")
SUMMARY_CSV = RESULTS_DIR / "classification_summary_srd.csv"


BUCKETS = [
    "Correct & Timely",
    "Correct but Late",
    "Unnecessary",
    "Ineffective",
]


# ============================================================================
# Classification
# ============================================================================

def classify_srd(row: dict) -> tuple[str, str, str]:
    """Return (bucket_srd, reason_code, reason_text) for one decision row.

    Applies the rules described in the module docstring.
    """
    direction = (row.get("direction") or "").lower()
    scaling_limited = (str(row.get("scaling_limited", "")).strip() == "True")
    limit_reason = (row.get("scaling_limit_reason") or "").strip()

    # -----------------------------------------------------------------
    # Rule 1: Ineffective — HPA hit a hard cap
    # -----------------------------------------------------------------
    if scaling_limited:
        if direction == "up" and limit_reason == "TooManyReplicas":
            return (
                "Ineffective",
                "UP_INEFFECTIVE_MAX",
                "Scale-up blocked by maxReplicas cap",
            )
        if direction == "down" and limit_reason == "TooFewReplicas":
            return (
                "Ineffective",
                "DOWN_INEFFECTIVE_MIN",
                "Scale-down blocked by minReplicas floor",
            )

    # -----------------------------------------------------------------
    # Rule 2: Scale-DOWN — SRD is undefined; default to Correct & Timely
    # -----------------------------------------------------------------
    if direction == "down":
        return (
            "Correct & Timely",
            "DOWN_TIMELY_SRD",
            "Scale-down after HPA's stabilisation window "
            "(SRD not defined for scale-down)",
        )

    # -----------------------------------------------------------------
    # Rule 3: Scale-UP — dispatch on SRD outcome
    # -----------------------------------------------------------------
    src = (row.get("srd_source") or "").strip()

    if src == "no_slo_breach":
        return (
            "Unnecessary",
            "UP_UNNECESSARY_NOBREACH",
            "Scaled up but p95 latency never breached the 500 ms SLO in "
            "this run — HPA reacted to CPU/memory before latency risk "
            "materialised",
        )

    if src in ("late", "pre_emptive"):
        try:
            srd_seconds = float(row.get("srd_seconds") or "nan")
        except ValueError:
            srd_seconds = float("nan")

        if srd_seconds != srd_seconds:  # NaN — treat as Unnecessary
            return (
                "Unnecessary",
                "UP_UNNECESSARY_NANSRD",
                "Scaled up but SRD could not be computed (missing latency "
                "window data)",
            )

        if srd_seconds <= 0:
            return (
                "Correct & Timely",
                "UP_TIMELY_SRD",
                f"Scaled up SRD = {srd_seconds:.1f} s "
                f"(≤ 0 → pre-emptive or on-time relative to SLO breach)",
            )
        # SRD > 0
        return (
            "Correct but Late",
            "UP_LATE_SRD",
            f"Scaled up SRD = {srd_seconds:.1f} s "
            f"(> 0 → HPA reacted after p95 latency crossed the 500 ms SLO)",
        )

    # -----------------------------------------------------------------
    # Fallback — should not reach here in practice
    # -----------------------------------------------------------------
    return (
        "Unnecessary",
        "UP_FALLBACK",
        f"Unhandled srd_source={src!r}; classified defensively as Unnecessary",
    )


# ============================================================================
# CSV I/O — read INPUT_CSV, add three columns, write back in place
# ============================================================================

def main() -> None:
    if not INPUT_CSV.exists():
        print(f"ERROR: input CSV not found: {INPUT_CSV}", file=sys.stderr)
        print("       Run compute_srd.py (and preferably compute_ses.py) first.",
              file=sys.stderr)
        sys.exit(1)

    print(f"Reading {INPUT_CSV.name}...")

    with open(INPUT_CSV) as f:
        reader = csv.DictReader(f)
        original_fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    print(f"  Loaded {len(rows)} decisions")

    # Compute the SRD-based bucket / reason / text for every row.
    for r in rows:
        bucket_srd, reason_code_srd, reason_text_srd = classify_srd(r)
        r["bucket_srd"] = bucket_srd
        r["reason_code_srd"] = reason_code_srd
        r["reason_text_srd"] = reason_text_srd

    # Fieldnames: preserve original ordering, then append new columns if
    # they weren't already present (idempotent — safe to re-run).
    new_columns = ["bucket_srd", "reason_code_srd", "reason_text_srd"]
    fieldnames = list(original_fieldnames)
    for col in new_columns:
        if col not in fieldnames:
            fieldnames.append(col)

    # Write back in place.
    with open(INPUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"  Rewrote {INPUT_CSV.name} with columns: "
          f"{', '.join(new_columns)}")

    # ------------------------------------------------------------------
    # Per-pattern × bucket_srd summary
    # ------------------------------------------------------------------
    print()
    print("=" * 78)
    print("SRD-BASED CLASSIFICATION SUMMARY")
    print("=" * 78)

    pattern_counts: dict[str, Counter] = defaultdict(Counter)
    total_counts: Counter = Counter()
    for r in rows:
        pat = (r.get("pattern") or "").strip() or "(untagged)"
        bucket = r["bucket_srd"]
        pattern_counts[pat][bucket] += 1
        total_counts[bucket] += 1

    header = f"{'Pattern':<12}" + "".join(f"{b:>18}" for b in BUCKETS) + f"{'TOTAL':>8}"
    print(header)
    print("-" * len(header))

    summary_rows: list[dict] = []
    for pat in sorted(pattern_counts):
        row_counts = pattern_counts[pat]
        row_total = sum(row_counts.values())
        line = f"{pat:<12}"
        for b in BUCKETS:
            line += f"{row_counts[b]:>18}"
        line += f"{row_total:>8}"
        print(line)
        for b in BUCKETS:
            summary_rows.append({
                "pattern": pat,
                "bucket_srd": b,
                "count": row_counts[b],
            })

    # Grand total
    print("-" * len(header))
    line = f"{'ALL':<12}"
    for b in BUCKETS:
        line += f"{total_counts[b]:>18}"
    line += f"{sum(total_counts.values()):>8}"
    print(line)

    # ------------------------------------------------------------------
    # Comparison against original CPU-based bucket, if that column exists
    # ------------------------------------------------------------------
    if "bucket" in original_fieldnames:
        print()
        print("=" * 78)
        print("AGREEMENT WITH ORIGINAL CPU-BASED CLASSIFICATION")
        print("=" * 78)
        agree = sum(1 for r in rows if r.get("bucket") == r["bucket_srd"])
        total = len(rows)
        pct = 100.0 * agree / total if total else 0.0
        print(f"  Rows where bucket_srd matches original bucket: {agree}/{total} "
              f"({pct:.1f}%)")
        disagreements: Counter = Counter()
        for r in rows:
            if r.get("bucket") != r["bucket_srd"]:
                disagreements[(r.get("bucket"), r["bucket_srd"])] += 1
        if disagreements:
            print(f"  Top disagreements (CPU → SRD):")
            for (old, new), n in disagreements.most_common(6):
                print(f"    {str(old):<20} → {str(new):<20}  {n}")

    # ------------------------------------------------------------------
    # Write per-pattern summary CSV
    # ------------------------------------------------------------------
    with open(SUMMARY_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pattern", "bucket_srd", "count"])
        w.writeheader()
        w.writerows(summary_rows)
    print()
    print(f"Wrote per-pattern summary → {SUMMARY_CSV.name}")


if __name__ == "__main__":
    main()
