// Gradual Ramp Load — slow, steady climb.
// Purpose (per research PDF): analyze threshold sensitivity and delayed reactions.
//
// Run:
//   k6 run -e TARGET_URL=http://<DROPLET_IP>:30080/api/compute ramp-load.js
//
// Shape:
//
//  VUs
//    ^
// 80 |                          ________
//    |                        /
//    |                      /
//    |                    /
//    |                  /
//  0 |________________/________________________
//    +---------|-----------------|----------> time
//              1m                 6m
//
// Post-audit recalibration (July 2026): ramp peak raised from 20 to 30
// VUs against the current 4 vCPU / 75% HPA target cluster. Ramp reaches
// ~75% CPU around 20-22 VUs (mid-way through the ramp) and end-state
// ~85% CPU at 30 VUs (with HPA at ~6-7 replicas). Gradual rise of
// ~3.75 VU/min gives HPA time to scale pre-emptively, so most Ramp
// decisions should land in the Correct & Timely bucket.

import http from 'k6/http';
import { sleep, check } from 'k6';

const BASE = __ENV.TARGET_URL || 'http://localhost:30080/api/compute';
const N    = __ENV.N || '50000';            // calibrated per-request cost (~29ms CPU)
const URL  = `${BASE}?n=${N}`;

export const options = {
  stages: [
    { duration: '30s', target: 0 },     // idle baseline
    { duration: '8m',  target: 30 },    // slow linear ramp 0->30 VUs (~3.75 VU/min)
    { duration: '30s', target: 0 },     // quick ramp down
    { duration: '4m',  target: 0 },     // tail to observe scale-down
  ],
};

export default function () {
  const res = http.get(URL);
  check(res, { 'status 200': (r) => r.status === 200 });
  sleep(0.1);
}
