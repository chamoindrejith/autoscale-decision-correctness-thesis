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
//
// VU count is 25 (peak) — the value approved by the co-supervisor and used
// throughout the pilot campaign. Documented decision (2026-07-03): keep the
// workload script unchanged and let HPA behavior be characterised at the
// workload's natural saturation point, rather than tune the workload to
// force ceiling saturation on the (now larger) max=10 HPA. The 4-bucket
// distribution reported by the campaign will therefore reflect the
// approved workload's actual demand profile — the same workload
// profile-comparison that was originally sanctioned. Fixed HPA policy is
// prioritised over exhaustive bucket coverage.
//
// Diagnostic history retained for reference:
//   - 25 VUs on 2 vCPU / max=5 (original pilots): HPA peaked at 3
//     replicas, 0 failures, p95 989→301 ms across runs (JIT warm-up).
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
