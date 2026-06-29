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
  "replicas": {"before": 2, "after": 4, "current_at_detection": 2},
  "direction": "up",
  "trigger_metric": "cpu",                       # the driving metric
  "trigger_value": "72% (target 75%)",
  "metrics": [                                   # all Resource metrics
    {"metric": "cpu",    "current_pct": 72, "target_pct": 75},
    {"metric": "memory", "current_pct": 41, "target_pct": 75}
  ],
  "last_scale_time": "2026-04-18T10:12:45.302Z",
  "min_replicas": 2,
  "max_replicas": 5,
  "hpa_conditions": [...]
}

Each line is a research-grade record of a single HPA decision. Combined with
Prometheus metrics (for latency before/after) this is enough to compute SRD
and SES per the research methodology.

Runs inside the cluster as a Deployment (see watcher-deployment.yaml).
Requires ServiceAccount with `get/list/watch` on horizontalpodautoscalers.
State (last seen desiredReplicas per HPA) is persisted to STATE_PATH so the
watcher survives pod restarts without dropping the first subsequent decision.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

from kubernetes import client, config, watch


NAMESPACE = os.environ.get("WATCH_NAMESPACE", "autoscale-research")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "/data/hpa-events.jsonl")
STATE_PATH = os.environ.get("STATE_PATH", "/data/watcher-state.json")
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


def extract_trigger(hpa) -> tuple[str | None, str | None, list]:
    """
    Identify which metric is actually driving the HPA's decision.

    The HPA's algorithm: for each metric, compute a desired-replica count
    from (current / target), then pick the MAX across metrics. So the
    metric with the highest (current / target) ratio is the one whose
    recommendation won — that is the trigger.

    Returns (trigger_metric_name, trigger_value_str, all_metrics_list).
    `all_metrics_list` records every Resource metric so the downstream
    analysis can recover CPU and memory independently rather than relying
    on the inferred trigger field alone.

    Previous implementation returned the first non-empty value and so
    frequently mis-attributed the decision when both CPU and memory had
    readings.
    """
    spec_metrics = (hpa.spec.metrics or [])
    status_metrics = (hpa.status.current_metrics or []) if hpa.status else []

    all_metrics: list[dict] = []
    driving_name: str | None = None
    driving_value: str | None = None
    driving_ratio: float = float("-inf")

    for spec_m, status_m in zip(spec_metrics, status_metrics):
        if getattr(spec_m, "type", None) != "Resource":
            continue
        resource_name = spec_m.resource.name
        target_pct = getattr(spec_m.resource.target, "average_utilization", None)

        current_pct = None
        if status_m and status_m.resource:
            current_pct = getattr(
                status_m.resource.current, "average_utilization", None
            )

        all_metrics.append({
            "metric": resource_name,
            "current_pct": current_pct,
            "target_pct": target_pct,
        })

        if current_pct is not None and target_pct is not None and target_pct > 0:
            ratio = current_pct / target_pct
            if ratio > driving_ratio:
                driving_ratio = ratio
                driving_name = resource_name
                driving_value = f"{current_pct}% (target {target_pct}%)"

    return driving_name, driving_value, all_metrics


def load_state() -> dict:
    """Load the persisted last_desired dict so that the first scaling decision
    after a watcher pod restart is not silently dropped."""
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def save_state(last_desired: dict) -> None:
    """Atomically persist last_desired so it survives pod restarts.
    Writes to a .tmp file and renames (rename is atomic on POSIX)."""
    tmp = STATE_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(last_desired, f)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        print(f"[watcher] failed to save state: {e}", flush=True)


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
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    out_file = open(OUTPUT_PATH, "a", buffering=1)  # line-buffered

    # Load persisted last-known desiredReplicas so the FIRST decision after a
    # watcher pod restart is not silently dropped. (Previous in-memory-only
    # design lost ~1 decision per restart, and the watcher restarted 9 times
    # during the original campaign.)
    last_desired: dict = load_state()
    restored_keys = list(last_desired.keys())

    # Boot event so we can align log timestamps with clock skew, and so we
    # can later count restarts and audit the persisted-state restoration.
    write_event(
        {
            "event_type": "watcher_started",
            "detected_at": iso_utc_now(),
            "namespace": NAMESPACE,
            "restored_state_keys": restored_keys,
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
                    if key not in last_desired:
                        last_desired[key] = desired
                        save_state(last_desired)
                    continue

                if event_type == "DELETED":
                    last_desired.pop(key, None)
                    save_state(last_desired)
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

                # MODIFIED — only record when desiredReplicas actually changed.
                # NOTE: prev may have been loaded from persisted state (so the
                # first MODIFIED after a restart will be compared against the
                # last known value, not silently swallowed as before).
                if prev is None:
                    last_desired[key] = desired
                    save_state(last_desired)
                    continue
                if desired == prev:
                    continue

                trigger_metric, trigger_value, all_metrics = extract_trigger(hpa)
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
                    # trigger_metric / trigger_value now reflect the metric
                    # whose (current / target) ratio was the highest — the
                    # one the HPA's algorithm actually selected.
                    "trigger_metric": trigger_metric,
                    "trigger_value": trigger_value,
                    # All metric readings (CPU and memory) so downstream
                    # analysis does not have to re-infer them from the
                    # single 'trigger' field.
                    "metrics": all_metrics,
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
                save_state(last_desired)

        except Exception as e:
            print(f"[watcher] stream error: {e}. Retrying in 5s.", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
