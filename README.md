# Observability for Autoscaling Correctness in Kubernetes-Based Systems

Experimental framework for a final-year research project evaluating the
correctness of Kubernetes Horizontal Pod Autoscaler (HPA) decisions using
observability-driven outcome-oriented metrics. The framework compares HPA
behavior across four distinct workload patterns to characterise reaction-time
and effectiveness, producing a per-decision correctness dataset for further
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

| Metric                              | Formula                                                  | Question it answers          |
| ----------------------------------- | -------------------------------------------------------- | ---------------------------- |
| **SRD** (Scale Reaction Delay)      | `T_decision − T_SLO_risk`                                | Was the decision timely?     |
| **SES** (Scale Effectiveness Score) | `(Latency_before − Latency_after) / Latency_before`      | Did the decision help?       |

Each decision is also placed in one of four correctness buckets:

| Bucket              | SRD          | SES                                |
| ------------------- | ------------ | ---------------------------------- |
| Correct & Timely    | Low          | High                               |
| Correct but Late    | High         | High                               |
| Unnecessary         | (varies)     | (low — no improvement to make)     |
| Ineffective         | (varies)     | Low (blocked by maxReplicas, etc.) |

Operational thresholds are documented in `analysis/classification_rules.pdf`.

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
│   └── calib-probe.js
├── scripts/                  # Campaign orchestration
│   └── run-campaign.sh
├── analysis/                 # Per-decision data pipeline and plotting
│   ├── build_master_dataset.py
│   ├── classify_decisions.py
│   ├── compute_ses.py
│   ├── extract_ses_input_dataset.py
│   ├── build_ses_window_summary.py
│   ├── export_aggregated_plot_data.py
│   ├── plot_decision_latency.py
│   ├── plot_all_decisions.py
│   └── classification_rules.pdf
└── data/                     # Pointer to raw datasets and outputs on Drive
    └── README.md
```

The `results/` directory is created at runtime by the analysis pipeline and
is excluded from version control. Raw experimental data and generated
outputs are hosted on Google Drive — see `data/README.md`.

## Infrastructure

- **Platform:** DigitalOcean droplet (Ubuntu 24.04)
- **Kubernetes:** k3s single-node cluster (2 vCPU / 4 GiB)
- **Sample app:** Ballerina HTTP service exposing a CPU-bound `/api/compute`
  endpoint; `n` controls CPU work per request
- **Observability:** kube-prometheus-stack (Prometheus + Grafana)
- **Custom instrumentation:** Python HPA decision watcher (a contribution of
  this study) capturing every HPA scaling decision with millisecond-precision
  timestamps and the CPU value observed at decision time, writing to a
  persistent volume as JSONL
- **Load generation:** k6 v2.0 driven over SSH from a developer workstation

HPA configuration: `target = 30% CPU`, `minReplicas = 2`, `maxReplicas = 6`.
The k6 endpoint parameter is calibrated to `n = 50,000` (≈ 29 ms CPU per
request) so CPU scales linearly with virtual-user count and HPA decisions
remain observable.

---

# Running the Experiment

Steps 1–7 are one-time setup; steps 8–12 are the experiment itself.
All commands assume execution from the repository root with `kubectl`
configured for the experimental cluster, unless stated otherwise.

## 1. Provision the droplet and install k3s

Follow `droplet/create-droplet.md` to create the droplet. Then on the droplet:

```bash
sudo ./droplet/install-k3s.sh
sudo ./droplet/install-ballerina.sh
```

Cluster hardening (UFW firewall, fail2ban, SSH key-only access, scoped
passwordless sudo for reboot) is applied separately following site policy.

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
Verify decisions are being logged:

```bash
kubectl logs -n autoscale-research -l app=hpa-watcher -f
```

## 5. Calibrate the workload parameter

Run the calibration probe to confirm `n = 50,000` produces a linear CPU
response on the target hardware:

```bash
k6 run --out json=results/calib.json \
  -e TARGET_URL="http://<DROPLET-IP>:30080/api/compute" \
  workloads/calib-probe.js
```

For different hardware, adjust `n` in each load script so CPU scales linearly
with VU count and HPA decisions emerge between idle and saturation.

## 6. Run an attended smoke workload

Verify the full pipeline (k6 → cluster → HPA → watcher → data) with one
attended Step workload:

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

Each batch takes approximately 5–6 hours. Run inside `tmux` or `screen`.

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

Reads `results/hpa-events-full.jsonl` and the k6 JSON files, tags each HPA
decision with its containing run, and writes `results/master_decisions.csv`
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

## Project Information

- **Student:** S.A.C.S. Indrejith (2020/ICT/65)
- **Supervisor:** Ms. R. Yasotha
- **Co-Supervisor:** Mr. Suvin Kodituwakku
- **University:** University of Vavuniya — Department of Physical Science
