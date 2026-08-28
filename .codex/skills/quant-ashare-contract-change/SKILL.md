---
name: quant-ashare-contract-change
description: "Implement Quant_Ashare changes to runtime contracts, report/index schemas, status artifacts, configuration fields, or paired Pipeline and WalkForward behavior. Use when callers and tests must migrate together; do not use for presentation-only UI work."
---

# Quant_Ashare Contract Change

Use a contract-first implementation for any change that affects persisted data,
runtime inputs, public signatures, errors, or canonical report semantics.

## Establish the contract first

- Start the required OpenSpec change before modifying a meaningful contract.
- Identify the canonical producer, every reader/caller, and every existing
  test before choosing a field name, type, default, or error behavior. Use
  repository search rather than inference.
- State the source of truth, lifecycle, provenance, failure representation,
  and backward-compatibility posture. A missing field is not permission to
  silently invent a default.
- Keep research, experimental, and official/canonical behavior explicitly
  separate. Do not promote an experimental artifact through a convenience path.

## Symmetry and migration

- Pipeline and WalkForward write parallel artifacts. When one gains or changes
  a shared schema field, migrate the other engine, the run index, consumers,
  fixtures, and contract tests in the same change unless the approved design
  explicitly says otherwise.
- When a function signature, dataclass field, exception, or JSON schema
  changes, update every caller and rename tests so their names describe the
  post-change behavior.
- Preserve fail-loud behavior for invalid configuration, corrupt artifacts, and
  unavailable canonical dependencies. Do not add fallback values merely to keep
  a caller running.

## Verification

- Add synthetic-input regression coverage that fails before the fix and passes
  after it; do not rely only on local data or an E2E run.
- Exercise both normal and failure/corruption paths for persisted artifacts.
- Import every changed source module, run the required logic/governance suites,
  and run `openspec validate` for OpenSpec-affecting work.
- Before committing, inspect the staged diff and make the commit message
  describe the implementation actually present.
