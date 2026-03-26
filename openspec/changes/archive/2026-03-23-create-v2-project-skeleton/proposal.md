## Why

The V2 repository has governance initialization but still lacks a concrete project skeleton that enforces production, contract, and research boundaries before runtime implementation starts. Establishing this structure now reduces coupling risk and keeps later canonical/data-contract/operator work staged and auditable.

## What Changes

- Create a clean V2 directory skeleton for later staged implementation:
  - `app/` or `web/`
  - `src/core/`
  - `src/data/`
  - `src/contracts/`
  - `tests/`
  - `docs/`
  - `research/factor_lab/` (research-only placeholder)
- Add minimal package/module placeholders (`__init__.py` or equivalent) where useful.
- Add minimal test skeleton files for:
  - logic tests
  - governance/contract boundary regression tests
- Add boundary docs for each major layer, including explicit research-vs-production separation.
- Keep this change foundation-only: no training/backtest/strategy/benchmark runtime logic implementation.

## Capabilities

### New Capabilities
- `v2-project-skeleton-boundaries`: V2 repository SHALL define and document clear runtime, contract, and research boundaries via a minimal project skeleton.

### Modified Capabilities
- None.

## Impact

- Affected areas:
  - top-level directory structure and placeholder files
  - boundary documentation in `docs/` and layer-level README notes
  - minimal test skeleton under `tests/`
- No runtime trading behavior changes.
- No canonical backtest contract implementation in this change.
