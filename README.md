# Observability for Autoscaling Correctness in Kubernetes-Based Systems

Experimental framework for a final-year research project evaluating the
correctness of Kubernetes Horizontal Pod Autoscaler (HPA) decisions using
observability-driven outcome-oriented metrics. Compares HPA behavior across
four distinct workload patterns to characterise reaction-time and
effectiveness, and produces a per-decision correctness dataset for further
analysis.

## Research Question

Given a Kubernetes deployment under varying workload patterns, how can the
correctness of individual HPA scaling decisions be measured using observable
outcomes (CPU utilisation and request latency), and how does HPA behavior
vary across workload shapes?

## Research Objectives

**General Objective:** Evaluate the correctness of autoscaling decisions in
Kubernetes-based systems using observability-driven outcome-oriented metrics.

**Specific Objectives:**
1. Define measurable criteria for classifying scaling decisions as correct or incorrect.
2. Design observability-based metrics for effectiveness, timeliness, and necessity.
3. Empirically evaluate Kubernetes HPA behavior under controlled workloads.
4. Analyze the relationship between individual scaling decisions and SLO outcomes.

## Workload Patterns Evaluated

| Pattern   | Profile                                              | Tests                                    |
| --------- | ---------------------------------------------------- | ---------------------------------------- |
| **Step**  | Sustained constant load (15 VUs for ~8 min)          | HPA response to steady demand            |
| **Burst** | 1 min idle → 60 s spike at 25 VUs → 7 min tail       | HPA reactivity to sudden spikes          |
| **Ramp**  | 30 s idle → 8 min linear ramp 0→20 VUs → 4 min tail  | HPA threshold sensitivity                |
| **Noisy** | Continuous variable arrival rate (4–35 RPS, jagged)  | HPA flapping resistance / averaging      |

Each pattern is repeated **20 times** to characterise run-to-run variance, for
a total campaign of **80 runs** and approximately **495 HPA decisions**.

## Correctness Metrics

| Metric                            | Formula                                                  | Question it answers          |
| --------------------------------- | -------------------------------------------------------- | ---------------------------- |
| **SRD** (Scale Reaction Delay)    | `T_decision − T_SLO_risk`                                | Was the decision timely?     |
| **SES** (Scale Effectiveness Score) | `(Latency_before − Latency_after) / Latency_before`    | Did the decision help?       |

Each decision is also placed in one of four correctness buckets:

| Bucket              | SRD          | SES                                |
| ------------------- | ------------ | ---------------------------------- |
| Correct & Timely    | Low          | High                               |
| Correct but Late    | High         | High                               |
| Unnecessary         | (varies)     | (low — no improvement to make)     |
| Ineffective         | (varies)     | Low (blocked by maxReplicas, etc.) |

The operational thresholds are documented in `analysis/classification_rules.pdf`.

## Repository Structure

```
├── configs/                  # Kubernetes manifests and Helm values
│   ├── 00-namespace.yaml
│   ├── 01-deployment.yaml
│   ├── 02-service.yaml
│   ├── 03-hpa.yaml
│   ├── 04-servicemonitor.yaml
│   └── prometheus-values.yaml
├── droplet/                  # Host provisioning
│   ├── create-droplet.md
│   ├── install-k3s.sh
│   ├── install-ballerina.sh
│   └── install-monitoring.sh
├── watcher/                  # Custom HPA decision watcher service
│   ├── watcher.py
│   ├── watcher-deployment.yaml
│   └── README.md
├── workloads/                # k6 load-test scripts
│   ├── step-load.js
│   ├── burst-load.js
│   ├── ramp-load.js
│   ├── noisy-load.js
│   └── calib-probe.js        # stepped-VU calibration probe
├── scripts/                  # Campaign orchestration
│   └── run-campaign.sh
├── analysis/                 # Per-decision data pipeline + plotting
│   ├── build_master_dataset.py
│   ├── classify_decisions.py
│   ├── compute_ses.py
│   ├── extract_ses_input_dataset.py
│   ├── build_ses_window_summary.py
│   ├── export_aggregated_plot_data.py
│   ├── plot_decision_latency.py
│   ├── plot_all_decisions.py
│   └── classification_rules.pdf
└── data/                     # Pointer to raw datasets + outputs on Drive
    └── README.md

# Note: results/ is created at runtime by the analysis pipeline and is
# gitignored. Pipeline outputs (CSVs, plots) and raw k6 data live on
# Google Drive — see data/README.md.
```

## Infrastructure

- **Platform:** DigitalOcean droplet (Ubuntu 24.04)
- **Kubernetes:** k3s single-node cluster (2 vCPU / 4 GiB)
- **Sample app:** Ballerina HTTP service exposing a CPU-bound `/api/compute`
  endpoint (`n` controls CPU work per request)
- **Observability:** kube-prometheus-stack (Prometheus + Grafana)
- **Custom instrumentation:** Python HPA decision watcher (one of the
  contributions of this study) that captures every HPA scaling decision
  with millisecond-precision timestamps and the CPU value observed at
  decision time, writing to a persistent volume as JSONL
- **Load generation:** k6 v2.0 driven from a developer Mac over SSH

The HPA is configured for `target = 30% CPU`, `minReplicas = 2`,
`maxReplicas = 6`. The k6 endpoint parameter is calibrated to
`n = 50,000` (≈ 29 ms CPU per request) so CPU scales linearly with
virtual-user count and HPA decisions remain observable.

---

# Running the Experiment

Steps 1–7 are one-time setup; steps 8–11 are the experiment itself.
All commands assume you are running them from the repository root, with
`kubectl` configured for the experimental cluster, unless stated otherwise.

## 1. Provision the droplet and install k3s

Follow the steps in `droplet/create-droplet.md` to create the droplet.
Then on the droplet:

```bash
sudo ./droplet/install-k3s.sh
sudo ./droplet/install-ballerina.sh
```

Apply the cluster hardening steps from `HARDENING_GUIDE.md` (UFW firewall,
fail2ban, SSH key-only access, scoped passwordless sudo for reboot).

## 2. Deploy the sample app, HPA, and namespace

```bash
kubectl apply -f configs/00-namespace.yaml
kubectl apply -f configs/01-deployment.yaml
kubectl apply -f configs/02-service.yaml
kubectl apply -f configs/03-hpa.yaml
```

## 3. Install the observability stack

```bash
./droplet/install-monitoring.sh        # installs kube-prometheus-stack via Helm
kubectl apply -f configs/04-servicemonitor.yaml
```

The default Prometheus retention is 3 days. Extend it to 30 days so the
campaign's CPU time-series remain available for SRD computation:

```bash
kubectl patch prometheus -n monitoring kube-prometheus-stack-prometheus \
  --type='merge' -p '{"spec":{"retention":"30d"}}'
```

## 4. Deploy the custom HPA decision watcher

```bash
kubectl apply -f watcher/watcher-deployment.yaml
```

The watcher writes events to `/data/hpa-events.jsonl` on a persistent volume.
Verify it is logging:

```bash
kubectl logs -n autoscale-research -l app=hpa-watcher -f
```

## 5. Calibrate the workload parameter

Run the calibration probe to confirm `n = 50,000` gives the desired linear
CPU response on your hardware:

```bash
k6 run --out json=results/calib.json \
  -e TARGET_URL="http://<DROPLET-IP>:30080/api/compute" \
  workloads/calib-probe.js
```

If your cluster differs from a 2-vCPU / 4 GiB droplet, adjust `n` in each
load script so CPU scales linearly with VU count and HPA decisions emerge
between idle and saturation.

## 6. Run an attended smoke workload

Verify the full pipeline (k6 → cluster → HPA → watcher → data) by running
one Step workload attended:

```bash
TS=$(date +%Y%m%d-%H%M%S)
k6 run --out json=results/step-run-$TS.json \
  -e TARGET_URL="http://<DROPLET-IP>:30080/api/compute" \
  workloads/step-load.js
```

Confirm the watcher recorded HPA decisions:

```bash
kubectl logs -n autoscale-research -l app=hpa-watcher | tail -20
```

## 7. Run the full campaign

The orchestrator `scripts/run-campaign.sh` runs N iterations of one pattern
unattended, with health checks, idle waits, and droplet reboots between runs.

```bash
# 20 runs per pattern × 4 patterns = 80 runs total
./scripts/run-campaign.sh step  1 20
./scripts/run-campaign.sh burst 1 20
./scripts/run-campaign.sh ramp  1 20
./scripts/run-campaign.sh noisy 1 20
```

Each pattern batch is ~5–6 hours. Run inside `tmux`/`screen`, or prepend
`caffeinate -i` on macOS to prevent local sleep.

## 8. Pull the watcher JSONL

```bash
WATCHER_POD=$(kubectl get pods -n autoscale-research \
  -l app=hpa-watcher -o jsonpath='{.items[0].metadata.name}')
kubectl cp autoscale-research/$WATCHER_POD:/data/hpa-events.jsonl \
  results/hpa-events-full.jsonl
```

## 9. Build the master decisions dataset

```bash
python3 analysis/build_master_dataset.py
```

This reads `results/hpa-events-full.jsonl` and the k6 JSON files, tags each
HPA decision with the run it belongs to, and writes `results/master_decisions.csv`
plus `results/run_index.csv`.

## 10. Apply the 4-bucket classification

```bash
python3 analysis/classify_decisions.py
```

Writes `results/classified_decisions.csv` and `results/classification_summary.csv`.

## 11. Compute SES per decision and aggregate

```bash
python3 analysis/compute_ses.py step
python3 analysis/compute_ses.py burst
python3 analysis/compute_ses.py ramp
python3 analysis/compute_ses.py noisy

python3 analysis/extract_ses_input_dataset.py all
python3 analysis/build_ses_window_summary.py
python3 analysis/export_aggregated_plot_data.py
```

Writes `decisions_with_ses.csv`, `ses_summary.csv`, `ses_window_summary.csv`,
`ses_input_dataset.csv`, and `aggregated_latency_per_pattern.csv`.

## 12. Generate plots

```bash
python3 analysis/plot_decision_latency.py 8 --save
python3 analysis/plot_all_decisions.py
```

PNGs are written into `results/plots/`.

---

## Dataset Summary

- Total runs: **80** (20 per pattern × 4 patterns)
- HPA decisions captured: **495** (488 tagged to runs)
- Workload campaign duration: **May 23 – June 8, 2026**
- Per-request latency points: **~841,594** in the SES windows

## Key Findings (in progress)

- **Burst-pattern HPA reacts very late** — median CPU at first scale-up is
  **109%** (vs. **38%** for Ramp), indicating substantial reaction-time lag.
- **All scale-down decisions** across patterns classified as Correct & Timely,
  consistent with the HPA's conservative scale-down stabilization design.
- **Burst pattern hits `maxReplicas` cap on 42%** of its scale-up decisions
  on a 2-vCPU node — captured as a Threats-to-Validity point.

## Large Raw Data

The 80 raw k6 JSON outputs and the 91 MB `ses_input_dataset.csv` are not
included in this repository due to size (~2 GB total). They are hosted on
Google Drive — see `data/README.md` for the access link.

---

## Project Information

- **Student:** S.A.C.S. Indrejith (2020/ICT/65)
- **Supervisor:** Ms. R. Yasotha
- **Co-Supervisor:** Mr. Suvin Kodituwakku
- **Institution:** University of Vavuniya — Department of Physical Science
- **Module:** IT4216 Research Project (2026)
- **Final submission target:** 30 June 2026
