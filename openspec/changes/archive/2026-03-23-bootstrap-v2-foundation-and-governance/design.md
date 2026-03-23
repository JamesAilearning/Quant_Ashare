## Context

This change creates the V2 baseline only. It does not build the trading pipeline yet.

The design objective is to lock governance intent early:

- canonical official metrics path as a first-class contract
- explicit experimental boundaries
- data-contract-first implementation style
- regression-first enforcement

## Goals

- Make V2 development spec-driven from day one.
- Capture V1 lessons in auditable repository docs.
- Provide clear stage roadmap for upcoming implementation changes.

## Non-Goals

- No runtime trading/model/backtest logic implementation.
- No UI workflow implementation.
- No provider or strategy behavior changes.

## Validation Plan

1. `openspec validate --specs --strict`
2. Ensure bootstrap docs exist and are internally consistent.
3. Keep this change docs-only and archivable.
