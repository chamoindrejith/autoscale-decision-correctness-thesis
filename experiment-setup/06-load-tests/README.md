# Load Tests

One k6 script per workload pattern defined in the research PDF (§4).

| File              | Pattern             | Purpose                           |
|-------------------|---------------------|-----------------------------------|
| `step-load.js`    | Step                | Reaction time / SRD measurement   |
| `burst-load.js`   | Short Burst         | Detect unnecessary / overreaction |
| `ramp-load.js`    | Gradual Ramp        | Threshold sensitivity             |
| `noisy-load.js`   | Noisy               | Oscillation / instability         |

## Install k6 (on your Mac)

```bash
brew install k6
```

## Run

```bash
# Hit the app via the Droplet's NodePort
export TARGET_URL="http://<DROPLET_IP>:30080/api/compute"

k6 run -e TARGET_URL=$TARGET_URL step-load.js
k6 run -e TARGET_URL=$TARGET_URL burst-load.js
k6 run -e TARGET_URL=$TARGET_URL ramp-load.js
k6 run -e TARGET_URL=$TARGET_URL noisy-load.js
```

## Recording for analysis

Each test prints a summary. For research, dump detailed results to JSON:

```bash
k6 run --out json=step-run-$(date +%s).json \
       -e TARGET_URL=$TARGET_URL step-load.js
```

Combine with:
- `hpa-events.jsonl` from the watcher (→ `T_decision`)
- Prometheus `response_time_seconds` (→ `Latency_before/after` for SES)
- Prometheus `container_cpu_usage_seconds_total` (→ SLO risk detection for SRD)

to compute SRD and SES per scaling decision.

## Run order for a clean experiment

1. Deploy app + HPA + monitoring + watcher.
2. Wait 10 min idle to measure baseline CPU%.
3. Retune HPA threshold = idle + 25%. `kubectl apply -f ../03-kubernetes-manifests/03-hpa.yaml`.
4. Run each load pattern 5× with 5-minute gaps in between (let HPA scale down).
5. Pull `hpa-events.jsonl` + Prometheus data for analysis.
