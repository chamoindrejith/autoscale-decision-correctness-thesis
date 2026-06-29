// Step Load — sudden jump from idle to a sustained load.
// Purpose (per research PDF): evaluate scaling reaction time. Measures SRD.
//
// Run:
//   k6 run -e TARGET_URL=http://<DROPLET_IP>:30080/api/compute step-load.js
//
// Shape:
//
//   VUs
//     ^
//  50 |          ________________________________________________
//     |         |
//   0 |_________|________________________________________________
//     +---------|----------------------------------------------> time
//       1 min   ^                       10 min
//               step up

import http from 'k6/http';
import { sleep, check } from 'k6';

const BASE = __ENV.TARGET_URL || 'http://localhost:30080/api/compute';
const N    = __ENV.N || '50000';            // calibrated per-request cost (~29ms CPU)
const URL  = `${BASE}?n=${N}`;

export const options = {
  stages: [
    { duration: '1m',  target: 0 },     // 1 min idle baseline
    { duration: '15s', target: 15 },    // step up to 15 VUs (calibrated)
    { duration: '8m',  target: 15 },    // hold for 8 minutes
    { duration: '15s', target: 0 },     // step down
    { duration: '1m',  target: 0 },     // tail measurement
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'],  // fail if p95 > 2s
  },
};

export default function () {
  const res = http.get(URL);
  check(res, { 'status 200': (r) => r.status === 200 });
  sleep(0.1); // ~10 req/s per VU
}
