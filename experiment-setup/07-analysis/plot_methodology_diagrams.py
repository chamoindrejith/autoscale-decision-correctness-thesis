#!/usr/bin/env python3
"""
plot_methodology_diagrams.py — Chapter 3 methodology figures.

Generates five figures suitable for Chapter 3 (Methodology) of the
thesis and the viva-voce presentation:

  1. pipeline_architecture.png     — Data-flow diagram
     workload -> HPA -> watcher -> analysis -> buckets
  2. bucket_classification_tree.png — Decision tree of the v3 (proposal-
     aligned SRD × SES) classifier as a top-down flowchart
  3. t_slo_risk_illustration.png   — Synthetic p95 timeline showing the
     rolling-30-s p95 crossing 500 ms, 5-s sustained-breach detection,
     and T_SLO_risk annotation
  4. ses_windowing_diagram.png     — Timeline showing T_decision,
     T_pod_Ready, before-window, after-window, cold-start delay
  5. cluster_topology.png          — Node / namespace / pod layout of
     the experimental cluster

All figures are schematic / illustrative — they use synthetic data or
schematic layouts rather than reading from the campaign CSVs.

Writes:
  - results/plots/pipeline_architecture.png
  - results/plots/bucket_classification_tree.png
  - results/plots/t_slo_risk_illustration.png
  - results/plots/ses_windowing_diagram.png
  - results/plots/cluster_topology.png
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import (
    FancyBboxPatch, FancyArrowPatch, Rectangle, Circle,
)
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parent.parent
PLOTS_DIR = ROOT / "results" / "plots"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)


# =====================================================================
# HELPER: rounded box with label
# =====================================================================

def draw_box(ax, x, y, w, h, label, colour="#e8f1fb", edge="#2c5aa0",
             fontsize=10, fontweight="bold", text_colour="#1a3d6d"):
    box = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.15",
        linewidth=1.5, facecolor=colour, edgecolor=edge, zorder=2,
    )
    ax.add_patch(box)
    ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
            fontsize=fontsize, fontweight=fontweight,
            color=text_colour, zorder=3, wrap=True)


def draw_arrow(ax, x0, y0, x1, y1, label=None, colour="#333333",
               style="->", lw=1.6, offset=(0, 0.15), fontsize=8):
    arrow = FancyArrowPatch(
        (x0, y0), (x1, y1),
        arrowstyle=style, mutation_scale=15,
        linewidth=lw, color=colour, zorder=1,
    )
    ax.add_patch(arrow)
    if label:
        mx = (x0 + x1) / 2 + offset[0]
        my = (y0 + y1) / 2 + offset[1]
        ax.text(mx, my, label, ha="center", va="bottom",
                fontsize=fontsize, color=colour,
                bbox=dict(facecolor="white", edgecolor="none",
                          alpha=0.85, pad=1.5), zorder=4)


# =====================================================================
# FIGURE 1 — PIPELINE ARCHITECTURE
# =====================================================================

def plot_pipeline_architecture():
    fig, ax = plt.subplots(figsize=(15, 8.5))
    ax.set_xlim(0, 20)
    ax.set_ylim(0, 11)
    ax.axis("off")

    # LEFT COLUMN — Runtime
    draw_box(ax, 0.5, 8.5, 3.5, 1.4,
             "k6 client\n(load generator)",
             colour="#fff3d6", edge="#e19b0a")
    draw_box(ax, 0.5, 5.5, 3.5, 1.4,
             "Sample App\n(Ballerina, HPA target)",
             colour="#e8f1fb", edge="#2c5aa0")
    draw_box(ax, 0.5, 2.5, 3.5, 1.4,
             "Kubernetes\nHorizontalPodAutoscaler",
             colour="#e8f1fb", edge="#2c5aa0")

    # MIDDLE COLUMN — Instrumentation
    draw_box(ax, 5.5, 8.5, 3.8, 1.4,
             "k6 JSON output\n(per-request latency)",
             colour="#fff3d6", edge="#e19b0a")
    draw_box(ax, 5.5, 5.5, 3.8, 1.4,
             "Prometheus\n(cluster CPU / memory / replicas)",
             colour="#eef8f2", edge="#0e7a4a")
    draw_box(ax, 5.5, 2.5, 3.8, 1.4,
             "HPA Watcher (v3)\n(hpa_decision + pod_ready events)",
             colour="#f5eaf9", edge="#7d3fa8")

    # RIGHT COLUMN — Analysis pipeline
    draw_box(ax, 11, 8.5, 4.2, 1.4,
             "build_master_dataset.py\n(tag decisions to runs)",
             colour="#f4f4f4", edge="#555555")
    draw_box(ax, 11, 6.4, 4.2, 1.4,
             "compute_srd.py\n(SRD = T_decision − T_SLO_risk)",
             colour="#f4f4f4", edge="#555555")
    draw_box(ax, 11, 4.3, 4.2, 1.4,
             "compute_ses.py\n(SES via before/after p95, T_pod_Ready anchor)",
             colour="#f4f4f4", edge="#555555", fontsize=9)
    draw_box(ax, 11, 2.2, 4.2, 1.4,
             "classify_decisions_v3.py\n(SRD × SES → 4 buckets)",
             colour="#f4f4f4", edge="#555555")

    # FAR RIGHT — Outputs
    draw_box(ax, 16.5, 8.5, 3, 1.4,
             "overall_summary.csv\n(supervisor report)",
             colour="#eef8f2", edge="#0e7a4a", fontsize=9)
    draw_box(ax, 16.5, 6.4, 3, 1.4,
             "decisions_with_ses.csv\n(canonical dataset)",
             colour="#eef8f2", edge="#0e7a4a", fontsize=9)
    draw_box(ax, 16.5, 4.3, 3, 1.4,
             "16 plot PNGs\n(bucket, SRD/SES hists, sensitivity, ...)",
             colour="#eef8f2", edge="#0e7a4a", fontsize=9)
    draw_box(ax, 16.5, 2.2, 3, 1.4,
             "Chapter 4 results\n+ Chapter 5 discussion",
             colour="#fddede", edge="#c00000", fontsize=9)

    # HORIZONTAL ARROWS — Runtime side
    draw_arrow(ax, 4, 9.2, 5.5, 9.2, "HTTP requests + timing",
               offset=(0, 0.15))
    draw_arrow(ax, 4, 6.2, 5.5, 6.2, "CPU / memory metrics",
               offset=(0, 0.15))
    draw_arrow(ax, 4, 3.2, 5.5, 3.2, "watch HPA + Pod events",
               offset=(0, 0.15))

    # HORIZONTAL ARROWS — Instrumentation to analysis
    draw_arrow(ax, 9.3, 9.2, 11, 9.2)
    draw_arrow(ax, 9.3, 3.2, 11, 3.2)
    # Prometheus provides context data (dashed connection)
    ax_ = ax
    ax.plot([9.3, 11], [6.2, 6.2], linestyle=":", color="#0e7a4a",
            linewidth=1.5, zorder=1)
    ax.text(10.15, 6.4, "(context)", fontsize=8, color="#0e7a4a",
            ha="center")

    # VERTICAL ARROWS between analysis stages
    draw_arrow(ax, 13.1, 8.5, 13.1, 7.8, style="->")
    draw_arrow(ax, 13.1, 6.4, 13.1, 5.7, style="->")
    draw_arrow(ax, 13.1, 4.3, 13.1, 3.6, style="->")

    # ANALYSIS TO OUTPUTS
    draw_arrow(ax, 15.2, 9.2, 16.5, 9.2)
    draw_arrow(ax, 15.2, 7.1, 16.5, 7.1)
    draw_arrow(ax, 15.2, 5, 16.5, 5)
    draw_arrow(ax, 15.2, 2.9, 16.5, 2.9)

    # HPA drives Sample App scale (feedback loop)
    draw_arrow(ax, 2.25, 2.5, 2.25, 5.5, label="scale replicas",
               colour="#2c5aa0", style="->", lw=1.5, offset=(0.9, 0))

    # Legends / column titles
    ax.text(2.25, 10.6, "RUNTIME",
            ha="center", fontsize=13, fontweight="bold",
            color="#333333")
    ax.text(7.4, 10.6, "INSTRUMENTATION",
            ha="center", fontsize=13, fontweight="bold",
            color="#333333")
    ax.text(13.1, 10.6, "ANALYSIS PIPELINE",
            ha="center", fontsize=13, fontweight="bold",
            color="#333333")
    ax.text(18, 10.6, "OUTPUTS",
            ha="center", fontsize=13, fontweight="bold",
            color="#333333")

    ax.set_title(
        "HPA Correctness Study — Data Flow from Workload to Bucketed Decisions",
        fontsize=15, fontweight="bold", pad=15,
    )

    outpath = PLOTS_DIR / "pipeline_architecture.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# FIGURE 2 — BUCKET CLASSIFICATION DECISION TREE
# =====================================================================

def plot_bucket_classification_tree():
    fig, ax = plt.subplots(figsize=(15, 11))
    ax.set_xlim(0, 20)
    ax.set_ylim(0, 12)
    ax.axis("off")

    # Bucket colour scheme
    C_TIMELY = "#2ca02c"   # green
    C_LATE = "#ff7f0e"     # orange
    C_UNN = "#7f7f7f"      # grey
    C_INEFF = "#d62728"    # red
    C_UNDEF = "#cccccc"    # light grey

    # Decision boxes — diamond shape via FancyBboxPatch with 'roundtooth'
    # Actually use rectangles with rounded corners for uniformity
    def decision(x, y, w, h, text):
        draw_box(ax, x, y, w, h, text,
                 colour="#fff5e0", edge="#c07a1a", fontsize=10)

    def outcome(x, y, w, h, text, colour):
        draw_box(ax, x, y, w, h, text,
                 colour=colour, edge="#333333", fontsize=10.5,
                 fontweight="bold", text_colour="#ffffff")

    # Root — decision on direction
    decision(8.5, 10.3, 3, 0.9, "direction ?")

    # Left branch: scale-down -> Correct & Timely
    outcome(0.5, 10.3, 3.5, 0.9, "Correct & Timely\n(scale-down default)",
            C_TIMELY)
    draw_arrow(ax, 8.5, 10.75, 4, 10.75, label="down", offset=(0, 0.15))

    # Right branch: scale-up -> check srd_source
    decision(8.5, 8.6, 3, 0.9, "srd_source = 'no_slo_breach' ?")
    draw_arrow(ax, 10, 10.3, 10, 9.5, style="->", label="up",
               offset=(0.3, 0))

    # Yes branch of no_slo_breach -> Unnecessary
    outcome(15.5, 8.6, 3.5, 0.9, "Unnecessary\n(no breach in run)", C_UNN)
    draw_arrow(ax, 11.5, 9.05, 15.5, 9.05, label="Yes", offset=(0, 0.15))

    # No branch -> check SES is null
    decision(8.5, 6.9, 3, 0.9, "SES is null ?")
    draw_arrow(ax, 10, 8.6, 10, 7.8, style="->", label="No",
               offset=(0.3, 0))

    # Yes -> Undefined
    outcome(15.5, 6.9, 3.5, 0.9, "Undefined\n(SES not computable)", C_UNDEF)
    draw_arrow(ax, 11.5, 7.35, 15.5, 7.35, label="Yes", offset=(0, 0.15))

    # No -> check SES < -tau
    decision(8.5, 5.2, 3, 0.9, "SES < −τ ?  (τ = 0.05)")
    draw_arrow(ax, 10, 6.9, 10, 6.1, style="->", label="No",
               offset=(0.3, 0))

    # Yes -> Ineffective (latency worsened)
    outcome(15.5, 5.2, 3.5, 0.9,
            "Ineffective\n(latency worsened)", C_INEFF)
    draw_arrow(ax, 11.5, 5.65, 15.5, 5.65, label="Yes", offset=(0, 0.15))

    # No -> check |SES| ≤ tau
    decision(8.5, 3.5, 3, 0.9, "|SES| ≤ τ  (near zero) ?")
    draw_arrow(ax, 10, 5.2, 10, 4.4, style="->", label="No",
               offset=(0.3, 0))

    # Yes -> Unnecessary (SES ≈ 0)
    outcome(15.5, 3.5, 3.5, 0.9,
            "Unnecessary\n(no meaningful\nlatency change)", C_UNN)
    draw_arrow(ax, 11.5, 3.95, 15.5, 3.95, label="Yes", offset=(0, 0.15))

    # No -> SES > tau, check SRD sign
    decision(8.5, 1.7, 3, 0.9, "SRD ≤ 0 ?  (pre-emptive)")
    draw_arrow(ax, 10, 3.5, 10, 2.6, style="->", label="No",
               offset=(0.3, 0))

    # Yes -> Correct & Timely
    outcome(15.5, 1.7, 3.5, 0.9,
            "Correct & Timely\n(pre-emptive + effective)", C_TIMELY)
    draw_arrow(ax, 11.5, 2.15, 15.5, 2.15, label="Yes", offset=(0, 0.15))

    # No -> Correct but Late
    outcome(0.5, 1.7, 3.5, 0.9,
            "Correct but Late\n(late but effective)", C_LATE)
    draw_arrow(ax, 8.5, 2.15, 4, 2.15, label="No", offset=(0, 0.15))

    # Title + legend of colours at bottom
    ax.set_title(
        "v3 Classifier Decision Tree — Proposal-aligned SRD × SES Rules\n"
        "(applied in top-down priority order to every HPA decision)",
        fontsize=14, fontweight="bold", pad=15,
    )

    # Legend
    legend_elements = [
        Line2D([0], [0], marker='s', color='w', label='Correct & Timely',
               markerfacecolor=C_TIMELY, markersize=13),
        Line2D([0], [0], marker='s', color='w', label='Correct but Late',
               markerfacecolor=C_LATE, markersize=13),
        Line2D([0], [0], marker='s', color='w', label='Unnecessary',
               markerfacecolor=C_UNN, markersize=13),
        Line2D([0], [0], marker='s', color='w', label='Ineffective',
               markerfacecolor=C_INEFF, markersize=13),
        Line2D([0], [0], marker='s', color='w', label='Undefined',
               markerfacecolor=C_UNDEF, markersize=13),
    ]
    ax.legend(handles=legend_elements, loc="lower center",
              ncol=5, fontsize=10, frameon=False,
              bbox_to_anchor=(0.5, -0.03))

    outpath = PLOTS_DIR / "bucket_classification_tree.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# FIGURE 3 — T_SLO_risk detection illustration
# =====================================================================

def plot_t_slo_risk_illustration():
    fig, ax = plt.subplots(figsize=(14, 6))

    # Synthetic realistic latency timeline
    np.random.seed(42)
    t = np.linspace(0, 200, 400)  # 200 s at 0.5 s resolution
    # Rising p95 latency, with brief blip early
    p95 = np.piecewise(
        t,
        [t < 30, (t >= 30) & (t < 55), (t >= 55) & (t < 80),
         (t >= 80) & (t < 110), (t >= 110) & (t < 150), t >= 150],
        [
            lambda x: 200 + 80 * np.sin(x / 10) + np.random.normal(0, 20, x.shape),
            lambda x: 540 + 30 * np.sin(x / 3) + np.random.normal(0, 25, x.shape),   # brief blip above SLO
            lambda x: 400 + 40 * np.sin(x / 5) + np.random.normal(0, 20, x.shape),
            lambda x: 480 + 30 * np.sin(x / 5) + np.random.normal(0, 20, x.shape),
            lambda x: 620 + 40 * np.sin(x / 4) + np.random.normal(0, 25, x.shape),   # sustained above SLO
            lambda x: 700 + 30 * np.sin(x / 5) + np.random.normal(0, 20, x.shape),
        ],
    )

    # Highlight sustained-breach region [110 s, end]
    ax.axvspan(110, 200, color="#ffdddd", alpha=0.5, zorder=0,
               label="Sustained SLO breach")
    # Highlight brief blip that fails the 5-s sustained test
    ax.axvspan(30, 55, color="#fff2d6", alpha=0.5, zorder=0,
               label="Brief blip (fails 5-s sustained rule)")

    ax.plot(t, p95, color="#1f77b4", linewidth=2, label="Rolling 30-s p95 latency")
    ax.fill_between(t, 0, p95, color="#1f77b4", alpha=0.1)

    ax.axhline(500, color="red", linestyle="--", linewidth=1.5,
               label="SLO threshold (500 ms)")

    # T_SLO_risk annotation
    ax.axvline(110, color="darkred", linewidth=2.2, alpha=0.9,
               label="T_SLO_risk (breach onset)")
    ax.annotate("T_SLO_risk\n= first sample of the 5-s\nsustained-breach streak",
                xy=(110, 620), xytext=(140, 250),
                arrowprops=dict(arrowstyle="->", color="darkred", lw=1.5),
                fontsize=10, ha="left", fontweight="bold",
                color="darkred",
                bbox=dict(facecolor="white", edgecolor="darkred",
                          boxstyle="round,pad=0.4"))

    # Also annotate the brief blip
    ax.annotate("Brief blip: p95 > 500 ms for < 5 s\n→ does NOT trigger T_SLO_risk\n"
                "(SLO_SUSTAINED_SECONDS = 5 protects\nagainst cold-start noise)",
                xy=(45, 560), xytext=(60, 900),
                arrowprops=dict(arrowstyle="->", color="#8a6d0a", lw=1.3),
                fontsize=9.5, ha="left",
                color="#8a6d0a",
                bbox=dict(facecolor="white", edgecolor="#8a6d0a",
                          boxstyle="round,pad=0.4"))

    ax.set_xlabel("Time within run (seconds)", fontsize=11)
    ax.set_ylabel("Rolling 30-s p95 of http_req_duration (ms)", fontsize=11)
    ax.set_title(
        "T_SLO_risk Detection — Multi-Window Multi-Burn-Rate Rule\n"
        "T_SLO_risk fires only when p95 > 500 ms continuously for ≥ 5 s "
        "(SLO_SUSTAINED_SECONDS)",
        fontsize=13, fontweight="bold", pad=12,
    )
    ax.set_xlim(0, 200)
    ax.set_ylim(0, 1000)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.95)

    plt.tight_layout()
    outpath = PLOTS_DIR / "t_slo_risk_illustration.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# FIGURE 4 — SES windowing diagram
# =====================================================================

def plot_ses_windowing_diagram():
    fig, ax = plt.subplots(figsize=(14, 6.5))

    # Timeline stretches from t = -80 to t = +200 (T_decision is at 0)
    t_dec = 0
    t_pod_ready = 40      # 40 s cold-start delay for illustration
    before_start = t_dec - 60
    before_end = t_dec - 1
    after_start = t_pod_ready
    after_end = t_pod_ready + 60

    # Synthetic latency trace — steady before, high during cold-start, drop after
    np.random.seed(11)
    t = np.linspace(-80, 200, 500)
    p95 = np.piecewise(
        t,
        [t < 0, (t >= 0) & (t < t_pod_ready),
         (t >= t_pod_ready) & (t < t_pod_ready + 60),
         t >= t_pod_ready + 60],
        [
            lambda x: 550 + 30 * np.sin(x / 5) + np.random.normal(0, 25, x.shape),
            lambda x: 700 + 60 * np.sin(x / 3) + np.random.normal(0, 30, x.shape),   # during cold-start, latency HIGH
            lambda x: 320 + 30 * np.sin(x / 4) + np.random.normal(0, 20, x.shape),   # after pod Ready, latency drops
            lambda x: 300 + 20 * np.sin(x / 5) + np.random.normal(0, 20, x.shape),
        ],
    )

    ax.plot(t, p95, color="#333333", linewidth=1.8,
            label="p95 latency (illustrative)")
    ax.axhline(500, color="red", linestyle=":", linewidth=1,
               alpha=0.6, label="500 ms SLO")

    # Vertical anchors
    ax.axvline(t_dec, color="green", linewidth=2, alpha=0.85,
               label="T_decision (HPA fires)")
    ax.axvline(t_pod_ready, color="blue", linewidth=2, alpha=0.85,
               label="T_pod_Ready (new pod becomes Ready)")

    # Before window shading
    ax.axvspan(before_start, before_end, color="#c3e0ff", alpha=0.55,
               label="Before window [T_decision − 60 s, T_decision − 1 s]",
               zorder=0)
    # After window shading (correct: T_pod_Ready anchor)
    ax.axvspan(after_start, after_end, color="#c3ffc3", alpha=0.55,
               label="After window [T_pod_Ready, T_pod_Ready + 60 s]",
               zorder=0)

    # WRONG after window (T_decision anchor) shown as red dashed
    ax.axvspan(t_dec, t_dec + 60, color="none",
               edgecolor="red", linewidth=1.4, hatch="///",
               alpha=0.30, zorder=0)
    ax.text(30, 850, "If we anchored the after-window at\n"
                     "T_decision (red hatched region), we'd sample\n"
                     "latency during the pod's cold-start phase and\n"
                     "systematically classify effective HPA decisions\n"
                     "as Ineffective.",
            fontsize=9, ha="left", color="darkred",
            bbox=dict(facecolor="white", edgecolor="darkred",
                      boxstyle="round,pad=0.4"))

    # Cold-start delay bracket
    ax.annotate("", xy=(t_pod_ready, 900), xytext=(t_dec, 900),
                arrowprops=dict(arrowstyle="<->", color="#7d3fa8", lw=1.6))
    ax.text((t_dec + t_pod_ready) / 2, 940,
            "cold_start_delay_s = T_pod_Ready − T_decision",
            ha="center", fontsize=9.5, color="#7d3fa8",
            fontweight="bold")

    # Labels for formula
    ax.text(before_start - 3, 200, "Latency_before = p95 of\nrequests in this window",
            ha="right", va="center", fontsize=9,
            color="#0e2f6d", fontweight="bold")
    ax.text(after_end + 3, 200, "Latency_after = p95 of\nrequests in this window",
            ha="left", va="center", fontsize=9,
            color="#0e5a2a", fontweight="bold")

    ax.set_xlabel("Time relative to T_decision (seconds)", fontsize=11)
    ax.set_ylabel("p95 latency (ms)  [illustrative]", fontsize=11)
    ax.set_title(
        "SES Windowing — Before/After Anchored at T_decision and T_pod_Ready\n"
        "SES = (Latency_before − Latency_after) / Latency_before",
        fontsize=13, fontweight="bold", pad=12,
    )
    ax.set_xlim(-80, 200)
    ax.set_ylim(0, 1000)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right", fontsize=8.5, framealpha=0.95)

    plt.tight_layout()
    outpath = PLOTS_DIR / "ses_windowing_diagram.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# FIGURE 5 — CLUSTER TOPOLOGY
# =====================================================================

def plot_cluster_topology():
    fig, ax = plt.subplots(figsize=(14, 9.5))
    ax.set_xlim(0, 20)
    ax.set_ylim(0, 13)
    ax.axis("off")

    # DROPLET outer box
    droplet = Rectangle((0.5, 0.5), 15, 12, linewidth=2.5,
                        facecolor="#f9fafc", edgecolor="#333333",
                        zorder=1)
    ax.add_patch(droplet)
    ax.text(0.7, 12.15, "DigitalOcean Droplet (4 vCPU / 8 GiB / Ubuntu 24.04)",
            fontsize=11, fontweight="bold", color="#333333")

    # k3s node box
    k3s = Rectangle((1, 0.9), 14, 10.7, linewidth=2,
                    facecolor="#eef4fb", edgecolor="#2c5aa0", zorder=2)
    ax.add_patch(k3s)
    ax.text(1.2, 11.28, "k3s v1.34.6+k3s1  (single-node Kubernetes cluster)",
            fontsize=10.5, fontweight="bold", color="#2c5aa0")

    # NAMESPACE 1 — autoscale-research
    ns1 = Rectangle((1.5, 5.3), 8.5, 5.6, linewidth=1.5,
                    facecolor="#e8f1fb", edgecolor="#2c5aa0", zorder=3)
    ax.add_patch(ns1)
    ax.text(1.7, 10.5, "namespace: autoscale-research",
            fontsize=10, fontweight="bold", color="#2c5aa0",
            style="italic")

    # sample-app Deployment (2..10 pods)
    for i, x in enumerate([2.0, 3.3, 4.6, 5.9]):
        draw_box(ax, x, 8.5, 1.15, 1.4,
                 f"pod {i+1}", colour="#ffffff",
                 edge="#2c5aa0", fontsize=8.5)
    ax.text(4.2, 10.1, "sample-app Deployment (min=2, max=10)",
            fontsize=9, color="#2c5aa0")
    ax.text(4.2, 8.2, "sample-app-*   |   200m CPU / 512 Mi memory (req = lim)",
            ha="center", fontsize=8, color="#333333")
    ax.text(4.2, 7.9, "container: autoscale-sample:v5   |   JVM: -Xmx384m",
            ha="center", fontsize=8, color="#333333")

    # HPA
    draw_box(ax, 7.4, 8.5, 2.3, 1.4,
             "HPA\ntarget CPU 75 %\ntarget Mem 75 %\nmax = 10",
             colour="#fff5e0", edge="#c07a1a", fontsize=8.5)

    # hpa-watcher
    draw_box(ax, 2.0, 5.8, 3.5, 1.8,
             "hpa-watcher-*  (v3)\nemits hpa_decision +\npod_ready JSONL events",
             colour="#f5eaf9", edge="#7d3fa8", fontsize=9)
    # PVC bubble
    draw_box(ax, 6.0, 5.9, 3.7, 1.5,
             "PVC hpa-watcher-data\n(local-path 1 GiB)\nhpa-events.jsonl",
             colour="#f5eaf9", edge="#7d3fa8", fontsize=8.5)

    # Arrow: watcher writes to PVC
    draw_arrow(ax, 5.5, 6.7, 6.0, 6.7)

    # NAMESPACE 2 — monitoring
    ns2 = Rectangle((10.4, 5.3), 4.4, 5.6, linewidth=1.5,
                    facecolor="#eef8f2", edgecolor="#0e7a4a", zorder=3)
    ax.add_patch(ns2)
    ax.text(10.6, 10.5, "namespace: monitoring",
            fontsize=10, fontweight="bold", color="#0e7a4a",
            style="italic")

    draw_box(ax, 10.7, 8.7, 3.8, 1.4,
             "Prometheus\n(kube-prometheus-stack 83.6.0)\n15 GiB PVC, 14-day retention",
             colour="#e0f4e6", edge="#0e7a4a", fontsize=8.5)

    draw_box(ax, 10.7, 6.8, 3.8, 1.4,
             "Grafana + dashboards\n(port 3000 via port-forward)",
             colour="#e0f4e6", edge="#0e7a4a", fontsize=9)

    draw_box(ax, 10.7, 5.5, 3.8, 1.1,
             "node-exporter + kube-state-metrics",
             colour="#e0f4e6", edge="#0e7a4a", fontsize=8.5)

    # NAMESPACE 3 (services outside our namespaces)
    ns3 = Rectangle((1.5, 1.4), 13.2, 3.5, linewidth=1.5,
                    facecolor="#fff5e0", edgecolor="#c07a1a", zorder=3)
    ax.add_patch(ns3)
    ax.text(1.7, 4.55, "cluster-wide services",
            fontsize=10, fontweight="bold", color="#c07a1a",
            style="italic")

    draw_box(ax, 2.0, 2.4, 3.5, 1.6,
             "NodePort Service\nautoscale-sample\n:30080  →  :9000",
             colour="#fff2d6", edge="#c07a1a", fontsize=9)
    draw_box(ax, 6.0, 2.4, 3.5, 1.6,
             "ServiceMonitor\n5-s scrape interval\n→ Prometheus",
             colour="#fff2d6", edge="#c07a1a", fontsize=9)
    draw_box(ax, 10.0, 2.4, 3.5, 1.6,
             "metrics-server\n(HPA reads from here)",
             colour="#fff2d6", edge="#c07a1a", fontsize=9)

    # OUTSIDE THE DROPLET — the k6 client
    k6 = Rectangle((16.5, 8), 3.2, 2.5, linewidth=2,
                   facecolor="#ffece0", edgecolor="#e0631f", zorder=2)
    ax.add_patch(k6)
    ax.text(18.1, 9.9, "k6 client\n(load generator)",
            ha="center", fontsize=10, fontweight="bold",
            color="#e0631f")
    ax.text(18.1, 8.5, "runs on droplet\nin LOCAL_MODE\n(same tmux session\nas run-campaign.sh)",
            ha="center", fontsize=8, color="#333333")

    # Arrow from k6 to NodePort
    draw_arrow(ax, 16.5, 8.5, 14.5, 3.5, label="HTTP GET",
               colour="#e0631f", lw=1.8, offset=(-0.2, 0.4))
    # Arrow HPA <-> metrics-server
    draw_arrow(ax, 10, 3.2, 8.5, 8.5, label="metrics query",
               colour="#c07a1a", lw=1.3, offset=(0.4, -0.3))
    # Arrow HPA -> sample-app pods
    draw_arrow(ax, 7.4, 9.2, 6.9, 9.2, label="scale",
               colour="#2c5aa0", lw=1.5, offset=(0, 0.15))
    # Arrow watcher -> HPA
    draw_arrow(ax, 5.5, 6.7, 8.5, 8.5, label="watches HPAs",
               colour="#7d3fa8", lw=1.3, offset=(0.3, 0.3))

    ax.set_title(
        "Cluster Topology — Where Each Component Runs",
        fontsize=14, fontweight="bold", pad=15,
    )

    outpath = PLOTS_DIR / "cluster_topology.png"
    plt.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"Saved {outpath.name}")
    plt.close()


# =====================================================================
# MAIN
# =====================================================================

def main():
    print("\n1/5 pipeline_architecture.png")
    plot_pipeline_architecture()
    print("\n2/5 bucket_classification_tree.png")
    plot_bucket_classification_tree()
    print("\n3/5 t_slo_risk_illustration.png")
    plot_t_slo_risk_illustration()
    print("\n4/5 ses_windowing_diagram.png")
    plot_ses_windowing_diagram()
    print("\n5/5 cluster_topology.png")
    plot_cluster_topology()


if __name__ == "__main__":
    main()
