## Why

Bootstrap rejects members trained from dirty, unknown or unmerged code, but maintenance rotation currently ignores the same producer source fields. A new member can therefore enter an otherwise valid ensemble through quarterly maintenance with unregistered implementation semantics.

## What Changes

- Explicitly apply bootstrap's clean-checkout/full-commit/mainline-ancestry source eligibility to the incoming member in quarterly rotation.
- Use the same manifest-bound sidecar buffer and the same pinned mainline revision already used for certification; refuse before model loading, backup or installation.
- **BREAKING**: missing, malformed, dirty or unregistered source evidence no longer authorizes maintenance installation, even when old gate artifacts say PASS.
- Keep bootstrap, daily serving, metric computation, gate thresholds and registered-family validation outside this source-only change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-daily-stock-recommendation`: make incoming maintenance-member source eligibility explicit without adding strategy recertification or daily Git access.

## Impact

The rotation executor, synthetic scratch-Git rotation tests and the production runbook. Reuse the existing pure source-field validator; producer `source_git_commit` and `source_git_dirty` fields, manifest and gate schemas remain unchanged. No production artifacts, training or deployment changes.

Source ancestry alone is not approval of a model family or proof of unseen performance. Direct/manual manifest changes and routine serving are not newly adjudicated here.
