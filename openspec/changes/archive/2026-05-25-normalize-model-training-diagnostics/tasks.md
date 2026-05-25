# Tasks: Normalize Model Training Diagnostics

## OpenSpec

- [x] Add diagnostics normalization requirements

## Implementation

- [x] Normalize nested and flat eval histories into one shape
- [x] Refresh XGB/CatBoost eval histories from fitted inner models
- [x] Select final valid loss with model-family-aware best-iteration indexing

## Tests

- [x] Add XGB flat eval-history normalization test
- [x] Add XGB/CatBoost inner eval-history refresh tests
- [x] Add model-family-specific final-loss indexing tests
- [x] Run targeted tests, import smoke, ruff, OpenSpec validation, and repo logic/governance tests
