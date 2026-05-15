# Tasks: UI Tushare And GPU Controls

## OpenSpec

- [x] Add operator UI requirements for live provider URI validation and Tushare ingest jobs
- [x] Add model-training requirement for explicit compute-device selection

## Implementation

- [x] Move `provider_uri` outside the Streamlit run form
- [x] Add Tushare ingest controls and job mode
- [x] Add `compute_device` to pipeline, walk-forward, and model-training config
- [x] Pass `compute_device="gpu"` to qlib `LGBModel` as `device_type`
- [x] Reject unsupported GPU/model combinations without silent fallback
- [x] Capture runner-level stdout/stderr for UI-launched jobs
- [x] Support non-Windows job stop without `taskkill`
- [x] Keep report/chart path guards anchored to the repository output root

## Tests

- [x] Add UI config validation tests for Tushare provider keys
- [x] Add job manager / job runner tests for Tushare ingest mode
- [x] Add job manager tests for runner logs and cross-platform stop
- [x] Add path guard regression coverage for CWD-independent roots
- [x] Add model config projection and model trainer tests for `compute_device`
- [x] Run targeted tests, import smoke, ruff, OpenSpec validation, and repo logic/governance tests
