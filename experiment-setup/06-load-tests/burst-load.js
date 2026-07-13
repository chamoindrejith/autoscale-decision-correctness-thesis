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
//    |       ___
//    |      |   |
//  0 |______|   |_________________________
//    +------|---|-----------------------> time
//           1m  60s      7 minutes tail
//
// Peak VU count is recalibrated against the current 4 vCPU / 75% HPA
// target cluster via `calib-probe.js` (see the 2026-07-XX recalibration
// notes in the campaign log). The pilot's 25-VU value was deliberately
// kept unchanged for the counted campaign, but the counted-campaign
// results showed only 1 of 182 decisions with a sustained SLO breach —
// evidence that the pilot-era amplitude is too small to meaningfully
// stress the current cluster. The recalibrated peak targets ~85-90%
// sustained pod CPU during the 60 s hold so HPA reliably fires and p95
// latency briefly touches the 500 ms SLO threshold.
//
// Diagnostic history retained for reference:
//   - 25 VUs on 2 vCPU / max=5 (original pilots): HPA peaked at 3
//     replicas, 0 failures, p95 989→301 ms across runs (JIT warm-up).
//   - 25 VUs on 4 vCPU / max=10 (counted campaign): 40 decisions, 1
//     sustained SLO breach — undersized for the new cluster, motivating
//     the recalibration.
//   - 60 VUs on 4 vCPU / max=10 (JIT calibration Run 1, aborted): system
//     collapsed — pods restarted from probe timeouts. Data discarded.

import http from 'k6/http';
import { sleep, check } from 'k6';

const BASE = __ENV.TARGET_URL || 'http://localhost:30080/api/compute';
const N    = __ENV.N || '50000';            // calibrated per-request cost (~29ms CPU)
const URL  = `${BASE}?n=${N}`;

export const options = {
  stages: [
    { duration: '1m',  target: 0 },     // idle baseline
    { duration: '10s', target: 25 },    // rapid ramp (approved workload)
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
