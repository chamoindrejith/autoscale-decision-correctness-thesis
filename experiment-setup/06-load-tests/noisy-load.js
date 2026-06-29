// Noisy Load — highly variable but low *average* load.
// Purpose (per research PDF): study oscillations, instability, ineffective scaling.
//
// Run:
//   k6 run -e TARGET_URL=http://<DROPLET_IP>:30080/api/compute noisy-load.js
//
// Shape: random small spikes every few seconds over 8 minutes.

import http from 'k6/http';
import { sleep, check } from 'k6';

const BASE = __ENV.TARGET_URL || 'http://localhost:30080/api/compute';
const N    = __ENV.N || '50000';            // calibrated per-request cost (~29ms CPU)
const URL  = `${BASE}?n=${N}`;

export const options = {
  scenarios: {
    noisy: {
      executor: 'ramping-arrival-rate',
      startRate: 4,
      timeUnit: '1s',
      preAllocatedVUs: 30,
      maxVUs: 50,
      stages: [
        // Calibrated staccato pattern: brief peaks above 30% CPU threshold,
        // troughs below. Tests whether HPA flaps on transient noise.
        { duration: '30s', target: 4 },     // baseline trough
        { duration: '10s', target: 22 },    // brief spike
        { duration: '20s', target: 4 },
        { duration: '15s', target: 30 },    // bigger spike
        { duration: '20s', target: 6 },
        { duration: '10s', target: 25 },    // spike
        { duration: '30s', target: 4 },
        { duration: '15s', target: 35 },    // largest spike
        { duration: '20s', target: 5 },
        { duration: '20s', target: 22 },
        { duration: '30s', target: 4 },
        { duration: '15s', target: 28 },
        { duration: '30s', target: 4 },
        { duration: '20s', target: 18 },
        { duration: '5m',  target: 0 },     // long tail (allows 5-min scale-down stabilization)
      ],
    },
  },
};

export default function () {
  const res = http.get(URL);
  check(res, { 'status 200': (r) => r.status === 200 });
}
