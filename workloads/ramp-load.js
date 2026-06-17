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

import http from 'k6/http';
import { sleep, check } from 'k6';

const BASE = __ENV.TARGET_URL || 'http://localhost:30080/api/compute';
const N    = __ENV.N || '50000';            // calibrated per-request cost (~29ms CPU)
const URL  = `${BASE}?n=${N}`;

export const options = {
  stages: [
    { duration: '30s', target: 0 },     // idle baseline
    { duration: '8m',  target: 20 },    // slow linear ramp 0->20 VUs (~2.5 VU/min)
    { duration: '30s', target: 0 },     // quick ramp down
    { duration: '4m',  target: 0 },     // tail to observe scale-down
  ],
};

export default function () {
  const res = http.get(URL);
  check(res, { 'status 200': (r) => r.status === 200 });
  sleep(0.1);
}
