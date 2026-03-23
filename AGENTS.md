# AGENTS

## Governance Baseline

- Official metrics must come only from one canonical qlib-native path.
- Experimental paths are allowed for research diagnostics, but must be labeled explicitly.
- Do not silently promote experimental constraints into official metrics.
- Any constraint migration into canonical path must be decision-first and semantic-fidelity checked.

## Engineering Rules

- Use OpenSpec for all meaningful changes.
- Keep patches minimal and reviewable.
- Preserve published metric semantics unless a spec-approved breaking change says otherwise.
- Add regression tests for governance boundaries and operator-visible status boundaries.

## Data Contract Rules

- Benchmark, universe, and taxonomy artifacts must be validated with explicit contract checks.
- Validation health is informational unless a policy explicitly defines hard-fail behavior.
- Source-of-truth and provenance metadata must be explicit and testable.
