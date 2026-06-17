# Datasets and Generated Outputs

The `results/` folder is **runtime-generated** and not tracked in this
repository. All datasets — raw inputs and pipeline outputs alike — are
hosted on Google Drive.

## Access

**Google Drive folder:** _(add the share link here once created)_

Shared with:
- Ms. R. Yasotha (Supervisor)
- Mr. Suvin Kodituwakku (Co-Supervisor)

## What's in the Drive Folder

Organised into two sections:

### Raw experimental data (inputs)

| File family | Description | Approx size |
|---|---|---|
| `step-run-*.json` × 20 | k6 raw per-request output for each Step run | ~24 MB each |
| `burst-run-*.json` × 20 | k6 raw output for each Burst run | varies |
| `ramp-run-*.json` × 22 | k6 raw output for each Ramp run (incl. 2 degraded + 3 re-runs) | ~24 MB each |
| `noisy-run-*.json` × 22 | k6 raw output for each Noisy run (incl. 2 pre-script attempts) | varies |
| `*-events-*.json` × 60 | Per-run HPA decision captures by the watcher | small |
| `hpa-events-full.jsonl` | Authoritative HPA decision log from the watcher PVC | 560 KB |
| **Total raw data** | | **~2 GB** |

### Generated pipeline outputs

| File | Produced by | Description |
|---|---|---|
| `run_index.csv` | `analysis/build_master_dataset.py` | One row per run with timestamps |
| `master_decisions.csv` | `analysis/build_master_dataset.py` | All HPA decisions tagged to runs |
| `classified_decisions.csv` | `analysis/classify_decisions.py` | Decisions with bucket assigned |
| `classification_summary.csv` | `analysis/classify_decisions.py` | Per-pattern × direction × bucket counts |
| `decisions_with_ses.csv` | `analysis/compute_ses.py` | Decisions augmented with SES values |
| `ses_summary.csv` | `analysis/compute_ses.py` | Per-pattern SES aggregates |
| `ses_window_summary.csv` | `analysis/build_ses_window_summary.py` | Per-decision before/after stats |
| `ses_input_dataset.csv` | `analysis/extract_ses_input_dataset.py` | Long-format raw inputs for SES (91 MB) |
| `aggregated_latency_per_pattern.csv` | `analysis/export_aggregated_plot_data.py` | Bin-aggregated time-series for plots |
| `plots/*.png` | `analysis/plot_*.py` | All thesis figures |

## Reproducing Locally

1. Download all raw files from the Drive folder
2. Place them in `results/` of this repository
3. Run the analysis pipeline as documented in the top-level README, sections 9–12
4. The pipeline will regenerate every file listed in "Generated pipeline outputs"
   above and write them to `results/` (and `results/plots/`)

## Excluded Runs

Two Noisy runs (June 4 evening attempts) and two Ramp runs (#11 degraded,
#15 degraded) are included in the dataset but flagged. Per supervisor
guidance, no runs are excluded from the primary analysis — flagged runs
are retained and discussed under Threats to Validity in the dissertation.
