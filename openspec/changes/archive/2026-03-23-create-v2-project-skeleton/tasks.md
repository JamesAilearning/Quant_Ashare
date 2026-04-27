## 1. Skeleton Directory Setup

- [x] 1.1 Create production/runtime skeleton directories (`app/` or `web/`, `src/core/`, `src/data/`, `src/contracts/`).
- [x] 1.2 Create `tests/`, `docs/`, and `research/factor_lab/` skeleton directories.
- [x] 1.3 Add minimal placeholder files (`__init__.py` or equivalent) to mark package boundaries where useful.

## 2. Boundary Documentation

- [x] 2.1 Add/update boundary notes for each major layer (runtime, data, contracts, tests, docs).
- [x] 2.2 Add `research/factor_lab/README` stating research artifacts are non-production and non-canonical.
- [x] 2.3 Ensure docs explicitly preserve canonical-only official metrics governance and no silent experimental promotion.

## 3. Minimal Test Skeleton

- [x] 3.1 Add minimal logic-test skeleton placeholders under `tests/`.
- [x] 3.2 Add minimal governance/contract regression skeleton placeholders under `tests/`.
- [x] 3.3 Confirm test skeleton is future-facing only and contains no runtime trading logic.

## 4. Validation and Review

- [x] 4.1 Verify no training/backtest/benchmark/strategy runtime behavior is introduced.
- [x] 4.2 Run `openspec validate create-v2-project-skeleton --strict`.
- [x] 4.3 Run `openspec validate --specs --strict`.

## 5. Archive Readiness

- [x] 5.1 Confirm scope remains foundation-only and minimal.
- [x] 5.2 Archive after implementation and validation pass.
