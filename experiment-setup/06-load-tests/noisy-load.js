// Noisy Load — highly variable but low *average* load.
// Purpose (per research PDF): study oscillations, instability, ineffective scaling.
//
// Run:
//   k6 run -e TARGET_URL=http://<DROPLET_IP>:30080/api/compute noisy-load.js
//
// Shape: brief high-amplitude spikes over 8 minutes to test whether HPA
// flaps on transient noise.
//
// Post-audit recalibration (July 2026): peak amplitudes raised roughly
// 4-5× against the current 4 vCPU / 75% HPA target cluster. The pilot
// amplitudes (22-35 req/s peaks) were calibrated for the earlier 30%
// HPA target and produced ZERO HPA decisions across 20 counted Noisy
// runs — the peaks were too small to sustain enough CPU pressure on the
// 4 vCPU node to cross the 75% averaged threshold before the metrics-
// server's ~15 s averaging window resolved them.
//
// New amplitudes: peaks 80-150 req/s, troughs 6-10 req/s. At 150 req/s
// on the initial 2 replicas, per-pod CPU briefly approaches 90% (well
// above 75% instantaneously), but the 10-15 s peak duration is at the
// edge of the metrics-server averaging window — some spikes will
// trigger HPA, some won't. That mixed outcome is the flapping-
// resistance property the Noisy pattern is designed to characterise.
//
// preAllocatedVUs raised from 30 to 60 (and maxVUs to 100) to sustain
// the higher arrival rates without VU starvation.

import http from 'k6/http';
import { sleep, check } from 'k6';

const BASE = __ENV.TARGET_URL || 'http://localhost:30080/api/compute';
const N    = __ENV.N || '50000';            // calibrated per-request cost (~29ms CPU)
const URL  = `${BASE}?n=${N}`;

export const options = {
  scenarios: {
    noisy: {
      executor: 'ramping-arrival-rate',
      startRate: 6,
      timeUnit: '1s',
      preAllocatedVUs: 60,
      maxVUs: 100,
      stages: [
        // Recalibrated staccato pattern: brief peaks above 75% CPU
        // threshold on 2 replicas, troughs below. Tests whether HPA
        // flaps on transient noise.
        { duration: '30s', target: 6 },     // baseline trough
        { duration: '10s', target: 100 },   // brief small spike
        { duration: '20s', target: 6 },
        { duration: '15s', target: 130 },   // medium spike
        { duration: '20s', target: 10 },
        { duration: '10s', target: 110 },   // small-medium spike (brief)
        { duration: '30s', target: 6 },
        { duration: '15s', target: 150 },   // largest spike
        { duration: '20s', target: 8 },
        { duration: '20s', target: 100 },   // sustained mid-amplitude
        { duration: '30s', target: 6 },
        { duration: '15s', target: 120 },   // medium spike
        { duration: '30s', target: 6 },
        { duration: '20s', target: 80 },    // low-amplitude tail spike
        { duration: '5m',  target: 0 },     // long tail (allows 5-min scale-down stabilization)
      ],
    },
  },
};

export default function () {
  const res = http.get(URL);
  check(res, { 'status 200': (r) => r.status === 200 });
}
