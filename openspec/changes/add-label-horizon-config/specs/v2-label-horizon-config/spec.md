## ADDED Requirements

### Requirement: The label horizon SHALL be configurable with an identity-preserving default

The dataset/pipeline/walk-forward configuration SHALL expose
`label_horizon_days` (int, default 1): the holding horizon in trading days (buy
T+1 close, sell T+1+H close). The Alpha158 label expression SHALL be
`Ref($close, -(H+1))/Ref($close, -1) - 1`, passed to the handler via its `label`
kwarg (no subclassing). For H=1 the produced expression SHALL be
character-for-character identical to today's hard-coded label
(`Ref($close, -2)/Ref($close, -1) - 1`), and the default-configured pipeline
SHALL remain byte-identical in behavior (REGEN-2 replay anchor green). A
non-positive or non-integer horizon SHALL be rejected fail-loud at config
validation time.

#### Scenario: default reproduces today exactly
- **WHEN** a dataset is built with the default `label_horizon_days=1`
- **THEN** the label expression equals `Ref($close, -2)/Ref($close, -1) - 1`
  and the REGEN-2 anchor test stays green

#### Scenario: a 5-day horizon produces the documented expression
- **WHEN** `label_horizon_days=5`
- **THEN** the label expression equals `Ref($close, -6)/Ref($close, -1) - 1`

#### Scenario: an invalid horizon is refused
- **WHEN** `label_horizon_days` is 0, negative, or non-integer
- **THEN** config validation raises with an actionable message

### Requirement: The feature-cache key SHALL separate label horizons

The feature-dataset cache key SHALL incorporate the label horizon such that
datasets built under different horizons can NEVER share a cache entry (a shared
entry would silently serve one horizon's labels to another's training — silent
cross-label poisoning). The mechanism is the EXTENSIBLE key-payload composition
already contracted in `compute_cache_key` ("other config fields, when added in
the future, MUST be included here if they affect dataset materialisation"): the
horizon joins the payload as a dimension key, ADDED ONLY WHEN NON-DEFAULT, so
H=1 produces a payload byte-identical to today's — existing caches remain
valid — while H≠1 produces a structurally distinct key. Future
materialisation-affecting dimensions follow the same include-when-non-default
pattern without refactoring. The handler identity string (`alpha158_default`)
keeps its single responsibility (handler-internal state) and is unchanged.

#### Scenario: horizons never share cache entries
- **WHEN** two builds differ only in `label_horizon_days` (1 vs 5)
- **THEN** their cache keys differ

#### Scenario: the default keeps its cache
- **WHEN** a build uses the default horizon
- **THEN** its cache key is byte-identical to the pre-change key (existing
  cache entries stay valid)

### Requirement: The label-lookahead embargo SHALL follow the configured horizon

The label-lookahead embargo SHALL be derived from the configured horizon as H+1
trading days everywhere the fixed 2-trading-day lookahead was assumed. The
grep-proven consumer inventory is: (a) the feature-builder segment-embargo
check, (b) the walk-forward fold gap, and (c) the operator-UI segment-gap guard
(`web/operator_ui/training_guards.py`) — ALL THREE SHALL derive the lookahead
from ONE shared helper so they cannot drift (the UI guard reads the horizon
from the parsed config when present, else 1 — today's UI cannot set the field,
so its behavior is unchanged). Segments (or folds) closer than the
horizon-driven embargo SHALL be refused fail-loud with a message naming the
horizon and the required gap. H=1 SHALL yield today's value (2), leaving
default runs unchanged.

#### Scenario: a longer horizon widens the required gap
- **WHEN** `label_horizon_days=5` and train/valid segments are 2 trading days
  apart
- **THEN** the build refuses with the horizon-driven embargo violation

#### Scenario: the default gap is unchanged
- **WHEN** `label_horizon_days=1`
- **THEN** the required gap equals the pre-change constant (2 trading days)

### Requirement: The horizon SHALL be resume- and audit-visible

The walk-forward resume fingerprint SHALL incorporate `label_horizon_days` so a
resumed run can never silently mix folds trained under different horizons (the
one-time invalidation of pre-existing manifests is accepted and documented).
The invalidation SHALL be fail-LOUD, not silent: the fold manifest additionally
records its `label_horizon_days`, and when a resume re-runs because of a
fingerprint mismatch the log SHALL name the cause when it is determinable — a
changed horizon is named with both values ("label_horizon_days changed:
manifest=1, config=5 — re-running, expected"), and a pre-upgrade manifest
(no recorded horizon) is named as such — never a bare unexplained re-run.
`SignalAnalyzer`'s IC measurement horizons (1d/5d) SHALL be verified by test to
be label-independent (computed from realized prices), so changing the label
horizon does not silently change what the IC diagnostics mean.

#### Scenario: a horizon change invalidates resume
- **WHEN** a run directory holds manifests from `label_horizon_days=1` and the
  config now says 5
- **THEN** the folds re-run (fingerprint mismatch) rather than resuming

#### Scenario: the re-run names its cause
- **WHEN** a resume re-runs a fold whose manifest records a different
  `label_horizon_days` than the config (or records none — pre-upgrade)
- **THEN** the log names the horizon change (or the pre-upgrade manifest) as
  the cause, never a bare unexplained re-run

#### Scenario: IC measurement is label-independent
- **WHEN** the label horizon changes
- **THEN** a pinned test shows the analyzer's IC periods still measure realized
  1d/5d forward returns
