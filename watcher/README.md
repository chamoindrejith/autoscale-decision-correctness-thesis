# HPA Decision Watcher

A Python service that watches Horizontal Pod Autoscaler resources in the
`autoscale-research` namespace and records every scaling decision as a JSONL
event on a persistent volume. This is the instrument that provides
`T_decision` for the SRD computation.

## Build and Deploy

Run on the droplet, from this directory:

```bash
docker build -t hpa-watcher:v1 .
docker save hpa-watcher:v1 | sudo k3s ctr images import -
kubectl apply -f watcher-deployment.yaml
```

Tail the live stream of decisions:

```bash
kubectl logs -n autoscale-research -l app=hpa-watcher -f
```

Retrieve the JSONL file from the pod after a campaign:

```bash
POD=$(kubectl get pod -n autoscale-research -l app=hpa-watcher -o name | head -1)
kubectl cp -n autoscale-research "${POD#pod/}:/data/hpa-events.jsonl" \
  ./hpa-events.jsonl
```

## Event Format

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

The `detected_at` field provides `T_decision` for SRD computation. Pairing
with Prometheus latency data (`response_time_seconds` quantiles) yields both
SRD and SES values per decision.
