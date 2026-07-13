#!/bin/bash
#
# run-campaign.sh — Orchestrates unattended workload runs for the autoscaling research campaign.
#
# Usage:
#   ./run-campaign.sh <pattern> <start_run> <end_run>
#
# Examples:
#   ./run-campaign.sh step  6 20    # Runs Step #6 through Step #20
#   ./run-campaign.sh burst 6 20    # Runs Burst #6 through Burst #20
#
# What it does for each iteration:
#   1. Verifies the cluster is healthy (HPA reporting real metrics, memory available)
#   2. Waits until the cluster is fully idle (HPA at 2 replicas, low CPU)
#   3. Runs k6 with the appropriate load script
#   4. Captures HPA watcher events for this run window into a separate JSON
#   5. Checks for quality issues (dropped iterations, failed checks)
#   6. Waits for HPA to scale back down to 2 replicas before next run
#   7. Every 4 runs, reboots the droplet to prevent cumulative memory pressure
#
set -euo pipefail

# ============================================================================
# CONFIGURATION
# ============================================================================
# These four values are sanitized in the GitHub copy for privacy. Set them
# for local runs by exporting env vars (recommended) or filling them below.
# Overriding via env vars keeps secrets out of git.
SSH_HOST="${SSH_HOST:-}"                                # SSH alias from ~/.ssh/config
NAMESPACE="${NAMESPACE:-}"
TARGET_URL="${TARGET_URL:-}"
K6_PROMETHEUS_RW_SERVER_URL="${K6_PROMETHEUS_RW_SERVER_URL:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results"
LOGS_DIR="${SCRIPT_DIR}/logs"

# Reboot cadence.
# Original 80-run campaign rebooted every 4 runs to relieve memory pressure
# on the 4 GiB droplet, which had the unintended side effect of resetting
# the JVM's JIT compilation state and injecting cold-JIT variance into the
# dataset (see analysis/slo_risk_and_ses_methodology.md §4 threats).
# For the new campaign the droplet has 8 GiB and JIT warm-up is controlled
# via the 3 warm-up discarded runs per pattern — reboots are disabled so
# the JVM stays continuously warm across the counted 20 runs.
# Set to a very high value rather than removing the reboot code entirely,
# so the safety net still triggers if the campaign runs for weeks.
REBOOT_EVERY_N_RUNS=1000

# Stabilization thresholds
IDLE_REPLICAS=2          # HPA should be at minReplicas before next run
IDLE_CPU_THRESHOLD=5     # HPA target CPU percentage should be below this to be "idle"
IDLE_WAIT_SECONDS=60     # Must be idle for at least this long before starting a run
# Step's scale-down sequence (5→4→3→2) typically takes ~12-13 minutes
# because of HPA's default 5-minute scale-down stabilization window.
# Set the timeout generously so we capture the FULL scale-down trajectory.
STABILIZATION_TIMEOUT=900  # Give up waiting after 15 minutes

# Pre-flight health thresholds
MIN_MEMORY_AVAILABLE_MB=800   # Refuse to start a run if free memory below this

# ============================================================================
# SETUP & ARGUMENT PARSING
# ============================================================================
if [ $# -ne 3 ]; then
    echo "Usage: $0 <pattern> <start_run> <end_run>"
    echo "  pattern: step | burst | ramp | noisy"
    echo "  Example: $0 step 6 20"
    exit 1
fi

PATTERN=$1
START_RUN=$2
END_RUN=$3

# Validate pattern
case "$PATTERN" in
    step|burst|ramp|noisy) ;;
    *) echo "ERROR: pattern must be step, burst, ramp, or noisy"; exit 1 ;;
esac

# Validate the load script exists
LOAD_SCRIPT="${SCRIPT_DIR}/${PATTERN}-load.js"
if [ ! -f "$LOAD_SCRIPT" ]; then
    echo "ERROR: load script not found at $LOAD_SCRIPT"
    exit 1
fi

mkdir -p "$RESULTS_DIR" "$LOGS_DIR"

# Master log file for this batch
BATCH_START_TS=$(date +%Y%m%d-%H%M%S)
BATCH_LOG="${LOGS_DIR}/${PATTERN}-batch-${BATCH_START_TS}.log"

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================
log() {
    local msg="[$(date +'%H:%M:%S')] $1"
    echo "$msg" | tee -a "$BATCH_LOG"
}

# Kubeconfig path on the droplet (chamodi's per-user copy, not the root-only system one)
REMOTE_KUBECONFIG='$HOME/.kube/config'

# LOCAL_MODE: if SSH_HOST is empty or "localhost", assume we're running on the
# droplet itself (or same host as k8s) and skip the SSH wrapper. This is used
# for droplet-native campaigns where network flakiness between Mac and droplet
# would otherwise disrupt long batches.
if [ -z "$SSH_HOST" ] || [ "$SSH_HOST" = "localhost" ]; then
    LOCAL_MODE=true
else
    LOCAL_MODE=false
fi

# Run a kubectl command on the cluster.
# In remote mode: wraps in SSH so it works from a Mac client.
# In local mode: calls kubectl directly (assumes KUBECONFIG is exported by
# the caller or by the environment).
#
# Note: callers pass all args as ONE string (e.g. remote_kubectl "get pods ..."),
# so in local mode we `eval` to word-split the string into distinct kubectl
# arguments. Without eval, kubectl sees "get pods -n ..." as a single command
# name and errors with `unknown command`.
remote_kubectl() {
    if [ "$LOCAL_MODE" = "true" ]; then
        eval "kubectl $*" 2>&1
    else
        ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$SSH_HOST" \
            "export KUBECONFIG=$REMOTE_KUBECONFIG && kubectl $*" 2>&1
    fi
}

# Run an arbitrary shell command.
# In remote mode: wraps in SSH.
# In local mode: runs via `eval` for the same reason (single-string arg needs
# shell parsing, not literal execution).
remote_exec() {
    if [ "$LOCAL_MODE" = "true" ]; then
        eval "$*" 2>&1
    else
        ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$SSH_HOST" "$*" 2>&1
    fi
}

# ============================================================================
# HEALTH CHECKS
# ============================================================================

# Returns 0 if cluster looks healthy, 1 otherwise.
# Echoes diagnostic info to stderr.
check_cluster_health() {
    log "Pre-flight health check..."

    # 0. NTP / clock synchronisation. SRD and SES both depend on the
    #    droplet's clock being aligned with the k6 client's clock. A
    #    silent drift of even a few seconds distorts the SRD numbers.
    #    Refuse to start a run if the droplet clock isn't synchronised.
    local ntp_ok
    ntp_ok=$(remote_exec "timedatectl show -p NTPSynchronized --value 2>/dev/null" | tr -d ' ')
    if [ "$ntp_ok" != "yes" ]; then
        log "FAIL: droplet clock is NOT NTP-synchronised (timedatectl says '$ntp_ok')"
        log "      Fix with: sudo timedatectl set-ntp true; sudo systemctl restart systemd-timesyncd"
        return 1
    fi
    log "  Clock sync OK (NTPSynchronized=yes)"

    # 1. Can we reach the cluster at all?
    if [ "$LOCAL_MODE" = "true" ]; then
        if ! kubectl version >/dev/null 2>&1; then
            log "FAIL: kubectl cannot reach the cluster (LOCAL_MODE)"
            return 1
        fi
    else
        if ! ssh -o ConnectTimeout=10 "$SSH_HOST" "echo ok" >/dev/null 2>&1; then
            log "FAIL: cannot SSH to droplet"
            return 1
        fi
    fi

    # 2. Get HPA status — must show real metrics, not <unknown>
    local hpa_output
    hpa_output=$(remote_kubectl "get hpa -n $NAMESPACE autoscale-sample-hpa --no-headers" || true)
    if [[ "$hpa_output" == *"<unknown>"* ]]; then
        log "FAIL: HPA reports <unknown> metrics — metrics-server not ready"
        log "      Output: $hpa_output"
        return 1
    fi
    log "  HPA OK: $hpa_output"

    # 3. Memory available — refuse to start if too low
    local mem_output mem_mb
    mem_output=$(remote_exec "free -m | awk '/^Mem:/ {print \$7}'" || echo "0")
    mem_mb=${mem_output//[!0-9]/}  # strip non-digits
    if [ -z "$mem_mb" ] || [ "$mem_mb" -lt "$MIN_MEMORY_AVAILABLE_MB" ]; then
        log "FAIL: memory available too low: ${mem_mb}MB (need >= ${MIN_MEMORY_AVAILABLE_MB}MB)"
        return 1
    fi
    log "  Memory OK: ${mem_mb}MB available"

    # 4. All pods Running
    local not_running
    not_running=$(remote_kubectl "get pods -n $NAMESPACE --no-headers" | awk '$3 != "Running" {print $1}' || true)
    if [ -n "$not_running" ]; then
        log "FAIL: pods not Running: $not_running"
        return 1
    fi
    log "  All pods Running"

    return 0
}

# Poll until HPA shows idle state (2 replicas, low CPU) for IDLE_WAIT_SECONDS continuously.
# Returns 0 on success, 1 on timeout.
wait_for_idle() {
    log "Waiting for HPA to stabilize at $IDLE_REPLICAS replicas, CPU < ${IDLE_CPU_THRESHOLD}%..."
    local idle_since=0
    local elapsed=0
    local poll_interval=15

    while [ $elapsed -lt $STABILIZATION_TIMEOUT ]; do
        # Sample HPA: "cpu: X%/30%, memory: Y%/75%   2  6  3  44d"
        local hpa_line
        hpa_line=$(remote_kubectl "get hpa -n $NAMESPACE autoscale-sample-hpa --no-headers" 2>/dev/null || echo "")

        # Extract CPU percentage and current replicas
        local cpu_pct replicas
        cpu_pct=$(echo "$hpa_line" | grep -oE 'cpu: *[0-9]+%' | head -1 | grep -oE '[0-9]+' || echo "999")
        replicas=$(echo "$hpa_line" | awk '{print $(NF-1)}')

        if [ "$replicas" = "$IDLE_REPLICAS" ] && [ "$cpu_pct" -lt "$IDLE_CPU_THRESHOLD" ]; then
            idle_since=$((idle_since + poll_interval))
            if [ $idle_since -ge $IDLE_WAIT_SECONDS ]; then
                log "  Idle confirmed (replicas=$replicas, CPU=${cpu_pct}%)"
                return 0
            fi
        else
            idle_since=0  # reset — not yet idle
        fi

        sleep $poll_interval
        elapsed=$((elapsed + poll_interval))
    done

    log "FAIL: stabilization timeout after ${STABILIZATION_TIMEOUT}s"
    return 1
}

# Reboot the droplet and wait for the cluster to come back healthy.
reboot_and_wait() {
    if [ "$LOCAL_MODE" = "true" ]; then
        log "SKIP REBOOT: LOCAL_MODE is set (rebooting our own host would kill the campaign)."
        log "  If a reboot is genuinely needed, set REBOOT_EVERY_N_RUNS higher"
        log "  and reboot manually between pattern batches."
        return 0
    fi
    log "=== REBOOT CYCLE (cumulative pressure prevention) ==="
    remote_exec "sudo reboot" || true  # connection will drop — that's expected

    log "  Droplet rebooting. Waiting 90 seconds before first reconnect attempt..."
    sleep 90

    # Try to reconnect for up to 5 minutes
    local attempts=0
    while [ $attempts -lt 20 ]; do
        if ssh -o ConnectTimeout=10 "$SSH_HOST" "echo back" >/dev/null 2>&1; then
            log "  SSH responsive after reboot."
            break
        fi
        sleep 15
        attempts=$((attempts + 1))
    done

    if [ $attempts -eq 20 ]; then
        log "FAIL: droplet did not come back after reboot"
        return 1
    fi

    # Wait for k3s to settle
    log "  Waiting 60 seconds for k3s to fully start..."
    sleep 60

    # Verify cluster is healthy
    if check_cluster_health; then
        log "=== REBOOT COMPLETE ==="
        return 0
    else
        log "FAIL: cluster not healthy after reboot"
        return 1
    fi
}

# ============================================================================
# RUN ONE ITERATION
# ============================================================================
run_one_iteration() {
    local run_num=$1
    local run_id=$(printf "%s-run-%02d" "$PATTERN" "$run_num")
    local ts=$(date +%Y%m%d-%H%M%S)
    # Post-audit: embed run_num in the k6 output filename so downstream
    # analysis (build_master_dataset.py, plot scripts) can parse the
    # correct run_num from the filename directly. Previous
    # timestamp-only naming relied on chronological directory sort which
    # got fragile if runs were re-executed after a failure.
    local k6_output="${RESULTS_DIR}/${PATTERN}-run-$(printf '%02d' "$run_num")-${ts}.json"
    local k6_summary="${LOGS_DIR}/${run_id}-${ts}-summary.txt"
    local events_output="${RESULTS_DIR}/${PATTERN}-events-$(printf '%02d' "$run_num")-${ts}.json"

    log ""
    log "######################################################################"
    log "# Starting ${run_id}"
    log "######################################################################"

    # Step 1: Pre-flight check
    if ! check_cluster_health; then
        log "ABORT ${run_id}: pre-flight check failed"
        return 1
    fi

    # Step 2: Wait for idle
    if ! wait_for_idle; then
        log "ABORT ${run_id}: stabilization timeout"
        return 1
    fi

    # Step 3: Mark the time so we can grab watcher events for just this window
    local k6_start_iso
    k6_start_iso=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    log "k6 starting at $k6_start_iso..."

    # Step 4: Run k6 (capture stdout for summary parsing)
    # Two outputs: JSON (per-request data for offline analysis) and
    # experimental-prometheus-rw (live metrics into Grafana during the run).
    # If K6_PROMETHEUS_RW_SERVER_URL is unset, RW output is skipped without
    # failing the run — JSON alone is sufficient for the analysis pipeline.
    local rw_args=()
    if [ -n "$K6_PROMETHEUS_RW_SERVER_URL" ]; then
        rw_args=(
            --out "experimental-prometheus-rw"
            -e "K6_PROMETHEUS_RW_SERVER_URL=$K6_PROMETHEUS_RW_SERVER_URL"
            -e "K6_PROMETHEUS_RW_TREND_STATS=p(50),p(95),p(99),max"
            -e "K6_PROMETHEUS_RW_STALE_MARKERS=true"
        )
    fi

    if k6 run \
        --out "json=$k6_output" \
        "${rw_args[@]}" \
        -e TARGET_URL="$TARGET_URL" \
        "$LOAD_SCRIPT" 2>&1 | tee "$k6_summary"; then
        log "k6 finished successfully"
    else
        log "WARN ${run_id}: k6 exited non-zero (may still be usable)"
    fi

    # Step 5: Wait for HPA scale-down before grabbing events (catches the full scale-down)
    log "Waiting for HPA scale-down to complete before capturing events..."
    if ! wait_for_idle; then
        log "WARN ${run_id}: scale-down stabilization timed out"
    fi

    # Step 6: Capture watcher events for this run's window
    log "Capturing watcher events since $k6_start_iso..."
    remote_kubectl "logs -n $NAMESPACE -l app=hpa-watcher --since-time=$k6_start_iso" > "$events_output" 2>&1 || true
    local event_count
    event_count=$(grep -c '"event_type":' "$events_output" 2>/dev/null || echo "0")
    log "  Captured $event_count events to $(basename "$events_output")"

    # Step 7: Quality check — flag bad runs but DON'T auto-exclude (human decides)
    # Use tight regex to target the actual numeric value, not the leading dots in k6's output.
    # k6 output looks like: "checks_failed......: 0.00%   0 out of 47220"
    local dropped failed
    dropped=$(grep 'dropped_iterations' "$k6_summary" 2>/dev/null \
              | grep -oE '[0-9]+' | head -1 || true)
    dropped="${dropped:-0}"
    failed=$(grep 'checks_failed' "$k6_summary" 2>/dev/null \
             | grep -oE '[0-9]+\.[0-9]+%' | head -1 | tr -d '%' || true)
    failed="${failed:-0}"
    local quality_note=""
    # Only flag if drops > 0 OR failure rate is non-zero (treat "0", "0.00", "0.0" as clean)
    local failed_is_zero=0
    case "$failed" in
        0|0.0|0.00|0.000) failed_is_zero=1 ;;
    esac
    if [ "$dropped" != "0" ] || [ "$failed_is_zero" -eq 0 ]; then
        quality_note=" *** QUALITY FLAG: dropped=${dropped}, failed=${failed}% ***"
    fi

    log "Completed ${run_id}: events=${event_count}${quality_note}"
    return 0
}

# ============================================================================
# MAIN LOOP
# ============================================================================
log "============================================================"
log "Starting campaign batch: $PATTERN runs $START_RUN through $END_RUN"
log "  Total runs: $((END_RUN - START_RUN + 1))"
log "  Reboot every: $REBOOT_EVERY_N_RUNS runs"
log "  Batch log: $BATCH_LOG"
log "============================================================"

# Initial health check before starting
if ! check_cluster_health; then
    log "FATAL: initial health check failed. Investigate before retrying."
    exit 1
fi

runs_since_reboot=0

for ((i=START_RUN; i<=END_RUN; i++)); do
    if run_one_iteration "$i"; then
        runs_since_reboot=$((runs_since_reboot + 1))
    else
        log "Iteration $i failed — continuing to next iteration"
    fi

    # Reboot every N runs, but not after the last iteration
    if [ "$runs_since_reboot" -ge "$REBOOT_EVERY_N_RUNS" ] && [ "$i" -lt "$END_RUN" ]; then
        if reboot_and_wait; then
            runs_since_reboot=0
        else
            log "FATAL: reboot recovery failed. Stopping batch."
            exit 2
        fi
    fi
done

log "============================================================"
log "BATCH COMPLETE: $PATTERN runs $START_RUN through $END_RUN"
log "Check $LOGS_DIR for individual run summaries"
log "Check $RESULTS_DIR for k6 outputs and watcher events"
log "============================================================"

# Copy the durable JSONL from the watcher's PersistentVolume as a
# post-batch audit snapshot. This gives the analysis pipeline a
# tamper-proof source of truth (matching build_master_dataset.py's
# `hpa-events-full-post-{pattern}.jsonl` search) without relying on
# `kubectl logs`, which loses history across watcher pod restarts.
log ""
log "Snapshotting durable watcher JSONL to results/..."
snapshot_dst="${RESULTS_DIR}/hpa-events-full-post-${PATTERN}.jsonl"
watcher_pod=$(remote_kubectl "get pod -n $NAMESPACE -l app=hpa-watcher -o name 2>/dev/null" | head -1)
if [ -n "$watcher_pod" ]; then
    # remote_kubectl wraps in SSH when needed. For kubectl cp we must run
    # locally (in LOCAL_MODE) or via the SSH host (in remote mode) so the
    # file ends up in $RESULTS_DIR on THIS host.
    if [ "$LOCAL_MODE" = "true" ]; then
        kubectl cp -n "$NAMESPACE" \
            "${watcher_pod#pod/}:/data/hpa-events.jsonl" \
            "$snapshot_dst" >/dev/null 2>&1 \
            && log "  Wrote $(basename "$snapshot_dst") ($(wc -l < "$snapshot_dst") lines)" \
            || log "  WARN: kubectl cp failed for $watcher_pod"
    else
        ssh -o ConnectTimeout=10 "$SSH_HOST" \
            "export KUBECONFIG=$REMOTE_KUBECONFIG && \
             kubectl cp -n $NAMESPACE ${watcher_pod#pod/}:/data/hpa-events.jsonl /tmp/hpa-events-full-post-${PATTERN}.jsonl" \
            >/dev/null 2>&1 \
            && scp -o ConnectTimeout=10 "$SSH_HOST:/tmp/hpa-events-full-post-${PATTERN}.jsonl" \
                    "$snapshot_dst" >/dev/null 2>&1 \
            && log "  Wrote $(basename "$snapshot_dst") ($(wc -l < "$snapshot_dst") lines)" \
            || log "  WARN: kubectl cp/scp failed for $watcher_pod"
    fi
else
    log "  WARN: could not locate watcher pod for JSONL snapshot"
fi
