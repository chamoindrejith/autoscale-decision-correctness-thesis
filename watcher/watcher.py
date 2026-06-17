"""
HPA Decision Watcher
====================

Observes HorizontalPodAutoscaler resources in a given namespace and records
every scaling decision as a structured JSON event.

Output format (one JSON object per line, JSONL):

{
  "event_type": "hpa_decision",
  "detected_at": "2026-04-18T10:12:45.321Z",
  "namespace": "autoscale-research",
  "hpa_name": "autoscale-sample-hpa",
  "target": {"kind": "Deployment", "name": "autoscale-sample"},
  "replicas": {"before": 2, "after": 4},
  "direction": "up",
  "trigger_metric": "cpu",
  "trigger_value": "72% (target 50%)",
  "last_scale_time": "2026-04-18T10:12:45.302Z",
  "hpa_conditions": [...]
}

Each line is a research-grade record of a single HPA decision. Combined with
Prometheus metrics (for latency before/after) this is enough to compute SRD
and SES per the research methodology.

Runs inside the cluster as a Deployment (see watcher-deployment.yaml).
Requires ServiceAccount with `get/list/watch` on horizontalpodautoscalers.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

from kubernetes import client, config, watch


NAMESPACE = os.environ.get("WATCH_NAMESPACE", "autoscale-research")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "/data/hpa-events.jsonl")
LOG_TO_STDOUT = os.environ.get("LOG_TO_STDOUT", "true").lower() == "true"


def iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def to_iso(ts) -> str | None:
    """Kubernetes returns datetime objects for timestamps; convert to ISO."""
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def extract_trigger(hpa) -> tuple[str | None, str | None]:
    """
    Look at hpa.status.currentMetrics and hpa.spec.metrics to figure out
    which metric exceeded its target (best effort).
    """
    spec_metrics = (hpa.spec.metrics or [])
    status_metrics = (hpa.status.current_metrics or []) if hpa.status else []

    for spec_m, status_m in zip(spec_metrics, status_metrics):
        if getattr(spec_m, "type", None) != "Resource":
            continue
        resource_name = spec_m.resource.name
        target = spec_m.resource.target
        target_pct = getattr(target, "average_utilization", None)

        current_pct = None
        if status_m and status_m.resource:
            current_pct = getattr(
                status_m.resource.current, "average_utilization", None
            )

        if current_pct is not None and target_pct is not None:
            return resource_name, f"{current_pct}% (target {target_pct}%)"
    return None, None


def write_event(event: dict, out_file) -> None:
    line = json.dumps(event, default=str)
    out_file.write(line + "\n")
    out_file.flush()
    if LOG_TO_STDOUT:
        print(line, flush=True)


def main() -> None:
    # Load kube config — in-cluster first, fall back to ~/.kube/config for dev.
    try:
        config.load_incluster_config()
        print(f"[watcher] loaded in-cluster config", flush=True)
    except config.ConfigException:
        config.load_kube_config()
        print(f"[watcher] loaded local kubeconfig", flush=True)

    api = client.AutoscalingV2Api()

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    out_file = open(OUTPUT_PATH, "a", buffering=1)  # line-buffered

    # Seed: remember last-known desiredReplicas per HPA so we only emit on change.
    last_desired: dict[str, int] = {}

    # Boot event so we can align log timestamps with clock skew.
    write_event(
        {
            "event_type": "watcher_started",
            "detected_at": iso_utc_now(),
            "namespace": NAMESPACE,
        },
        out_file,
    )

    while True:
        try:
            w = watch.Watch()
            for raw in w.stream(
                api.list_namespaced_horizontal_pod_autoscaler,
                namespace=NAMESPACE,
                timeout_seconds=0,
            ):
                hpa = raw["object"]
                event_type = raw["type"]  # ADDED, MODIFIED, DELETED
                name = hpa.metadata.name
                key = f"{NAMESPACE}/{name}"
                status = hpa.status

                desired = (status.desired_replicas if status else None) or 0
                current = (status.current_replicas if status else None) or 0
                prev = last_desired.get(key)

                if event_type == "ADDED":
                    # Seed: remember baseline silently
                    last_desired[key] = desired
                    continue

                if event_type == "DELETED":
                    last_desired.pop(key, None)
                    write_event(
                        {
                            "event_type": "hpa_deleted",
                            "detected_at": iso_utc_now(),
                            "namespace": NAMESPACE,
                            "hpa_name": name,
                        },
                        out_file,
                    )
                    continue

                # MODIFIED — only record when desiredReplicas actually changed
                if prev is None:
                    last_desired[key] = desired
                    continue
                if desired == prev:
                    continue

                trigger_metric, trigger_value = extract_trigger(hpa)
                direction = "up" if desired > prev else "down"

                event = {
                    "event_type": "hpa_decision",
                    "detected_at": iso_utc_now(),
                    "namespace": NAMESPACE,
                    "hpa_name": name,
                    "target": {
                        "kind": hpa.spec.scale_target_ref.kind,
                        "name": hpa.spec.scale_target_ref.name,
                    },
                    "replicas": {
                        "before": prev,
                        "after": desired,
                        "current_at_detection": current,
                    },
                    "direction": direction,
                    "trigger_metric": trigger_metric,
                    "trigger_value": trigger_value,
                    "last_scale_time": to_iso(
                        status.last_scale_time if status else None
                    ),
                    "min_replicas": hpa.spec.min_replicas,
                    "max_replicas": hpa.spec.max_replicas,
                    "hpa_conditions": [
                        {
                            "type": c.type,
                            "status": c.status,
                            "reason": c.reason,
                            "message": c.message,
                            "last_transition_time": to_iso(c.last_transition_time),
                        }
                        for c in (status.conditions or [])
                    ]
                    if status
                    else [],
                }

                write_event(event, out_file)
                last_desired[key] = desired

        except Exception as e:
            print(f"[watcher] stream error: {e}. Retrying in 5s.", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
