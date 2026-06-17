// Short Burst Load — brief spike, then back to idle.
// Purpose (per research PDF): detect unnecessary scaling / overreaction.
//
// Run:
//   k6 run -e TARGET_URL=http://<DROPLET_IP>:30080/api/compute burst-load.js
//
// Shape:
//
//  VUs
//    ^
// 60 |       ___
//    |      |   |
//  0 |______|   |_________________________
//    +------|---|-----------------------> time
//           1m  90s      5 minutes tail

import http from 'k6/http';
import { sleep, check } from 'k6';

const BASE = __ENV.TARGET_URL || 'http://localhost:30080/api/compute';
const N    = __ENV.N || '50000';            // calibrated per-request cost (~29ms CPU)
const URL  = `${BASE}?n=${N}`;

export const options = {
  stages: [
    { duration: '1m',  target: 0 },     // idle baseline
    { duration: '10s', target: 25 },    // rapid ramp (calibrated)
    { duration: '60s', target: 25 },    // short burst (HPA gets one chance to react)
    { duration: '10s', target: 0 },     // back to idle
    { duration: '7m',  target: 0 },     // observe scale-down & any trailing decisions
  ],
};

export default function () {
  const res = http.get(URL);
  check(res, { 'status 200': (r) => r.status === 200 });
  sleep(0.1);
}
