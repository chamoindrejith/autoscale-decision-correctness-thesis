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
- `hpa-events-full-post-{pattern}.jsonl` from the watcher's persistent
  volume (→ `T_decision`, and — via v3 `pod_ready` events — `T_pod_Ready`
  for the SES after-window anchor)
- k6 client-side `http_req_duration` (→ `Latency_before/after` for SES
  and → SLO breach detection for SRD)

to compute SRD and SES per scaling decision. Note: Ballerina's server-side
Prometheus reporter was not deployed for the campaign (see
`02-sample-app/Config.toml`), so `response_time_seconds` from Prometheus is
not available — k6's client-side latency is the sole latency source.

## Run order for a clean experiment

1. Deploy app + HPA + monitoring + watcher (see the per-folder READMEs).
2. Fix the HPA CPU + memory targets at 75% (see `03-kubernetes-manifests/03-hpa.yaml`).
   The pilot's earlier "idle + 25%" heuristic was superseded during
   post-pilot recalibration — see the campaign log and the corresponding
   supervisor communication for the rationale.
3. For each pattern, run:
   ```
   ./run-campaign.sh step  1 23
   ./run-campaign.sh burst 1 23
   ./run-campaign.sh ramp  1 23
   ./run-campaign.sh noisy 1 23
   ```
   The orchestrator handles pre-flight health checks, waits for HPA to
   return to `minReplicas=2 + CPU < 5%` between runs, and captures both
   the k6 output and the HPA watcher events per run. First 3 runs of
   each pattern are warm-up (discarded in analysis) so the effective
   counted campaign is 20 runs per pattern.
4. Pull the per-run event JSONs and the durable
   `hpa-events-full-post-{pattern}.jsonl` snapshot from the watcher PV,
   then run the analysis pipeline (see `../07-analysis/`).
