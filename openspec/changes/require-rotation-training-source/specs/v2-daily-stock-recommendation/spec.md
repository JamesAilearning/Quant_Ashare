## ADDED Requirements

### Requirement: Maintenance rotation SHALL require eligible incoming training source evidence

The rotation executor SHALL validate source evidence before loading models,
backing up or installing a candidate. The incoming manifest-bound trainer sidecar SHALL
record `source_git_dirty` as the boolean false and `source_git_commit` as a
full lowercase 40-hex commit. Missing or malformed fields SHALL NOT be filled
from the current checkout, operator declarations or defaults.

The training commit SHALL be equal to or an ancestor of the same immutable
mainline revision already pinned for that execution's certification checks.
The executor SHALL use a read-only ancestry check in the configured repository;
a non-ancestor, unknown commit, failed Git command, timeout or OS error SHALL
produce a classified refusal. It SHALL NOT fetch history or choose another
revision as a fallback.

Source checking SHALL reuse the same parsed incoming sidecar bytes whose hash
was verified against the staged manifest. Older PASS gates SHALL NOT bypass
this source-evidence eligibility. Refusal SHALL leave the incumbent manifest
unchanged, without a rotation backup or surviving staging file. The held lock
SHALL be released; its intentionally persistent lockfile SHALL retain existing
behavior. Existing gate artifacts SHALL remain available.

This qualifies incoming-member source eligibility in the maintenance path. It
SHALL NOT add a per-retrain gate, rerun strategy certification, change gate
thresholds or canonical metrics, introduce daily-serving Git requirements, or
claim that ancestry certifies a model family or unseen performance. Bootstrap
and surviving incumbent-member behavior SHALL remain unchanged.

#### Scenario: Unregistered or unusable source cannot enter maintenance
- **WHEN** an otherwise valid candidate has dirty/missing/malformed source
  fields or a training commit not reachable from the pinned mainline revision
- **THEN** rotation refuses before model loading and installation, even if the
  existing member and ensemble gates say PASS

#### Scenario: Source checks remain bound to the certification revision
- **WHEN** the mainline ref moves after the executor pins its revision
- **THEN** source ancestry uses the pinned commit, not the updated ref

#### Scenario: Git failure preserves the incumbent
- **WHEN** ancestry checking fails to start, exceeds its 30-second bound or
  returns an error
- **THEN** the executor reports a classified source refusal and leaves no
  backup, installed replacement, staging residue or held lock

#### Scenario: Clean mainline training retains normal rotation
- **WHEN** a clean source commit is equal to or ancestral to the pinned
  revision and all existing checks pass
- **THEN** rotation retains its normal backup and installation behavior
