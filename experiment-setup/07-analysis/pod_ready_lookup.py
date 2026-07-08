#!/usr/bin/env python3
"""
pod_ready_lookup.py — Helper for anchoring the SES after-window at T_pod_Ready.

Per analysis/slo_risk_and_ses_methodology.md §3.5, the SES `after` window must
start when a newly created pod is actually serving traffic (T_pod_Ready), not
when HPA emitted the decision (T_decision). Anchoring at T_decision would
sample latency during the ~20-40 s window when the new pod is still starting
up and would systematically bias correct scale-up decisions into the
"Ineffective" bucket.

The v3 watcher (experiment-setup/05-hpa-watcher/watcher.py) emits `pod_ready`
events whenever an autoscale-sample pod's Ready condition transitions to True.
This module loads those events from per-run watcher log JSONs (produced by
run-campaign.sh via `kubectl logs ... --since-time=...`) and provides a
`find_pod_ready_for_decision()` function that matches each scale-up decision
to its resulting pod_ready event.

Fallback rules (per methodology §2 and reviewer decision):

  * Scale-up decisions where no matching pod_ready event is found within 60 s:
    fall back to `T_decision + 60 s` and flag the row as `t_after_source =
    "fallback"`. The reader can see this happened.

  * Scale-down decisions: no new pod is created, so anchor at
    `T_decision + T_removal_settle` where T_removal_settle = 30 s (methodology
    §2). Flag as `t_after_source = "scale_down"`.

Public API:
    load_pod_ready_events(events_dir_or_files)
    find_pod_ready_for_decision(decision_ts, direction, events, ...)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


# ============================================================================
# Configurable constants (mirror the methodology doc)
# ============================================================================

# Maximum time-gap between a scale-up decision and the pod_created_at of the
# resulting pod. Kubernetes typically creates the new pod within 1-3 seconds
# of the HPA scale-up API call; allowing 30 s gives comfortable margin for
# scheduler load, image pull, etc.
MAX_DECISION_TO_POD_CREATED_SECONDS = 30

# If no matching pod_ready event is found for a scale-up decision within
# this many seconds after T_decision, fall back to T_decision + FALLBACK_S.
FALLBACK_SECONDS_FOR_SCALE_UP = 60

# For scale-down decisions there is no new pod. Use this offset from
# T_decision to allow terminating pods to complete their preStop grace and
# drain in-flight requests.
SCALE_DOWN_SETTLE_SECONDS = 30


# ============================================================================
# Timestamp parsing (matches compute_ses.py's parse_iso)
# ============================================================================

def parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 timestamp string to a UTC datetime.

    Robust to variable-length microseconds and 'Z' vs '+00:00' suffixes.
    """
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.+?\.)(\d+)(.+)$", s)
    if m:
        micros = m.group(2)[:6].ljust(6, "0")
        s = m.group(1) + micros + m.group(3)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


# ============================================================================
# Event loading
# ============================================================================

def load_pod_ready_events(sources) -> list[dict]:
    """Load pod_ready events from one or more watcher-event JSON files.

    `sources` may be:
      - a single Path or str to a file
      - a directory Path (all *-events-*.json inside are loaded)
      - an iterable of Paths/strs

    Each returned event is a dict with the keys emitted by the watcher's
    pod_watcher_loop:
        event_type, detected_at, namespace, pod_name,
        pod_created_at, pod_ready_at, app_label

    Returns them sorted by pod_ready_at (oldest first).
    """
    if isinstance(sources, (str, Path)):
        p = Path(sources)
        if p.is_dir():
            files: list[Path] = sorted(p.glob("*-events-*.json"))
        else:
            files = [p]
    else:
        files = [Path(x) for x in sources]

    events: list[dict] = []
    for f in files:
        if not f.exists():
            continue
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                # Watcher writes JSONL (one JSON object per line). The
                # run-campaign.sh capture also produces JSONL via kubectl
                # logs, but a leading `[watcher] loaded ...` non-JSON line
                # may appear — skip anything that doesn't start with '{'.
                if not line.startswith("{"):
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("event_type") == "pod_ready":
                    events.append(evt)

    events.sort(key=lambda e: e.get("pod_ready_at") or "")
    return events


# ============================================================================
# Matching decisions to pod_ready events
# ============================================================================

def find_pod_ready_for_decision(
    decision_ts: datetime,
    direction: str,
    pod_ready_events: list[dict],
) -> tuple[datetime, str, str | None]:
    """Return the T for the SES after-window anchor.

    Parameters
    ----------
    decision_ts : datetime
        T_decision — when the HPA emitted this scaling decision.
    direction : str
        "up" for a scale-up decision, "down" for a scale-down.
    pod_ready_events : list[dict]
        As returned by load_pod_ready_events().

    Returns
    -------
    (t_after_anchor, source, pod_name)
        t_after_anchor : datetime
            The UTC timestamp to use as the START of the SES after-window.
        source : str
            One of:
              "pod_ready" — a matching pod_ready event was found
              "fallback"  — no pod_ready found; used T_decision + 60 s
              "scale_down" — direction was "down"; used T_decision + 30 s
        pod_name : str | None
            The name of the pod whose ready-transition was used
            (or None for fallback / scale_down).
    """
    if direction == "down":
        return (
            decision_ts + timedelta(seconds=SCALE_DOWN_SETTLE_SECONDS),
            "scale_down",
            None,
        )

    # Scale-up: find the pod_ready event whose pod_created_at is closest to
    # and immediately after (or slightly before) the decision_ts.
    #
    # Kubernetes emits the Pod object within milliseconds of the HPA API call
    # that raised .spec.replicas, so pod_created_at should be very close to
    # decision_ts. We allow a small negative slack for clock skew.
    best_evt = None
    best_gap = None

    for evt in pod_ready_events:
        try:
            pod_created = parse_iso(evt["pod_created_at"])
            pod_ready = parse_iso(evt["pod_ready_at"])
        except (KeyError, ValueError):
            continue

        gap = (pod_created - decision_ts).total_seconds()

        # Skip pods created too far before the decision (unrelated to this
        # scale-up) or too far after (would have been triggered by a later
        # decision).
        if gap < -5:
            continue
        if gap > MAX_DECISION_TO_POD_CREATED_SECONDS:
            continue

        # Sanity: the pod's ready-transition should be AT or AFTER the
        # decision. If it's before, the pod already existed and was
        # re-reporting Ready — skip.
        if pod_ready < decision_ts:
            continue

        # Prefer the smallest positive gap.
        if best_gap is None or abs(gap) < abs(best_gap):
            best_evt = evt
            best_gap = gap

    if best_evt is not None:
        return (
            parse_iso(best_evt["pod_ready_at"]),
            "pod_ready",
            best_evt.get("pod_name"),
        )

    # Fallback — no matching pod_ready found.
    return (
        decision_ts + timedelta(seconds=FALLBACK_SECONDS_FOR_SCALE_UP),
        "fallback",
        None,
    )


# ============================================================================
# CLI: quick sanity check when run directly
# ============================================================================

def _main():
    """Print a summary of pod_ready events found under results/."""
    import sys

    if len(sys.argv) < 2:
        root = Path(__file__).resolve().parent.parent / "results"
    else:
        root = Path(sys.argv[1])

    events = load_pod_ready_events(root)
    print(f"Loaded {len(events)} pod_ready events from {root}")
    if events[:3]:
        print("\nFirst 3:")
        for e in events[:3]:
            print(f"  {e.get('pod_name')}  created={e.get('pod_created_at')}"
                  f"  ready={e.get('pod_ready_at')}")


if __name__ == "__main__":
    _main()
