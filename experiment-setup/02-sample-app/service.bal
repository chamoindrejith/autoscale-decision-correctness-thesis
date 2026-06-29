// ============================================================================
// Ballerina sample workload for the HPA correctness research.
//
// Exposes:
//   GET  /api/health            -> liveness probe
//   GET  /api/compute?n=NNN     -> CPU-intensive handler that takes ~proportional
//                                  time. This is what your load tests hit.
//   GET  /api/light             -> cheap endpoint, no meaningful CPU (for control runs)
//
// Every request emits a structured log line like:
//   {"timestamp":"2026-...Z","level":"INFO","msg":"request completed",
//    "action":"compute","timeTakenMs":42.1,"traceId":"..."}
//
// This is the "time-taken audit log" described in the research PDF.
// In addition, Ballerina's built-in observability exports Prometheus metrics
// on port 9797 at /metrics (response_time histograms, throughput, errors).
// ============================================================================

import ballerina/http;
import ballerina/log;
import ballerina/time;
import ballerina/uuid;

// ---------------------------------------------------------------------------
// Configurable values (see Config.toml)
// ---------------------------------------------------------------------------
configurable int defaultIterations = 500000;

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------
service /api on new http:Listener(9000) {

    # Liveness/readiness probe — always responds fast.
    resource function get health() returns json {
        return {status: "UP"};
    }

    # Lightweight endpoint (baseline / control).
    resource function get light() returns json {
        string traceId = uuid:createType4AsString();
        log:printInfo("request completed",
            action = "light",
            timeTakenMs = 0,
            traceId = traceId);
        return {result: "ok"};
    }

    # CPU-intensive endpoint. Repeats an arithmetic loop `n` times.
    # Clients can tune `n` to produce heavier or lighter requests.
    resource function get compute(int? n = ()) returns json {
        int iterations = n ?: defaultIterations;
        string traceId = uuid:createType4AsString();
        time:Utc startTime = time:utcNow();

        // CPU burn. Simple loop that the JIT can't completely optimize away.
        int acc = 0;
        foreach int i in 0 ... iterations {
            acc = acc + (i * 7) % 13;
        }

        time:Utc endTime = time:utcNow();
        decimal elapsedSeconds = time:utcDiffSeconds(endTime, startTime);
        decimal elapsedMs = elapsedSeconds * 1000;

        log:printInfo("request completed",
            action = "compute",
            iterations = iterations,
            timeTakenMs = elapsedMs,
            traceId = traceId);

        return {
            result: acc,
            iterations: iterations,
            elapsedMs: elapsedMs
        };
    }
}
