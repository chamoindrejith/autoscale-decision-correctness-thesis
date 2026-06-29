# HPA Watcher

Watches HPA resources in `autoscale-research` namespace and records every
scaling decision as a JSONL event. This is the instrument that provides
`T_decision` for your SRD formula.

## Build & deploy

```bash
# On the Droplet, from this folder:
docker build -t hpa-watcher:v1 .
docker save hpa-watcher:v1 | sudo k3s ctr images import -
kubectl apply -f watcher-deployment.yaml

# Watch the live stream of decisions
kubectl logs -n autoscale-research -l app=hpa-watcher -f

# Retrieve the JSONL file from the pod after a run
POD=$(kubectl get pod -n autoscale-research -l app=hpa-watcher -o name | head -1)
kubectl cp -n autoscale-research "${POD#pod/}:/data/hpa-events.jsonl" ./hpa-events.jsonl
```

## Event format

```json
{
  "event_type": "hpa_decision",
  "detected_at": "2026-04-18T10:12:45.321Z",
  "namespace": "autoscale-research",
  "hpa_name": "autoscale-sample-hpa",
  "target": {"kind": "Deployment", "name": "autoscale-sample"},
  "replicas": {"before": 2, "after": 4, "current_at_detection": 2},
  "direction": "up",
  "trigger_metric": "cpu",
  "trigger_value": "72% (target 50%)",
  "last_scale_time": "2026-04-18T10:12:45.302Z",
  "min_replicas": 2,
  "max_replicas": 8,
  "hpa_conditions": [...]
}
```

Use `detected_at` as `T_decision`. Pair with Prometheus latency data
(`response_time_seconds` quantiles) to compute SRD and SES.
