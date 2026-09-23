## Why

The real 2026-09-23 isolated update correctly refused a new suspend_d discrepancy: the retained `(688005.SH, 20260116, null, R)` key is absent and the observed replacement is `(688005.SH, 20260116, 09:30-09:30, S)`. The operator explicitly approved isolating this exact conflict and excluding SH688005 from daily picks, without choosing which vendor version is true.

## What Changes

- Add an independent, explicitly selected `suspend-688005-20260116-conflict` policy with byte-bound original, retained and candidate evidence.
- Preserve the original 688766 policy and its existing artifacts unchanged. Policies are alternatives, never a combined waiver; mismatched selection, other holes and any unapproved payload still refuse.
- Keep the new conflict quarantined on subsequent refreshes, even if the old R row returns: resolution of contradictory history requires a separate decision, not automatic certification.
- Derive serving exclusion and informational disclosure from the selected evidence policy. Do not change scores, historical universes, holdings, training or canonical backtest refusal.

## Capabilities

### New Capabilities

- `scoped-suspension-conflict`: Exact conflict authorization, evidence lifecycle and single-security serving isolation alongside the unchanged legacy incident.

### Modified Capabilities

None. This adds a separate explicit policy; it does not broaden the legacy incident's permitted keys.

## Impact

Suspension evidence contract, acquisition publication/recovery, fetch preflight, CLI choices, recommendation output validation and read-only operator disclosure. Synthetic contract/acquisition/recovery/serving tests are required. No production write, data replay-as-live, bulk retry, scheduler reactivation or UI authorization is part of this code change. Production acceptance and cutover remain separate gates.
