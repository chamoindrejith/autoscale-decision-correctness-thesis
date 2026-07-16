#!/usr/bin/env python3
"""
drop_v2_columns.py — Remove the legacy v2 (SRD-only) bucket columns from
decisions_with_ses.csv now that v3 (SRD × SES) is the canonical classifier.

Removes: bucket_srd, reason_code_srd, reason_text_srd
Keeps:   bucket_v3, reason_code_v3, reason_text_v3 (canonical)
         all other columns unchanged

Safe to re-run; if the v2 columns are already absent, this is a no-op.

Reads/writes:
  - results/decisions_with_ses.csv (in place)
"""
from __future__ import annotations
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "results" / "decisions_with_ses.csv"

DROP_COLUMNS = {"bucket_srd", "reason_code_srd", "reason_text_srd"}


def main():
    if not CSV_PATH.exists():
        raise SystemExit(f"ERROR: {CSV_PATH} not found")

    with open(CSV_PATH) as f:
        reader = csv.DictReader(f)
        original_fields = list(reader.fieldnames or [])
        rows = list(reader)

    to_drop = [c for c in DROP_COLUMNS if c in original_fields]
    if not to_drop:
        print(f"No v2 columns present in {CSV_PATH.name}; nothing to do.")
        return

    kept_fields = [c for c in original_fields if c not in DROP_COLUMNS]

    with open(CSV_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=kept_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"Dropped columns from {CSV_PATH.name}: {', '.join(to_drop)}")
    print(f"Remaining columns: {len(kept_fields)}")


if __name__ == "__main__":
    main()
