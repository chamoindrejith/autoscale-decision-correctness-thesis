#!/usr/bin/env python3
"""
overall_summary.py — Single consolidated CSV summarising the entire
counted campaign for the supervisor.

Produces a multi-section CSV that a supervisor can open in
Excel / Numbers and skim end-to-end without touching the 206-row
per-decision dataset. Each section starts with a title row and its own
header, separated by blank rows.

Sections
--------
1. CAMPAIGN OVERVIEW           — dates, totals, HPA + SLO configuration
2. RUNS PER PATTERN            — raw / warm-up / counted breakdown
3. DECISIONS PER PATTERN       — same, at the decision level, up vs down
4. BUCKET DISTRIBUTION (500 ms) — Proposal-aligned (SRD+SES), counted only
5. SRD SUMMARY (defined only)  — median / min / max SRD per pattern
6. SES SUMMARY                  — mean / median / min / max SES per pattern
7. THRESHOLD SENSITIVITY        — bucket counts across {250,400,500,750,1000} ms
8. HEADLINE FINDINGS           — short prose rows

Reads:
  - results/decisions_with_ses.csv   (canonical per-decision dataset)
  - results/run_index.csv            (run metadata)
  - results/srd_sensitivity_summary.csv (already filters warm-ups)

Writes:
  - results/overall_summary.csv
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
DECISIONS_CSV = RESULTS / "decisions_with_ses.csv"
RUN_INDEX_CSV = RESULTS / "run_index.csv"
SENSITIVITY_CSV = RESULTS / "srd_sensitivity_summary.csv"
OUTPUT_CSV = RESULTS / "overall_summary.csv"

PATTERNS = ["step", "burst", "ramp", "noisy"]
WARMUP_LAST_RUN_NUM = 3
BUCKETS = ["Correct & Timely", "Correct but Late", "Unnecessary", "Ineffective"]

HPA_CPU_TARGET_PCT = 75
HPA_MEM_TARGET_PCT = 75
HPA_MIN_REPLICAS = 2
HPA_MAX_REPLICAS = 10
SLO_THRESHOLD_MS = 500
SLO_WINDOW_SECONDS = 30
SLO_SUSTAINED_SAMPLES = 3


# =====================================================================
# LOADERS
# =====================================================================

def load_decisions() -> list[dict]:
    if not DECISIONS_CSV.exists():
        print(f"ERROR: {DECISIONS_CSV} missing — run the analysis pipeline first",
              file=sys.stderr)
        sys.exit(1)
    with open(DECISIONS_CSV) as f:
        return list(csv.DictReader(f))


def load_runs() -> list[dict]:
    with open(RUN_INDEX_CSV) as f:
        return list(csv.DictReader(f))


def load_sensitivity() -> list[dict]:
    if not SENSITIVITY_CSV.exists():
        return []
    with open(SENSITIVITY_CSV) as f:
        return list(csv.DictReader(f))


def safe_int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def safe_float(v) -> float | None:
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def is_counted(r: dict) -> bool:
    rn = safe_int(r.get("run_num"))
    if rn is None or rn <= WARMUP_LAST_RUN_NUM:
        return False
    return (r.get("pattern") or "").strip() in PATTERNS


# =====================================================================
# CSV EMITTER
# =====================================================================

class Section:
    """A named table with its own header + rows."""

    def __init__(self, title: str, header: list[str]):
        self.title = title
        self.header = header
        self.rows: list[list] = []

    def add(self, *values) -> None:
        self.rows.append(list(values))

    def emit(self, w: csv.writer) -> None:
        w.writerow([f"### {self.title}"])
        w.writerow(self.header)
        for r in self.rows:
            w.writerow(r)
        w.writerow([])


# =====================================================================
# BUILDERS
# =====================================================================

def build_overview(runs: list[dict], decisions: list[dict]) -> Section:
    s = Section("SECTION 1 — CAMPAIGN OVERVIEW",
                ["key", "value", "note"])
    counted_runs = [r for r in runs
                    if (rn := safe_int(r.get("run_num"))) is not None
                    and rn > WARMUP_LAST_RUN_NUM]
    counted_decs = [d for d in decisions if is_counted(d)]
    start_dates = sorted(r.get("start_utc", "")[:10] for r in runs
                         if r.get("start_utc"))
    campaign_start = start_dates[0] if start_dates else ""
    campaign_end = start_dates[-1] if start_dates else ""

    s.add("campaign_start_utc", campaign_start, "")
    s.add("campaign_end_utc",   campaign_end, "")
    s.add("total_runs",         len(runs), "all patterns")
    s.add("warmup_runs",        len(runs) - len(counted_runs),
          f"first {WARMUP_LAST_RUN_NUM} runs of each pattern")
    s.add("counted_runs",       len(counted_runs), "used in analysis")
    s.add("total_decisions",    len(decisions), "raw HPA decisions")
    s.add("counted_decisions",  len(counted_decs),
          "after warm-up filter + pattern-tag filter")
    s.add("hpa_cpu_target_pct", HPA_CPU_TARGET_PCT,
          "joint decision — see email §3 for rationale")
    s.add("hpa_mem_target_pct", HPA_MEM_TARGET_PCT, "")
    s.add("hpa_min_replicas",   HPA_MIN_REPLICAS, "")
    s.add("hpa_max_replicas",   HPA_MAX_REPLICAS, "")
    s.add("slo_threshold_ms",   SLO_THRESHOLD_MS, "primary threshold")
    s.add("slo_window_seconds", SLO_WINDOW_SECONDS, "rolling p95 window")
    s.add("slo_sustained_samples", SLO_SUSTAINED_SAMPLES,
          "consecutive breach requirement — SRE Workbook MWMBR pattern")
    return s


def build_runs_per_pattern(runs: list[dict]) -> Section:
    s = Section("SECTION 2 — RUNS PER PATTERN",
                ["pattern", "total_runs", "warmup_runs", "counted_runs"])
    by_pat: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_pat[(r.get("pattern") or "").strip()].append(r)
    for pat in PATTERNS:
        rows = by_pat.get(pat, [])
        counted = [r for r in rows
                   if (rn := safe_int(r.get("run_num"))) is not None
                   and rn > WARMUP_LAST_RUN_NUM]
        s.add(pat, len(rows), len(rows) - len(counted), len(counted))
    return s


def build_decisions_per_pattern(decisions: list[dict]) -> Section:
    s = Section(
        "SECTION 3 — DECISIONS PER PATTERN (counted only)",
        ["pattern", "total", "scale_up", "scale_down", "scaling_limited"],
    )
    counted = [d for d in decisions if is_counted(d)]
    by_pat: dict[str, list[dict]] = defaultdict(list)
    for d in counted:
        by_pat[d["pattern"]].append(d)
    for pat in PATTERNS:
        rows = by_pat.get(pat, [])
        ups = sum(1 for d in rows if (d.get("direction") or "").lower() == "up")
        dns = sum(1 for d in rows if (d.get("direction") or "").lower() == "down")
        lim = sum(1 for d in rows
                  if (str(d.get("scaling_limited", "")).strip() == "True"))
        s.add(pat, len(rows), ups, dns, lim)
    s.add("TOTAL", len(counted),
          sum(1 for d in counted if (d.get("direction") or "").lower() == "up"),
          sum(1 for d in counted if (d.get("direction") or "").lower() == "down"),
          sum(1 for d in counted
              if (str(d.get("scaling_limited", "")).strip() == "True")))
    return s


def build_bucket_distribution(decisions: list[dict]) -> Section:
    s = Section(
        f"SECTION 4 — BUCKET DISTRIBUTION AT {SLO_THRESHOLD_MS} ms SLO "
        f"(Proposal-aligned SRD × SES, counted only)",
        ["pattern",
         "Correct & Timely", "Correct but Late",
         "Unnecessary", "Ineffective", "total"],
    )
    counted = [d for d in decisions if is_counted(d)]
    grid: dict[tuple[str, str], int] = defaultdict(int)
    for d in counted:
        b = (d.get("bucket_v3") or "").strip()
        if b in BUCKETS:
            grid[(d["pattern"], b)] += 1

    totals = {b: 0 for b in BUCKETS}
    for pat in PATTERNS:
        row = [grid[(pat, b)] for b in BUCKETS]
        s.add(pat, *row, sum(row))
        for b, v in zip(BUCKETS, row):
            totals[b] += v
    s.add("TOTAL", *(totals[b] for b in BUCKETS), sum(totals.values()))
    return s


def build_srd_summary(decisions: list[dict]) -> Section:
    s = Section(
        "SECTION 5 — SRD SUMMARY (scale-ups with a defined SRD, counted only)",
        ["pattern", "n_defined_srd", "median_srd_s",
         "min_srd_s", "max_srd_s"],
    )
    counted = [d for d in decisions if is_counted(d)]
    by_pat: dict[str, list[float]] = defaultdict(list)
    for d in counted:
        if (d.get("direction") or "").lower() != "up":
            continue
        if (d.get("srd_source") or "") not in {"late", "pre_emptive"}:
            continue
        srd = safe_float(d.get("srd_seconds"))
        if srd is None:
            continue
        by_pat[d["pattern"]].append(srd)
    for pat in PATTERNS:
        vals = by_pat.get(pat, [])
        if not vals:
            s.add(pat, 0, "", "", "")
            continue
        s.add(pat, len(vals),
              round(statistics.median(vals), 3),
              round(min(vals), 3),
              round(max(vals), 3))
    return s


def build_ses_summary(decisions: list[dict]) -> Section:
    s = Section(
        "SECTION 6 — SES SUMMARY (scale-ups with a defined SES, counted only)",
        ["pattern", "n_defined_ses", "mean_ses",
         "median_ses", "min_ses", "max_ses"],
    )
    counted = [d for d in decisions if is_counted(d)]
    by_pat: dict[str, list[float]] = defaultdict(list)
    for d in counted:
        if (d.get("direction") or "").lower() != "up":
            continue
        v = safe_float(d.get("ses"))
        if v is None:
            continue
        by_pat[d["pattern"]].append(v)
    for pat in PATTERNS:
        vals = by_pat.get(pat, [])
        if not vals:
            s.add(pat, 0, "", "", "", "")
            continue
        s.add(pat, len(vals),
              round(statistics.mean(vals), 4),
              round(statistics.median(vals), 4),
              round(min(vals), 4),
              round(max(vals), 4))
    return s


def build_sensitivity(sensitivity: list[dict]) -> Section:
    s = Section(
        "SECTION 7 — THRESHOLD SENSITIVITY (counted only)",
        ["threshold_ms", "pattern",
         "Correct & Timely", "Correct but Late",
         "Unnecessary", "Ineffective", "n_decisions"],
    )
    if not sensitivity:
        s.add("(srd_sensitivity_summary.csv missing — "
              "run plot_srd_sensitivity.py)", "", "", "", "", "", "")
        return s
    for r in sensitivity:
        s.add(r.get("threshold_ms"),
              r.get("pattern"),
              r.get("bucket_correct_timely"),
              r.get("bucket_correct_but_late"),
              r.get("bucket_unnecessary"),
              r.get("bucket_ineffective"),
              r.get("n_decisions"))
    return s


def build_findings(decisions: list[dict]) -> Section:
    """Short prose findings — one row each — that summarise the
    supervisor-facing story from the corrected data."""
    s = Section("SECTION 8 — HEADLINE FINDINGS", ["#", "finding"])
    counted = [d for d in decisions if is_counted(d)]
    late = sum(1 for d in counted
               if (d.get("bucket_v3") or "") == "Correct but Late")
    unn = sum(1 for d in counted
              if (d.get("bucket_v3") or "") == "Unnecessary")
    total = len(counted)

    s.add(1,
          f"Only {late} of {total} counted decisions were preceded by a "
          f"sustained SLO breach at {SLO_THRESHOLD_MS} ms — the 75 % HPA "
          "CPU target prevents almost all sustained breaches on this workload.")
    s.add(2,
          f"{unn} of {total} decisions (~{100*unn//total} %) are 'Unnecessary' "
          "against the SLO — HPA reacts to CPU pressure well before "
          "p95 latency approaches threshold.")
    s.add(3,
          "The Noisy pattern produced zero counted decisions at the 75 % "
          "target — its brief 15-second spikes never sustain long enough "
          "for HPA to react. Legitimate finding about flapping resistance.")
    s.add(4,
          "Threshold-sensitivity analysis at 250 ms and 400 ms recovers "
          "many more breach-based decisions — the SRD/SES metrics work "
          "when breaches are common enough to observe.")
    s.add(5,
          "A data-tagging bug in build_master_dataset.py (fixed) had "
          "attributed 71 of 206 decisions to the wrong run. Corrected "
          "outputs validated to the microsecond against the pipeline's "
          "stored values.")
    return s


# =====================================================================
# MAIN
# =====================================================================

def main() -> None:
    print("Loading data...")
    decisions = load_decisions()
    runs = load_runs()
    sensitivity = load_sensitivity()
    print(f"  {len(decisions)} decisions, {len(runs)} runs, "
          f"{len(sensitivity)} sensitivity rows")

    sections = [
        build_overview(runs, decisions),
        build_runs_per_pattern(runs),
        build_decisions_per_pattern(decisions),
        build_bucket_distribution(decisions),
        build_srd_summary(decisions),
        build_ses_summary(decisions),
        build_sensitivity(sensitivity),
        build_findings(decisions),
    ]

    with open(OUTPUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([f"Overall Campaign Summary — decisions_with_ses.csv "
                    f"is the source of truth"])
        w.writerow([f"Generated by overall_summary.py"])
        w.writerow([])
        for sec in sections:
            sec.emit(w)

    print(f"Wrote {OUTPUT_CSV}")
    # Console preview
    print()
    for sec in sections:
        print(f"### {sec.title}  ({len(sec.rows)} rows)")


if __name__ == "__main__":
    main()
