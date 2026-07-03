## ADDED Requirements

### Requirement: SignalAnalyzer and PerformanceAttribution SHALL accept PIT routing with a WARN fallback

`SignalAnalyzer.analyze` and `PerformanceAttribution.analyze` SHALL accept an
optional `pit_provider`; when supplied, their qlib price fetches
(`_fetch_returns` / `_get_instrument_returns`) SHALL route through
`pit_provider.get_features` (post-delist masking applied), and when absent the
behavior SHALL remain bit-identical to today (direct `D.features` + the
existing WARN log), so independent callers are not broken. The provider SHALL
be alignment-validated against the canonical qlib runtime the same way the
existing `backtest_runner` opt-in does (no new mechanism).

#### Scenario: PIT path masks post-delist fills
- **WHEN** `analyze` runs with a `pit_provider` over a window containing an
  instrument's delist date
- **THEN** returns after the delist date are NaN (masked), not forward-filled
  pseudo-returns

#### Scenario: the WARN fallback is unchanged
- **WHEN** `analyze` runs without a `pit_provider`
- **THEN** outputs are bit-identical to the pre-change implementation and the
  WARN log still fires

### Requirement: The canonical engines SHALL construct and thread the PIT provider from configuration

`WalkForwardConfig` and `PipelineConfig` SHALL gain
`delisted_registry_path: str = ""`. When non-empty, the engine SHALL construct
ONE `PITDataProvider` at run start and pass it to both analyzers; when empty,
no provider is constructed and the WARN path runs (today's behavior, so the
default is identity-preserving). A non-empty path that does not exist SHALL
fail loud at provider construction (never silently degrade to the WARN path).

#### Scenario: default is identity-preserving
- **WHEN** a run executes with the default empty `delisted_registry_path`
- **THEN** behavior is bit-identical to today (WARN path; REGEN-2 anchor
  untouched by PR-1)

#### Scenario: a bad registry path fails loud
- **WHEN** `delisted_registry_path` is non-empty but unreadable/missing
- **THEN** the run refuses at provider construction with an actionable error,
  rather than silently running the WARN path

### Requirement: The REGEN-2 replay SHALL follow canonical semantics and the baseline SHALL be re-signed deliberately

The REGEN-2 replay script SHALL construct the same PIT provider (from a
committed mini delisted-registry fixture) so the anchor keeps measuring the
CANONICAL semantics. The resulting baseline change SHALL be a deliberate
re-sign: regenerated ONLY on the canonical-pinned environment (CI; the local
box is off-pin and forbidden), with an old-vs-new per-fold diff table in which
every `ic_1d`/`ic_5d` change is attributable to one of the Step-0-identified
delisted instruments, and the backtest three metrics
(`annualized_return`/`max_drawdown`/`information_ratio`) are bit-unchanged —
any drift there aborts the re-sign.

#### Scenario: IC drift must be attributable
- **WHEN** the re-signed baseline differs from the old on a fold's IC
- **THEN** the diff table names the delisted instrument(s) whose masked
  post-delist fills explain that fold's change

#### Scenario: backtest metrics must not move
- **WHEN** the re-signed baseline is compared to the old
- **THEN** per-fold `annualized_return`/`max_drawdown`/`information_ratio`
  are identical within the replay tolerance (the change touches IC inputs
  only)

### Requirement: The governance whitelist SHALL reflect the opt-in deliberately

The `PIT_FEATURES_BYPASS_ALLOWLIST` entries for the two modules SHALL be
updated to document the opt-in + WARN-fallback状态 (counts and comments), as
an itemized, deliberate change — the whitelist test's design purpose, not a
bypass.

#### Scenario: whitelist changes are itemized
- **WHEN** the whitelist entries change
- **THEN** each change carries a comment naming the opt-in and the retained
  WARN fallback call site
