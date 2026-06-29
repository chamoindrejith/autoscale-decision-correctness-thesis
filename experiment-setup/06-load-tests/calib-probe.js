// calib-probe.js — calibration probe (NOT one of the four research patterns).
//
// Purpose: find the load level where the cluster scales but can still recover,
// so we can size the Step/Burst/Ramp/Noisy patterns correctly for this 2-vCPU
// node with maxReplicas=6 and a 30% CPU target.
//
// It steps the virtual-user (VU) count up in stages and HOLDS at each level for
// 2 minutes so CPU stabilises and the HPA has time to react. Watch your
// "kubectl get hpa,pods" window while this runs and note:
//   - at which VU level CPU crosses 30% and replicas start climbing
//   - whether p95 latency (printed by k6) stays healthy (say < 1s) at each level
//   - the VU level at which latency blows up (that's the saturation point)
//
// Lighter per-request cost than the patterns: n=50000 (~29ms CPU each) instead
// of the default 500000. Override with -e N=... if you want.
//
// Run (from your device):
//   k6 run -e TARGET_URL="" calib-probe.js
//
// (n is appended automatically; you can override with -e N=30000 etc.)

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
    { duration: '20s', target: 10 },   // ramp to 10 VUs
    { duration: '2m',  target: 10 },   // hold 10 VUs
    { duration: '20s', target: 15 },   // ramp to 15 VUs
    { duration: '2m',  target: 15 },   // hold 15 VUs
    { duration: '20s', target: 20 },   // ramp to 20 VUs
    { duration: '2m',  target: 20 },   // hold 20 VUs
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
