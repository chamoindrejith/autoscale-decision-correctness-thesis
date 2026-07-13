# Operational Definitions: T_SLO_risk and SES Windows

**Project.** Observability for Autoscaling Correctness in Kubernetes-Based Systems (IT4216)
**Author.** S.A.C.S. Indrejith (2020/ICT/65), University of Vavuniya
**Supervisor.** Ms. R. Yasotha  **Co-supervisor.** Mr. Suvin Kodituwakku
**Status.** Draft v1 — locked before the 80-run campaign
**Purpose.** Fix the operational parameters that turn the proposal's abstract
metrics (`SRD`, `SES`, correctness buckets) into concrete numbers computable
from k6 JSON output and the HPA watcher event log.

---

## 1. Scope

The research proposal defines two per-decision correctness metrics:

- **Scale Reaction Delay** — `SRD = T_decision − T_SLO_risk`
- **Scale Effectiveness Score** — `SES = (Latency_before − Latency_after) / Latency_before`

Both depend on parameters the proposal deliberately leaves open (percentile,
threshold, window length, anchoring), so this document nails those parameters
against published literature and industry standards. Once these values are
locked, the same numbers apply to every one of the 80 counted campaign runs
and are the operational reference for `analysis/classify_decisions.py`,
`analysis/compute_ses.py`, and `analysis/build_ses_window_summary.py`.

Values are chosen so that a reviewer can trace every constant back to a
citable source, and so that a reasonable alternative choice would move the
result along a smooth axis rather than change its qualitative character.

---

## 2. Decision Summary (What the Code Uses)

| Parameter                       | Value                              | Primary source                                                                                                                        |
| ------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Latency metric                  | k6 `http_req_duration` (client-side) | k6 documentation; corroborated by Straesser & Kounev (2023)                                                                            |
| Percentile                      | **p95**                            | SRE Workbook Ch. 5 (canonical SLI type); AAPA (arXiv 2507.05653) evaluates on P95/P99                                                  |
| SLO threshold                   | **500 ms**                         | Straesser & Kounev (2023) use 100 ms and 1 s bands; SRE Workbook Table 5-10 `HIGH_SLOW` bucket permits up to 1000 ms p90               |
| T_SLO_risk detection window     | rolling **30 seconds**             | Adapted from SRE MWMBR short window (5 min) — scaled to a 2-minute burst; longer than a single-request outlier, short enough to fit inside a burst |
| T_SLO_risk trigger condition    | rolling 30 s p95 > 500 ms          | Directly analogous to SRE MWMBR short-window burn-rate check                                                                           |
| SES **before** window           | **60 seconds** preceding T_decision | Straesser & Kounev warn very short intervals cause oscillation and very long intervals miss rising loads; 60 s is 1/5 of the SRE MWMBR standard rolling window, scaled to burst duration |
| SES **after** window anchor     | **T_pod_Ready** (not T_decision)   | Straesser & Kounev (2023) report average pod readiness ≈ 35 s; Google Cloud GKE production data reports typical pod readiness 90 s–2 min. Anchoring at T_decision would sample during startup and systematically bias SES downward |
| SES **after** window duration   | **60 seconds** following T_pod_Ready | Symmetry with the before window; enough samples at pilot throughput (~40 req/s → ~2 400 samples) for stable p95                        |

Symbolic definitions:

```
T_SLO_risk  := first t ∈ run such that
               p95( http_req_duration over [t − 30s, t] ) > 500 ms

SRD         := T_decision − T_SLO_risk
               (positive → late reaction; negative → pre-emptive)

Latency_before := p95( http_req_duration over
                        [T_decision − 60s, T_decision] )

Latency_after  := p95( http_req_duration over
                        [T_pod_Ready, T_pod_Ready + 60s] )

SES         := (Latency_before − Latency_after) / Latency_before
```

For **scale-down** decisions (no new pod), `T_pod_Ready` is undefined and the
after window is anchored at `T_decision + T_removal_settle` where
`T_removal_settle = 30 s`, giving the terminating pod time to complete its
`preStop` grace and drain in-flight requests.

---

## 3. Justification and Sources

### 3.1 Percentile — Why p95

The SRE Workbook Chapter 5 uses percentile-based SLIs as the recommended
default for latency SLOs, without prescribing p95 vs p99. Two considerations
push us to p95 for this study:

- **Sample stability at short windows.** Both the before and after windows
  are 60 seconds. At the pilot throughput of ~40 req/s, that is ~2 400 samples
  per window, giving a stable p95 estimate but only ~24 samples in the top
  percentile — p99 would be noisier and dominated by individual slow requests.
- **Consistency with the autoscaler evaluation literature.** The
  Archetype-Aware Predictive Autoscaler paper (arXiv 2507.05653) evaluates
  autoscaler performance using "P95/P99 response latency" as one of its
  headline metrics; the recent AIOps autoscaling framework paper
  (arXiv 2512.23415) frames SLO adherence in terms of "latency percentile
  breaches" without over-committing to one percentile. p95 is the more
  robust of the two at the sample sizes involved here.

### 3.2 SLO threshold — Why 500 ms

Two sources bracket this choice:

- **Straesser & Kounev (2023, ICPE '23)** evaluate autoscalers by measuring
  the proportion of requests whose response time exceeds either 100 ms or
  1 s (they run both bands, comparing autoscalers across strict and lenient
  SLOs). 500 ms sits in the middle of that empirical bracket.
- **Google SRE Workbook, Table 5-10 (Alerting at Scale)** publishes
  "request class buckets" — the `HIGH_SLOW` class permits up to 1 000 ms at
  the p90 objective and 5 000 ms at p99. A CPU-bound `/api/compute`
  endpoint whose baseline latency is ~100 ms cleanly sits in the
  `HIGH_SLOW` regime; using 500 ms at p95 is stricter than the SRE guidance
  and pulls the SLO into the range the pilot data actually exercised.

500 ms is not arbitrary but calibrated: pilot bursts showed p50 at 170–310 ms
and p95 at 300–990 ms across three back-to-back runs. A 500 ms p95 threshold
places the SLO right at the boundary of what the current infrastructure
comfortably sustains, so the campaign will produce a mix of decisions on
both sides of the SLO boundary — the required condition for exercising the
4-bucket correctness classification.

### 3.3 T_SLO_risk detection window — Why 30 seconds

The Google SRE Workbook's MWMBR pattern uses a **5-minute short window**
against a **1-hour long window** to detect burn-rate breaches (Table 5-8).
This construction filters out single-request noise (short window) while
requiring the problem to be sustained (long window).

The Workbook's 5-minute short window is calibrated for production error
budgets over a **30-day objective window**. In this study, the counted unit
of analysis is a burst of ~2 minutes' duration — the entire burst is
shorter than one SRE short window. Applying the SRE ratios (short = long/12)
to a 2-minute burst gives a short window of ~10 seconds, which is dominated
by single-request outliers at 25 concurrent VUs.

30 seconds is the smallest window that:
- Filters out single-request outliers (with ~40 req/s throughput, 30 s = 1 200
  samples, enough for a stable rolling p95).
- Is defensibly analogous to the SRE MWMBR short-window role: in both cases,
  the short window fires the moment burn-rate is unambiguously above
  threshold; the long window ensures the problem is sustained. Here the
  "long window" role is played by the whole burst — the analysis discards
  any decision whose SLO-breach window is not sustained for at least the
  30-second detection window.

The choice is conservative in the sense that a shorter window would classify
more decisions as "Late" (higher SRD), so the reported SRD values are a
**lower bound** on how late the HPA is under this workload.

### 3.4 SES before window — Why 60 seconds preceding T_decision

Two considerations:

- **Straesser & Kounev (2023)** explicitly note the tension: *"Choosing the
  evaluation interval too large can lead to violations of SLOs in case of
  rising loads; too small intervals can lead to rapid up-or down-scaling
  behaviour."* They avoid nailing a specific window because it depends on
  the application; they instead evaluate autoscalers over intervals ranging
  from seconds to minutes.
- **The SRE Workbook** uses rolling 5-minute rate windows in its example
  Prometheus queries (`ratio_rate5m`). At 1/5 of that we get 60 seconds,
  which is a natural quantum for a 2-minute burst (the burst has a 60-second
  peak-load plateau; the before window can therefore capture exactly the
  plateau period preceding an HPA decision).

At pilot throughput (~40 req/s), a 60-second window contains ~2 400 samples,
which produces stable p95 estimates (percentile standard error scales
roughly as √n).

### 3.5 SES after window — Why 60 s anchored at T_pod_Ready

The single most important choice in this document. If the after window is
anchored at `T_decision`, it samples latency during the period when the
newly-scheduled pod is still being scheduled, image-pulled, or passing its
startup probes — during which the new pod is **not** yet serving traffic,
so latency stays high for reasons unrelated to whether the HPA decision
itself was correct.

Two empirical measurements settle this:

- **Straesser & Kounev (2023)** report *"the average readiness time of
  evaluated microservices being about 35 seconds"* end-to-end.
- **Google Cloud GKE production observations** report *"new pods typically
  accept traffic in 90 seconds to 2 minutes,"* combining image pull, init
  containers, container creation, and startup probes.

At 35–120 seconds for a new pod to serve traffic, a 60-second after window
anchored at `T_decision` would spend 60–200 % of its duration sampling the
pre-effect period. This would systematically shift `Latency_after` upward
and push otherwise-correct scale-up decisions into the "Ineffective" bucket.

Anchoring at `T_pod_Ready` — the timestamp when the newly-created pod's
readiness probe first succeeds, taken from the Kubernetes API — measures the
period during which the HPA's decision has actually taken effect. This is
the semantic that SES is *supposed* to capture (was the decision effective at
reducing latency?), so the operational definition must match.

Kubernetes exposes pod readiness on the Pod's `.status.conditions` array
with `type: Ready`; the watcher already reads Pod status via
`kubernetes.watch.Watch()` and can log the transition timestamp. A small
extension to `experiment-setup/05-hpa-watcher/watcher.py` records
`T_pod_Ready` for every pod created within N seconds of an `hpa_decision`
event, and the pairing is written into the JSONL alongside the decision.

### 3.6 SES after window duration — Why 60 s

Symmetry with the before window. Also matches the burst's post-peak
recovery phase duration (the burst pattern has a ~7-minute post-peak tail;
sampling the first 60 seconds after the new pod is ready captures the
system's response to the added capacity while load is still elevated).

---

## 4. Threats to Validity

Explicit threats introduced or affected by these choices:

- **Window length sensitivity.** The 30-second detection window and 60-second
  SES windows are defensible but not unique. A sensitivity analysis in the
  results section will report SRD and SES under {15, 30, 45, 60} s detection
  windows and {30, 60, 90, 120} s SES windows, showing that the qualitative
  conclusions (bucket proportions per pattern) are stable across this range.
- **Threshold calibration.** 500 ms was chosen so that pilot p95 values sit
  on both sides. A threshold set to 250 ms would push almost all pilot
  decisions into "Ineffective"; 1 000 ms would push almost all into
  "Unnecessary". This is documented and the analysis will report bucket
  proportions across a small sweep {250, 500, 750, 1 000} ms so the reader
  can see the sensitivity.
- **Pod-readiness anchor precision.** `T_pod_Ready` is derived from the
  Kubernetes API's `.status.conditions[type=Ready].lastTransitionTime`, which
  has second-level resolution on this cluster (verified via `kubectl get pod
  -o yaml`). SRD reported in seconds; sub-second precision is not claimed.
- **Client-side vs server-side latency.** All latency is measured client-side
  (k6). This includes network transit between the k6 client (Mac) and the
  droplet, which under the campaign network conditions is <10 ms round-trip
  on the wired path; the round-trip time is documented and does not affect
  qualitative conclusions.
- **JVM JIT warm-up.** Documented separately (see campaign warm-up strategy).
  Pilot data shows a run-1-to-run-3 latency drop of 2–3× due to JIT warm-up.
  The campaign includes 3 discard warm-up bursts per pattern before the
  20 counted runs to allow the JVM to stabilise.
- **Clock synchronisation.** k6 timestamps come from the Mac clock;
  T_decision and T_pod_Ready come from the droplet clock. Both hosts run
  NTP synchronisation with drift <100 ms (verified with `timedatectl status`
  on the droplet and system time preferences on the Mac). SRD is reported in
  seconds so this drift does not affect qualitative conclusions.

---

## 5. Implementation Reference

The definitions above map to the analysis scripts as follows:

| Definition          | Implementation file                              | Function or column                                       |
| ------------------- | ------------------------------------------------ | -------------------------------------------------------- |
| T_SLO_risk          | `analysis/classify_decisions.py`                 | `compute_t_slo_risk(k6_json, threshold=0.500, window=30)`|
| SRD                 | `analysis/classify_decisions.py`                 | column `srd_seconds` in `classified_decisions.csv`       |
| Latency_before      | `analysis/build_ses_window_summary.py`           | column `latency_before_p95` in `ses_window_summary.csv`  |
| T_pod_Ready         | `analysis/build_ses_window_summary.py`           | column `t_pod_ready` (joined from watcher JSONL)         |
| Latency_after       | `analysis/build_ses_window_summary.py`           | column `latency_after_p95` in `ses_window_summary.csv`   |
| SES                 | `analysis/compute_ses.py`                        | column `ses` in `decisions_with_ses.csv`                 |
| 4-bucket assignment | `analysis/classify_decisions.py`                 | column `bucket` — Correct&Timely, Correct-but-Late, Unnecessary, Ineffective |

The classification rules (which combinations of SRD and SES map to which
bucket) are documented in `analysis/classification_rules.pdf` and are
unchanged by this document — this document specifies the *inputs* to that
classification, not the classification itself.

---

## 6. Sources

Full citations for the references used above:

- **Google SRE Workbook — Chapter 5, "Alerting on SLOs"**
  Thurgood, Frame, Lenton, Quinito, Tolchanov & Trdin (2018)
  https://sre.google/workbook/alerting-on-slos/
  Used for: Multi-Window Multi-Burn-Rate pattern, short/long window rationale,
  Table 5-8 (page thresholds) and Table 5-10 (request class buckets).

- **Straesser, M. & Kounev, S. (2023). "Autoscaler Evaluation and
  Configuration: A Practitioner's Guideline." ICPE '23.**
  https://www.researchgate.net/publication/368477961
  Used for: 100 ms / 1 s SLO threshold bracket, "average readiness time
  ~35 s" for microservices, evaluation-interval trade-off framing.

- **arXiv:2512.23415 — "An SLO Driven and Cost-Aware Autoscaling Framework
  for Kubernetes."**
  https://arxiv.org/abs/2512.23415
  Used for: SLO adherence measured by "frequency and duration of SLO
  violations" — corroborates the immediate-per-decision SLO breach approach.

- **arXiv:2507.05653 — "AAPA: An Archetype-Aware Predictive Autoscaler
  with Uncertainty Quantification for Serverless Workloads on Kubernetes."**
  https://arxiv.org/pdf/2507.05653
  Used for: P95/P99 response latency as canonical evaluation metric for
  autoscalers.

- **Google Cloud Documentation — "Monitor startup latency metrics on GKE."**
  https://cloud.google.com/kubernetes-engine/docs/how-to/monitor-startup-latency-metrics
  Used for: Empirical measurement of pod startup timeline; "new pods
  typically accept traffic in 90 seconds to 2 minutes."

- **Nobl9 — "SLO Metrics: A Best Practices Guide."**
  https://www.nobl9.com/service-level-objectives/slo-metrics
  Used for: Industry practitioner corroboration of 5-minute rolling
  percentile windows for latency SLOs.

- **The New Stack — "How to Correctly Frame and Calculate Latency SLOs."**
  https://thenewstack.io/how-to-correctly-frame-and-calculate-latency-slos/
  Used for: Corroboration that 5-minute rolling windows for latency
  percentiles are industry-standard.

---

## 7. Change Log

| Version | Date       | Change                                                                                              |
| ------- | ---------- | --------------------------------------------------------------------------------------------------- |
| v1      | 2026-07-03 | Initial version. Locked before the 80-run campaign. All values in §2 are the operational reference. |
