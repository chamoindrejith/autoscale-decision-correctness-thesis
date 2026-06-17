# Datasets and Generated Outputs

The `results/` directory in this repository is created at runtime by the
analysis pipeline and is excluded from version control. All datasets — raw
inputs and pipeline outputs — are hosted externally on Google Drive.

### Raw experimental data (inputs)

| File family               | Description                                                       | Approx size  |
| ------------------------- | ----------------------------------------------------------------- | ------------ |
| `step-run-*.json` × 20    | k6 raw per-request output for each Step run                       | ~24 MB each  |
| `burst-run-*.json` × 20   | k6 raw output for each Burst run                                  | varies       |
| `ramp-run-*.json` × 22    | k6 raw output for each Ramp run (incl. 2 degraded + 3 re-runs)    | ~24 MB each  |
| `noisy-run-*.json` × 22   | k6 raw output for each Noisy run (incl. 2 pre-script attempts)    | varies       |
| `*-events-*.json` × 60    | Per-run HPA decision captures by the watcher                      | small        |
| `hpa-events-full.jsonl`   | Authoritative HPA decision log from the watcher PVC               | 560 KB       |
| **Total raw data**        |                                                                   | **~2 GB**    |

### Generated pipeline outputs

| File                                  | Produced by                                  | Description                                              |
| ------------------------------------- | -------------------------------------------- | -------------------------------------------------------- |
| `run_index.csv`                       | `analysis/build_master_dataset.py`           | One row per run with timestamps                          |
| `master_decisions.csv`                | `analysis/build_master_dataset.py`           | All HPA decisions tagged to runs                         |
| `classified_decisions.csv`            | `analysis/classify_decisions.py`             | Decisions with bucket assignment                         |
| `classification_summary.csv`          | `analysis/classify_decisions.py`             | Per-pattern × direction × bucket counts                  |
| `decisions_with_ses.csv`              | `analysis/compute_ses.py`                    | Decisions augmented with SES values                      |
| `ses_summary.csv`                     | `analysis/compute_ses.py`                    | Per-pattern SES aggregates                               |
| `ses_window_summary.csv`              | `analysis/build_ses_window_summary.py`       | Per-decision before/after statistics                     |
| `ses_input_dataset.csv`               | `analysis/extract_ses_input_dataset.py`      | Long-format raw inputs for SES (91 MB)                   |
| `aggregated_latency_per_pattern.csv`  | `analysis/export_aggregated_plot_data.py`    | Bin-aggregated time-series for plots                     |
| `plots/*.png`                         | `analysis/plot_*.py`                         | Generated figures                                        |

## Reproducing Locally

1. Download the raw files from the Drive folder.
2. Place them in `results/` of this repository.
3. Run the analysis pipeline as documented in the top-level README, sections 9–12.
4. The pipeline regenerates every file listed under "Generated pipeline outputs"
   into `results/` (and `results/plots/`).
