#!/usr/bin/env python3
"""
plot_results_extras.py — Chapter 4 additional figures and tables.

Generates seven items suitable for Chapter 4 (Results) of the thesis
and the viva-voce presentation:

  1. srd_boxplot_per_pattern.png       Boxplot of SRD per pattern
  2. ses_boxplot_per_pattern.png       Boxplot of SES per pattern
  3. bucket_stacked_100pct.png         Stacked 100 % bars of bucket
                                        proportions per pattern
  4. cpu_utilization_case_study.png    CPU % per exemplar run per
                                        pattern (from decision-time
                                        CPU readings in the watcher
                                        events)
  5. cold_start_delay_distribution.png Histogram of
                                        T_pod_Ready − T_decision
  6. replica_trajectory_overlay.png    All counted runs' replica
                                        counts overlaid per pattern
  7. descriptive_statistics.csv        Mean/median/std/min/max for
                                        SRD, SES, cold_start_delay
                                        per pattern

Reads:
  - results/decisions_with_ses.csv
  - results/run_index.csv

Writes:
  - results/plots/<file>.png (six PNGs)
  - results/descriptive_statistics.csv
"""
from __future__ import annotations

import csv
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
DECISIONS_CSV = RESULTS / "decisions_with_ses.csv"
RUN_INDEX_CSV = RESULTS / "run_index.csv"
PLOTS_DIR = RESULTS / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

PATTERNS = ["step", "burst", "ramp", "noisy"]
PATTERN_COLORS = {
    "step":  "#1f77b4",
    "burst": "#ff7f0e",
    "ramp":  "#2ca02c",
    "noisy": "#d62728",
}
WARMUP_LAST_RUN_NUM = 3

BUCKETS = ["Correct & Timely", "Correct but Late",
           "Unnecessary", "Ineffective", "Undefined"]
BUCKET_COLORS = {
    "Correct & Timely":  "#2ca02c",
    "Correct but Late":  "#ff7f0e",
    "Unnecessary":       "#7f7f7f",
    "Ineffective":       "#d62728",
    "Undefined":         "#cccccc",
}


def parse_iso(s):
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.+?\.)(\d+)(.+)$", s)
    if m:
        micros = m.group(2)[:6].ljust(6, "0")
        s = m.group(1) + micros + m.group(3)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def safe_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def is_counted(r):
    try:
        rn = int(r.get("run_num") or 0)
    except ValueError:
        return False
    if rn <= WARMUP_LAST_RUN_NUM:
        return False
    return (r.get("pattern") or "").strip() in PATTERNS


def load_decisions():
    with open(DECISIONS_CSV) as f:
        return list(csv.DictReader(f))


def load_run_starts():
    starts = {}
    with open(RUN_INDEX_CSV) as f:
        for r in csv.DictReader(f):
            try:
                starts[r["run_label"]] = parse_iso(r["start_utc"])
            except (KeyError, ValueError):
                continue
    return starts


# =====================================================================
# 1. SRD BOXPLOT
# =====================================================================

def plot_srd_boxplot(decisions):
    per_pat = defaultdict(list)
    for d in decisions:
        if not is_counted(d):
            continue
        if (d.get("direction") or "").lower() != "up":
            continue
        if (d.get("srd_source") or "") not in ("late", "pre_emptive"):
            continue
        srd = safe_float(d.get("srd_seconds"))
        if srd is None:
            continue
        per_pat[d["pattern"]].append(srd)

    fig, ax = plt.subplots(figsize=(11, 6.5))
    data = [per_pat.get(p, []) for p in PATTERNS]
    labels = [f"{p.upper()}\n(n={len(per_pat.get(p, []))})" for p in PATTERNS]
    bp = ax.boxplot(data, labels=labels, patch_artist=True,
                    widths=0.55, showfliers=True,
                    medianprops=dict(color="black", linewidth=1.8),
                    whis=1.5)
    for patch, pattern in zip(bp['boxes'], PATTERNS):
        patch.set_facecolor(PATTERN_COLORS[pattern])
        patch.set_alpha(0.75)
        patch.set_edgecolor("black")

    ax.axhline(0, color="black", linestyle="--", alpha=0.6, linewidth=1,
               label="SLO breach moment (SRD = 0)")
    ax.set_ylabel("SRD (seconds)  —  negative = pre-emptive, "
                  "positive = late", fontsize=11)
    ax.set_title(
        "Scale Reaction Delay Distribution by Workload Pattern\n"
        "Boxes = median + IQR (25–75 %), whiskers = 1.5 × IQR, "
        "circles = outliers",
        fontsize=13, fontweight="bold", pad=12,
    )
    ax.grid(axis="y", alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    plt.tight_layout()

    outpath = PLOTS_DIR / "srd_boxplot_per_pattern.png"
    plt.savefig(outpath, dpi=140, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# 2. SES BOXPLOT
# =====================================================================

def plot_ses_boxplot(decisions):
    per_pat = defaultdict(list)
    for d in decisions:
        if not is_counted(d):
            continue
        if (d.get("direction") or "").lower() != "up":
            continue
        v = safe_float(d.get("ses"))
        if v is None:
            continue
        per_pat[d["pattern"]].append(v)

    fig, ax = plt.subplots(figsize=(11, 6.5))
    data = [per_pat.get(p, []) for p in PATTERNS]
    labels = [f"{p.upper()}\n(n={len(per_pat.get(p, []))})" for p in PATTERNS]
    bp = ax.boxplot(data, labels=labels, patch_artist=True,
                    widths=0.55, showfliers=True,
                    medianprops=dict(color="black", linewidth=1.8),
                    whis=1.5)
    for patch, pattern in zip(bp['boxes'], PATTERNS):
        patch.set_facecolor(PATTERN_COLORS[pattern])
        patch.set_alpha(0.75)
        patch.set_edgecolor("black")

    ax.axhline(0, color="black", linestyle="--", alpha=0.6, linewidth=1,
               label="No change (SES = 0)")
    ax.axhline(0.05, color="#0e7a4a", linestyle=":", alpha=0.6, linewidth=1,
               label="+τ (0.05, classifier boundary)")
    ax.axhline(-0.05, color="#c00000", linestyle=":", alpha=0.6, linewidth=1,
               label="−τ (−0.05, classifier boundary)")

    ax.set_ylabel("SES  —  positive = latency improved after scaling",
                  fontsize=11)
    ax.set_title(
        "Scale Effectiveness Score Distribution by Workload Pattern\n"
        "Boxes = median + IQR (25–75 %), whiskers = 1.5 × IQR",
        fontsize=13, fontweight="bold", pad=12,
    )
    ax.grid(axis="y", alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    plt.tight_layout()

    outpath = PLOTS_DIR / "ses_boxplot_per_pattern.png"
    plt.savefig(outpath, dpi=140, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# 3. STACKED 100% BAR CHART
# =====================================================================

def plot_bucket_stacked_100pct(decisions):
    counted = [d for d in decisions if is_counted(d)]
    per_pat_bucket = defaultdict(lambda: defaultdict(int))
    for d in counted:
        bucket = (d.get("bucket_v3") or "Undefined").strip()
        if bucket not in BUCKETS:
            bucket = "Undefined"
        per_pat_bucket[d["pattern"]][bucket] += 1

    fig, ax = plt.subplots(figsize=(11, 6.5))
    x = np.arange(len(PATTERNS))
    bar_width = 0.65
    bottom = np.zeros(len(PATTERNS))
    for bucket in BUCKETS:
        pcts = []
        counts = []
        for pattern in PATTERNS:
            total = sum(per_pat_bucket[pattern].values())
            c = per_pat_bucket[pattern][bucket]
            pcts.append(100 * c / total if total else 0)
            counts.append(c)
        ax.bar(x, pcts, bar_width, bottom=bottom,
               color=BUCKET_COLORS[bucket], edgecolor="black",
               linewidth=0.5, label=bucket)
        for i, (pct, c) in enumerate(zip(pcts, counts)):
            if pct > 3:  # only label if segment is big enough
                ax.text(i, bottom[i] + pct / 2,
                        f"{c}\n({pct:.0f} %)",
                        ha="center", va="center", fontsize=9,
                        fontweight="bold",
                        color="white" if bucket in ("Ineffective",
                                                     "Correct but Late")
                              else "black")
        bottom += np.array(pcts)

    ax.set_xticks(x)
    ax.set_xticklabels(
        [f"{p.upper()}\n(n = {sum(per_pat_bucket[p].values())})"
         for p in PATTERNS],
        fontsize=11,
    )
    ax.set_ylabel("Percentage of counted decisions", fontsize=11)
    ax.set_ylim(0, 105)
    ax.set_title(
        "Bucket Composition per Workload Pattern (100 % Stacked)\n"
        "Proposal-aligned SRD × SES classification, 500 ms SLO, "
        "sustained = 5 s",
        fontsize=13, fontweight="bold", pad=12,
    )
    ax.legend(loc="lower center", ncol=len(BUCKETS), fontsize=9,
              bbox_to_anchor=(0.5, -0.13), frameon=False)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()

    outpath = PLOTS_DIR / "bucket_stacked_100pct.png"
    plt.savefig(outpath, dpi=140, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# 4. CPU UTILIZATION CASE STUDY (from watcher decision-time CPU readings)
# =====================================================================

def plot_cpu_utilization_case_study(decisions, run_starts):
    # For each pattern, pick one exemplar run and plot CPU% at each
    # decision timestamp as a step function.
    # Watcher records current_pct in decisions_with_ses.csv (CPU% at
    # decision time). We use these sparse points, connected as a step
    # function, per pattern for run_num == 4 (first counted).
    DEFAULT_RUN = 4
    per_pat = {}
    for d in decisions:
        try:
            rn = int(d.get("run_num") or 0)
        except ValueError:
            continue
        if rn != DEFAULT_RUN:
            continue
        pat = (d.get("pattern") or "").strip()
        if pat not in PATTERNS:
            continue
        cpu = safe_float(d.get("current_pct"))
        rl = d.get("run_label") or ""
        start = run_starts.get(rl)
        if cpu is None or start is None:
            continue
        try:
            ts = parse_iso(d["timestamp_utc"])
        except (KeyError, ValueError):
            continue
        per_pat.setdefault(pat, []).append(
            ((ts - start).total_seconds(), cpu, rl)
        )

    fig, axes = plt.subplots(2, 2, figsize=(15, 9), sharey=True)
    axes = axes.flatten()
    for ax, pattern in zip(axes, PATTERNS):
        points = sorted(per_pat.get(pattern, []))
        colour = PATTERN_COLORS[pattern]
        if not points:
            ax.text(0.5, 0.5, f"no data for {pattern} run 4",
                    ha="center", va="center", transform=ax.transAxes,
                    color="grey")
            ax.set_title(f"{pattern.upper()}  —  (no data)",
                         fontsize=12, fontweight="bold", loc="left",
                         color=colour)
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        run_label = points[0][2]

        ax.step(xs, ys, where="post", color=colour, linewidth=2.2,
                marker="o", markersize=6, label="CPU % at HPA decision")
        ax.fill_between(xs, 0, ys, step="post", color=colour, alpha=0.20)

        ax.axhline(75, color="red", linestyle="--", linewidth=1.2,
                   alpha=0.7, label="HPA target 75 %")

        ax.set_title(f"{pattern.upper()}  —  {run_label}  "
                     f"({len(points)} decisions)",
                     fontsize=12, fontweight="bold", loc="left",
                     color=colour)
        ax.set_xlabel("Seconds since run start", fontsize=10)
        ax.set_ylabel("CPU % as HPA saw it", fontsize=10)
        ax.set_ylim(0, max(max(ys) * 1.15, 100))
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)

    fig.suptitle(
        "CPU Utilization at Each HPA Decision (Exemplar Run per Pattern)\n"
        "current_pct field of the hpa_decision events, plotted as a step "
        "function.",
        fontsize=13, y=0.995,
    )
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    outpath = PLOTS_DIR / "cpu_utilization_case_study.png"
    plt.savefig(outpath, dpi=140, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# 5. COLD-START DELAY DISTRIBUTION
# =====================================================================

def plot_cold_start_delay(decisions):
    per_pat = defaultdict(list)
    for d in decisions:
        if not is_counted(d):
            continue
        if (d.get("direction") or "").lower() != "up":
            continue
        v = safe_float(d.get("cold_start_delay_s"))
        if v is None:
            continue
        per_pat[d["pattern"]].append(v)

    fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True)
    axes = axes.flatten()
    all_vals = [v for lst in per_pat.values() for v in lst]
    if all_vals:
        lo = min(0, min(all_vals))
        hi = max(all_vals) * 1.05
        bin_edges = np.linspace(lo, hi, 26)
    else:
        bin_edges = np.linspace(0, 60, 26)

    for ax, pattern in zip(axes, PATTERNS):
        vals = per_pat.get(pattern, [])
        colour = PATTERN_COLORS[pattern]
        if not vals:
            ax.text(0.5, 0.5, f"no cold-start data\nfor {pattern}",
                    ha="center", va="center", transform=ax.transAxes,
                    color="grey")
            ax.set_title(f"{pattern.upper()}", fontsize=12,
                         fontweight="bold", loc="left", color=colour)
            continue
        ax.hist(vals, bins=bin_edges, color=colour, edgecolor="black",
                alpha=0.8, linewidth=0.4)
        mn = statistics.mean(vals)
        md = statistics.median(vals)
        ax.axvline(mn, color="red", linestyle=":", alpha=0.8,
                   linewidth=1.4, label=f"mean = {mn:.1f} s")
        ax.axvline(md, color="darkblue", linestyle=":", alpha=0.8,
                   linewidth=1.4, label=f"median = {md:.1f} s")
        ax.set_title(f"{pattern.upper()}  (n = {len(vals)})",
                     fontsize=12, fontweight="bold", loc="left",
                     color=colour)
        ax.set_xlabel("Cold-start delay: T_pod_Ready − T_decision (s)",
                      fontsize=10)
        ax.set_ylabel("Number of scale-up decisions", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right", fontsize=9)

    fig.suptitle(
        "Cold-Start Delay Distribution per Workload Pattern\n"
        "Cold-start delay = time from HPA scale-up decision to newly-"
        "created pod becoming Ready",
        fontsize=13, y=0.995,
    )
    plt.tight_layout(rect=[0, 0, 1, 0.955])
    outpath = PLOTS_DIR / "cold_start_delay_distribution.png"
    plt.savefig(outpath, dpi=140, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# 6. MULTI-RUN REPLICA TRAJECTORY OVERLAY
# =====================================================================

def plot_replica_trajectory_overlay(decisions, run_starts):
    # per pattern -> {run_label: [(t_within_run, replicas_after), ...]}
    per_pat_runs = defaultdict(lambda: defaultdict(list))
    for d in decisions:
        if not is_counted(d):
            continue
        rl = d.get("run_label") or ""
        if rl not in run_starts:
            continue
        try:
            ts = parse_iso(d["timestamp_utc"])
            replicas_after = int(d["replicas_after"])
        except (KeyError, ValueError):
            continue
        t_within = (ts - run_starts[rl]).total_seconds()
        per_pat_runs[d["pattern"]][rl].append((t_within, replicas_after))

    fig, axes = plt.subplots(2, 2, figsize=(15, 9), sharey=True)
    axes = axes.flatten()
    for ax, pattern in zip(axes, PATTERNS):
        runs = per_pat_runs.get(pattern, {})
        colour = PATTERN_COLORS[pattern]
        if not runs:
            ax.text(0.5, 0.5, f"no runs for {pattern}",
                    ha="center", va="center", transform=ax.transAxes,
                    color="grey")
            ax.set_title(f"{pattern.upper()}", fontsize=12,
                         fontweight="bold", loc="left", color=colour)
            continue
        max_x = 0
        for rl, points in sorted(runs.items()):
            points.sort(key=lambda p: p[0])
            xs = [0] + [p[0] for p in points]
            initial = points[0] if points else (0, 2)
            # Start from 2 replicas at run start
            ys = [2] + [p[1] for p in points]
            ax.step(xs, ys, where="post", color=colour, alpha=0.30,
                    linewidth=1.1)
            if xs:
                max_x = max(max_x, xs[-1])

        # Overlay a bold median trajectory
        # Sample every 30 s and take median across runs
        if runs:
            grid = list(range(0, int(max_x) + 30, 30))
            medians = []
            for gt in grid:
                per_run_val = []
                for rl, pts in runs.items():
                    # find current replica count at time gt
                    val = 2  # default starting
                    for t, r in pts:
                        if t <= gt:
                            val = r
                    per_run_val.append(val)
                if per_run_val:
                    medians.append(statistics.median(per_run_val))
            ax.plot(grid, medians, color="black", linewidth=2.2,
                    label=f"Median across {len(runs)} runs")

        ax.axhline(10, color="red", linestyle=":", alpha=0.5, linewidth=1,
                   label="maxReplicas = 10")
        ax.axhline(2, color="grey", linestyle=":", alpha=0.5, linewidth=1)
        ax.set_ylim(0, 12)

        ax.set_title(f"{pattern.upper()}  —  {len(runs)} counted runs "
                     f"overlaid",
                     fontsize=12, fontweight="bold", loc="left",
                     color=colour)
        ax.set_xlabel("Seconds since run start", fontsize=10)
        ax.set_ylabel("Replica count", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)

    fig.suptitle(
        "Replica-Count Trajectories — All Counted Runs Overlaid per Pattern\n"
        "Thin lines = individual runs, thick black = median across runs",
        fontsize=13, y=0.995,
    )
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    outpath = PLOTS_DIR / "replica_trajectory_overlay.png"
    plt.savefig(outpath, dpi=140, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# 7. DESCRIPTIVE STATISTICS TABLE (CSV)
# =====================================================================

def build_descriptive_stats(decisions):
    per_pat_srd = defaultdict(list)
    per_pat_ses = defaultdict(list)
    per_pat_csd = defaultdict(list)
    per_pat_ups = defaultdict(int)
    per_pat_downs = defaultdict(int)

    for d in decisions:
        if not is_counted(d):
            continue
        pat = d["pattern"]
        dir_ = (d.get("direction") or "").lower()
        if dir_ == "up":
            per_pat_ups[pat] += 1
            srd = safe_float(d.get("srd_seconds"))
            if srd is not None and (d.get("srd_source") or "") in ("late", "pre_emptive"):
                per_pat_srd[pat].append(srd)
            ses = safe_float(d.get("ses"))
            if ses is not None:
                per_pat_ses[pat].append(ses)
            csd = safe_float(d.get("cold_start_delay_s"))
            if csd is not None:
                per_pat_csd[pat].append(csd)
        elif dir_ == "down":
            per_pat_downs[pat] += 1

    def stats(vals):
        if not vals:
            return {"n": 0, "mean": "", "median": "",
                    "std": "", "min": "", "max": ""}
        return {
            "n": len(vals),
            "mean": round(statistics.mean(vals), 3),
            "median": round(statistics.median(vals), 3),
            "std": round(statistics.stdev(vals), 3) if len(vals) > 1 else 0,
            "min": round(min(vals), 3),
            "max": round(max(vals), 3),
        }

    outpath = RESULTS / "descriptive_statistics.csv"
    with open(outpath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Descriptive Statistics — Rerun (July 2026)"])
        w.writerow([f"Generated from decisions_with_ses.csv "
                    f"(counted runs only, warm-up 1-3 excluded)"])
        w.writerow([])

        # SRD section
        w.writerow(["### SRD (seconds) — scale-ups with defined SRD"])
        w.writerow(["pattern", "n", "mean", "median", "std", "min", "max"])
        for pat in PATTERNS:
            s = stats(per_pat_srd.get(pat, []))
            w.writerow([pat, s["n"], s["mean"], s["median"], s["std"],
                        s["min"], s["max"]])
        w.writerow([])

        # SES section
        w.writerow(["### SES — scale-ups with defined SES"])
        w.writerow(["pattern", "n", "mean", "median", "std", "min", "max"])
        for pat in PATTERNS:
            s = stats(per_pat_ses.get(pat, []))
            w.writerow([pat, s["n"], s["mean"], s["median"], s["std"],
                        s["min"], s["max"]])
        w.writerow([])

        # Cold-start delay section
        w.writerow(["### Cold-start delay (seconds) — "
                    "T_pod_Ready − T_decision"])
        w.writerow(["pattern", "n", "mean", "median", "std", "min", "max"])
        for pat in PATTERNS:
            s = stats(per_pat_csd.get(pat, []))
            w.writerow([pat, s["n"], s["mean"], s["median"], s["std"],
                        s["min"], s["max"]])
        w.writerow([])

        # Direction counts
        w.writerow(["### Decision counts per direction (counted)"])
        w.writerow(["pattern", "scale_up", "scale_down", "total"])
        for pat in PATTERNS:
            u = per_pat_ups.get(pat, 0)
            d = per_pat_downs.get(pat, 0)
            w.writerow([pat, u, d, u + d])

    print(f"Saved {outpath.name}")


# =====================================================================
# MAIN
# =====================================================================

def main():
    print("Loading data...")
    decisions = load_decisions()
    run_starts = load_run_starts()
    print(f"  {len(decisions)} decisions, {len(run_starts)} runs")

    print("\n1/7 srd_boxplot_per_pattern.png")
    plot_srd_boxplot(decisions)

    print("\n2/7 ses_boxplot_per_pattern.png")
    plot_ses_boxplot(decisions)

    print("\n3/7 bucket_stacked_100pct.png")
    plot_bucket_stacked_100pct(decisions)

    print("\n4/7 cpu_utilization_case_study.png")
    plot_cpu_utilization_case_study(decisions, run_starts)

    print("\n5/7 cold_start_delay_distribution.png")
    plot_cold_start_delay(decisions)

    print("\n6/7 replica_trajectory_overlay.png")
    plot_replica_trajectory_overlay(decisions, run_starts)

    print("\n7/7 descriptive_statistics.csv")
    build_descriptive_stats(decisions)


if __name__ == "__main__":
    main()
