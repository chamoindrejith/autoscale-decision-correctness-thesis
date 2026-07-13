// calib-probe.js — calibration probe (NOT one of the four research patterns).
//
// Purpose: find the load level where the cluster scales but can still recover,
// so we can size the Step/Burst/Ramp/Noisy patterns correctly for the CURRENT
// cluster: 4 vCPU / 8 GiB node with maxReplicas=10 and a 75% HPA CPU target.
//
// The stages below span 5..40 VUs (widened from the earlier 5..20 range to
// cover the higher post-recalibration peaks expected on this cluster). It
// steps the VU count up in stages and HOLDS at each level for 2 minutes so
// CPU stabilises and the HPA has time to react. Watch your "kubectl get
// hpa,pods" window while this runs and note:
//   - at which VU level CPU crosses 75% and replicas start climbing
//   - whether p95 latency (printed by k6) stays healthy (say < 500 ms —
//     that's the SLO threshold used by the SRD analysis)
//   - the VU level at which latency crosses 500 ms sustained (that's the
//     SLO-breach point we want the patterns to reach or approach)
//
// Historical context: this probe was originally sized against a 2-vCPU node
// with maxReplicas=6 and a 30% CPU target. The counted campaign showed the
// pilot-era workload amplitudes are too small to meaningfully stress the
// current cluster (only 1 of 182 decisions with a sustained SLO breach at
// 500 ms). RE-RUN this probe on the current cluster before adjusting the
// four workload scripts.
//
// Per-request cost: n=50000 (~29 ms CPU each) — same as the four patterns
// so probe results directly inform them. Override with -e N=... if needed.
//
// Run (from your device):
//   k6 run -e TARGET_URL="http://<DROPLET_IP>:30080/api/compute" calib-probe.js

import http from 'k6/http';
import { sleep, check } from 'k6';

const BASE = __ENV.TARGET_URL || 'http://localhost:30080/api/compute';
const N    = __ENV.N || '50000';
const URL  = `${BASE}?n=${N}`;

export const options = {
  stages: [
    { duration: '1m',  target: 0 },    // 0–1 min: idle baseline
    { duration: '20s', target: 5 },    // ramp to 5 VUs
    { duration: '2m',  target: 5 },    // hold 5 VUs
    { duration: '20s', target: 15 },   // ramp to 15 VUs
    { duration: '2m',  target: 15 },   // hold 15 VUs
    { duration: '20s', target: 25 },   // ramp to 25 VUs
    { duration: '2m',  target: 25 },   // hold 25 VUs
    { duration: '20s', target: 40 },   // ramp to 40 VUs
    { duration: '2m',  target: 40 },   // hold 40 VUs
    { duration: '30s', target: 0 },    // ramp down
    { duration: '1m',  target: 0 },    // tail
  ],
  // No hard threshold failure here — this is exploratory calibration.
  thresholds: {},
};

export default function () {
  const res = http.get(URL);
  check(res, { 'status 200': (r) => r.status === 200 });
  sleep(0.1);
}
