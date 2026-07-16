#!/usr/bin/env python3
"""
classify_decisions_v3.py — PRIMARY classifier (proposal-aligned).

As of the July 2026 audit this is the CANONICAL classifier for Chapter 4
results. The earlier SRD-only classifier is preserved as
classify_decisions_v2_srd_only.py for reference only. Do NOT run v2 as
part of the primary pipeline.

Implements the 2D SRD × SES classification matrix described in the
research proposal document (Section 9, "Decision Classification"):

    Classification         SRD                 SES
    ------------------- ------------------- --------------------
    Correct & Timely    Low  (SRD <= 0)     High (SES > +tau)
    Correct but Late    High (SRD >  0)     High (SES > +tau)
    Unnecessary         Low  or undefined   Near zero (|SES| <= tau)
                        or no_slo_breach
    Ineffective         Any                 Low (SES < -tau)

Where tau (SES_NEAR_ZERO_TAU) is the "near-zero" threshold for SES.
Default 0.05 — SES changes below 5% are treated as effectively no change.
Override via env var: SES_NEAR_ZERO_TAU=0.10 python3 classify_..._v3.py

Rules resolution (first-match-wins, in priority order):

  Scale-UP decisions:
    1. srd_source = "no_slo_breach"                   -> Unnecessary
    2. ses is null (missing before/after window data) -> (skipped; retains
                                                          v2 classification
                                                          in bucket_v3 as
                                                          "Undefined")
    3. ses < -tau                                      -> Ineffective
    4. |ses| <= tau (near zero)                        -> Unnecessary
    5. ses > +tau AND srd <= 0                         -> Correct & Timely
    6. ses > +tau AND srd >  0                         -> Correct but Late
    7. (fallback)                                       -> Undefined

  Scale-DOWN decisions:
    Follow v2 default: Correct & Timely (SES not applicable since a
    scale-down does not have a T_pod_Ready anchor).

Runs AFTER classify_decisions_v2.py so the input CSV has both srd_seconds
and ses columns. Writes THREE new columns to decisions_with_ses.csv
(idempotent — safe to re-run):

    bucket_v3          Proposal-rule bucket ("Correct & Timely" etc.)
    reason_code_v3     e.g. UP_TIMELY_V3, UP_LATE_V3, UP_UNNEC_NEARZERO_V3,
                       UP_INEFF_NEGSES_V3, UP_UNNEC_NOBREACH_V3
    reason_text_v3     Human-readable one-line rationale

Prints a v2 vs v3 agreement summary at the end so the reader can see
where the two classifiers diverge.

Reads:
  - results/decisions_with_ses.csv  (must have bucket_srd from v2)

Writes:
  - Same file, with three new columns appended
  - results/classification_summary_v3_proposal.csv (pattern x bucket_v3)
"""
from __future__ import annotations

import csv
import os as _os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
INPUT_CSV = RESULTS_DIR / "decisions_with_ses.csv"
SUMMARY_CSV = RESULTS_DIR / "classification_summary_v3_proposal.csv"

# "Near-zero" SES threshold. Values in [-tau, +tau] are treated as
# effectively no change. Override via env var.
SES_NEAR_ZERO_TAU = float(_os.environ.get("SES_NEAR_ZERO_TAU", "0.05"))

BUCKETS = [
    "Correct & Timely",
    "Correct but Late",
    "Unnecessary",
    "Ineffective",
    "Undefined",         # v3 only: SES missing so proposal rules can't decide
]


def safe_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def classify_v3(row):
    """Return (bucket_v3, reason_code_v3, reason_text_v3) for one row.

    Applies the proposal's SRD x SES rules with strict priority order.
    """
    direction = (row.get("direction") or "").lower()
    srd_source = (row.get("srd_source") or "").strip()
    srd = safe_float(row.get("srd_seconds"))
    ses = safe_float(row.get("ses"))

    # ---- Scale-DOWN: SES not applicable, follow v2 default ----
    if direction == "down":
        return (
            "Correct & Timely",
            "DOWN_TIMELY_V3",
            "Scale-down: SES not applicable (no new pod); "
            "defaults to Correct & Timely per proposal Section 9 note.",
        )

    # ---- Scale-UP rules ----

    # Rule 1: srd_source = no_slo_breach -> Unnecessary regardless of SES
    if srd_source == "no_slo_breach":
        return (
            "Unnecessary",
            "UP_UNNEC_NOBREACH_V3",
            "Scale-up but no sustained SLO breach ever occurred in "
            "this run; classified Unnecessary per proposal Section 9.",
        )

    # Rule 2: SES undefined -> Undefined (can't apply proposal rules)
    if ses is None:
        return (
            "Undefined",
            "UP_UNDEFINED_V3",
            "Scale-up but SES could not be computed (before or after "
            "window had insufficient samples); proposal rules require "
            "SES to classify.",
        )

    # Rule 3: SES < -tau -> Ineffective
    if ses < -SES_NEAR_ZERO_TAU:
        return (
            "Ineffective",
            "UP_INEFF_NEGSES_V3",
            f"Scale-up with SES = {ses:.3f} < -{SES_NEAR_ZERO_TAU} "
            f"(latency worsened after scaling); classified Ineffective "
            f"per proposal Section 9.",
        )

    # Rule 4: |SES| <= tau -> Unnecessary (near zero change)
    if abs(ses) <= SES_NEAR_ZERO_TAU:
        return (
            "Unnecessary",
            "UP_UNNEC_NEARZERO_V3",
            f"Scale-up with SES = {ses:.3f} within +/-{SES_NEAR_ZERO_TAU} "
            f"of zero (no meaningful latency change); classified "
            f"Unnecessary per proposal Section 9.",
        )

    # Rule 5+6: SES > +tau -> Correct & Timely if SRD <= 0, else Correct but Late
    if ses > SES_NEAR_ZERO_TAU:
        if srd is None:
            return (
                "Undefined",
                "UP_UNDEFINED_NOSRD_V3",
                f"Scale-up with SES = {ses:.3f} > +{SES_NEAR_ZERO_TAU} "
                f"but SRD undefined; cannot classify Timely vs Late.",
            )
        if srd <= 0:
            return (
                "Correct & Timely",
                "UP_TIMELY_V3",
                f"Scale-up: SRD = {srd:.1f} s <= 0 (pre-emptive or "
                f"on-time) AND SES = {ses:.3f} > +{SES_NEAR_ZERO_TAU} "
                f"(latency improved); classified Correct & Timely.",
            )
        # srd > 0
        return (
            "Correct but Late",
            "UP_LATE_V3",
            f"Scale-up: SRD = {srd:.1f} s > 0 (late reaction) AND "
            f"SES = {ses:.3f} > +{SES_NEAR_ZERO_TAU} (latency improved "
            f"despite lateness); classified Correct but Late.",
        )

    # Fallback (should not reach here)
    return (
        "Undefined",
        "UP_FALLBACK_V3",
        f"Unhandled combination: srd={srd}, ses={ses}, "
        f"srd_source={srd_source!r}",
    )


def main():
    if not INPUT_CSV.exists():
        print(f"ERROR: input CSV not found: {INPUT_CSV}", file=sys.stderr)
        print("       Run classify_decisions_v2.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {INPUT_CSV.name}...")
    print(f"Using SES_NEAR_ZERO_TAU = {SES_NEAR_ZERO_TAU}")
    print()

    with open(INPUT_CSV) as f:
        reader = csv.DictReader(f)
        fieldnames_in = list(reader.fieldnames or [])
        rows = list(reader)
    print(f"  Loaded {len(rows)} decisions")

    for r in rows:
        b, c, t = classify_v3(r)
        r["bucket_v3"] = b
        r["reason_code_v3"] = c
        r["reason_text_v3"] = t

    new_cols = ["bucket_v3", "reason_code_v3", "reason_text_v3"]
    fieldnames_out = list(fieldnames_in)
    for col in new_cols:
        if col not in fieldnames_out:
            fieldnames_out.append(col)

    with open(INPUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames_out, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  Rewrote {INPUT_CSV.name} with {', '.join(new_cols)}")

    # --------------------------------------------------------------
    # Summary tables
    # --------------------------------------------------------------
    print()
    print("=" * 88)
    print(f"PROPOSAL-RULE (v3) CLASSIFICATION SUMMARY  (SES_NEAR_ZERO_TAU={SES_NEAR_ZERO_TAU})")
    print("=" * 88)

    per_pattern = defaultdict(Counter)
    for r in rows:
        pat = (r.get("pattern") or "").strip() or "(untagged)"
        per_pattern[pat][r["bucket_v3"]] += 1

    header = f"{'Pattern':<12}" + "".join(f"{b:>18}" for b in BUCKETS) + f"{'TOTAL':>8}"
    print(header)
    print("-" * len(header))
    summary_rows = []
    for pat in sorted(per_pattern):
        counts = per_pattern[pat]
        total = sum(counts.values())
        line = f"{pat:<12}"
        for b in BUCKETS:
            line += f"{counts[b]:>18}"
        line += f"{total:>8}"
        print(line)
        for b in BUCKETS:
            summary_rows.append({
                "pattern": pat, "bucket_v3": b, "count": counts[b],
            })
    total_all = Counter()
    for p in per_pattern.values():
        total_all.update(p)
    print("-" * len(header))
    line = f"{'ALL':<12}"
    for b in BUCKETS:
        line += f"{total_all[b]:>18}"
    line += f"{sum(total_all.values()):>8}"
    print(line)

    with open(SUMMARY_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pattern", "bucket_v3", "count"])
        w.writeheader()
        w.writerows(summary_rows)
    print()
    print(f"Wrote per-pattern summary -> {SUMMARY_CSV.name}")

    # --------------------------------------------------------------
    # v2 vs v3 agreement (if bucket_srd is present)
    # --------------------------------------------------------------
    if "bucket_srd" in fieldnames_in:
        print()
        print("=" * 88)
        print("AGREEMENT BETWEEN v2 (SRD-only) AND v3 (proposal rules)")
        print("=" * 88)
        agree = sum(1 for r in rows if r.get("bucket_srd") == r["bucket_v3"])
        total = len(rows)
        pct = 100.0 * agree / total if total else 0.0
        print(f"  Rows where bucket_v3 == bucket_srd (v2): {agree}/{total} "
              f"({pct:.1f} %)")

        disagreements = Counter()
        for r in rows:
            v2b = r.get("bucket_srd")
            v3b = r["bucket_v3"]
            if v2b != v3b:
                disagreements[(v2b, v3b)] += 1
        if disagreements:
            print(f"  Top disagreements (v2 -> v3):")
            for (v2b, v3b), n in disagreements.most_common(10):
                print(f"    {str(v2b):<20} -> {str(v3b):<20}  {n}")


if __name__ == "__main__":
    main()
