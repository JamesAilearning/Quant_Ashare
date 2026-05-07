# Agent Development Guide

Use this guide only when the user asks to implement, fix, refactor, create tests, update contracts, or perform other code-changing work.

Do not use this guide for pure code review unless the user explicitly asks to fix review findings.

## Development goal

Make the smallest compliant change that satisfies the approved scope while preserving qlib/OpenSpec governance boundaries.

## General workflow

1. Restate the requested change in one sentence.
2. Identify whether the change is foundation, contract, runtime, UI, test, or documentation work.
3. Check whether OpenSpec approval is required.
4. Inspect only the files needed for the scoped change.
5. Implement the smallest safe change.
6. Add or update targeted tests when behavior or governance boundaries are affected.
7. Run the narrowest useful validation first; expand only when needed.
8. Summarize changed files, scope, tests, and any intentionally unimplemented work.

## Change categories

### Foundation changes

Allowed:

- Skeletons.
- Interfaces.
- Placeholders.
- Documentation.
- Tests that lock intended non-behavior.

Not allowed:

- Runtime trading behavior.
- Hidden selection semantics.
- Unapproved metric calculation paths.

### Contract changes

Allowed:

- Schemas.
- Metadata.
- Source-of-truth definitions.
- Provenance fields.
- Validation boundaries.
- Status fields.
- Placeholders.

Not allowed:

- Silent runtime selection.
- Trading semantics hidden inside validation.
- Treating informational health as canonical policy.

### Runtime changes

Allowed only when explicit, narrowly scoped, and spec-approved.

Must not:

- Widen into unrelated contract or UI work without approval.
- Create competing official paths.
- Introduce implicit fallback.
- Promote experimental or research behavior.

### UI changes

Allowed:

- Operator-facing informational views.
- Status or health display that preserves governance wording.

Must not:

- Blur canonical vs experimental meaning.
- Present research or experimental behavior as production-canonical.
- Turn informational validation into hard-fail policy unless a policy explicitly defines it.

## Data contract rules

- Benchmark, taxonomy, universe, and similar artifacts require explicit source-of-truth and provenance.
- Validation health is informational unless a policy explicitly defines hard-fail behavior.
- Contract validation must remain separate from runtime selection semantics.

## Testing expectations

- Governance boundaries should be protected by regression tests.
- Operator-visible status boundaries should be protected by regression tests where practical.
- New contract foundations should include contract-focused tests.
- Placeholder runtime components should include tests confirming they remain intentionally unimplemented.
- Prefer targeted tests plus repo-wide tests when the change is small enough.

## Conflict behavior

Stop and report the conflict if the request would violate:

- `AGENTS.md`.
- The current OpenSpec baseline.
- Approved scope.
- Canonical qlib-native metric semantics.
- Explicit experimental/research boundaries.

Do not widen the scope to resolve the conflict on your own.

## Completion summary

End implementation work with:

```md
## Changed files

- ...

## Implemented scope

- ...

## Intentionally not implemented

- ...

## Validation

- Ran: ...
- Not run: ...

## Governance check

- Scope drift: none / explain
- Contract drift: none / explain
- Runtime semantics drift: none / explain
- Experimental leakage: none / explain
```
