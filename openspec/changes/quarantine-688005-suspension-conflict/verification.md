## Review summary

No blocking findings found in the final locally reviewed scope. This is one separately authorized conflict policy, not a generic missing-row tolerance or a production cutover.

## Findings

No P0/P1/P2 findings remain after local iteration. The initial export migration dropped a string-type guard; the existing `null_instrument` regression caught it and the guard was restored before publishing. Initial new fixtures lacked a required name/provenance record; fixtures were corrected without weakening runtime validation. Legacy failure diagnostics remain covered. No accepted P3 exceptions.

Reviewed contract producers and readers, acquisition exact-key checks, original/retained lineage, journal policy matching and interrupted recovery, fetch preflight, CLI choices, pre-Top-K exclusion, JSON/CSV/UI identity validation, and both canonical historical-engine refusal paths. Old policy semantics and seven-field evidence shape remain supported. Unapproved loss/conflict still fails closed.

## Tests / validation gaps

- Before implementation: `test_conflict_contract_has_separate_explicit_identity` raised `ValueError: unknown suspension quarantine policy`; the new publication test rejected the unsupported policy argument. After implementation: both pass.
- Final logic/governance suite: 5626 passed, 33 skipped, 2087 subtests passed (262.20 seconds).
- Final data-pipeline suite: 1479 passed, 1 skipped, 94 subtests passed (31.46 seconds).
- Ruff passes across src/tests/scripts/web. Fresh non-incremental mypy passes all 244 source files. All nine changed source/CLI/UI modules import successfully.
- Strict OpenSpec validation and diff whitespace checks pass.
- Heavy gates ran serially with one-CPU affinity, numerical threads set to one, below-normal priority, no vendor token, 8 GiB tree cap and 6 GiB free-memory guard. Full test peak tree RSS: 606998528 bytes. No resource abort.
- Full tests emitted warnings including a Windows subprocess decoding/thread warning; no tests failed. This result is not a warning-free environment claim.
- Not run: actual vendor recovery, independent real-data/provider/PIT/historical recommendation acceptance, production backups/switch/first update, or UI server/browser validation. No app is listening on ports 8500–8509. UI changes are limited to the existing pure disclosure helper and synthetic artifact checks; no button authorization was added.
- Remote CI and fresh Codex review remain separate requirements before the authorized merge. Operational acceptance remains separate after that merge.

## Merge recommendation

Safe to merge, assuming the unrun remote CI and current-head review pass. Not approval to switch production. Preserve failed isolated runs, paused app heartbeat and disabled production scheduler.
