# v2-pit-analyzer-routing Specification

## Purpose

Route the two per-fold analyzers (`SignalAnalyzer`, `PerformanceAttribution`)
through the point-in-time data layer so their price fetches carry the
post-delist mask (audit P2), with an identity-preserving default and a
deliberate, evidence-carrying re-sign channel for the REGEN-2 anchor.
Shipped by `add-pit-analyzer-routing` (#320 attribution + config surface,
#321 signal routing + replay wiring + re-sign channel); the mask was proven a
no-op on the Phase-B.2 bundle's raw `$close` (anchor bit-unchanged, CI green),
so the channel stands ready-but-unused.

## Requirements

### Requirement: SignalAnalyzer and PerformanceAttribution SHALL accept PIT routing with a WARN fallback

`SignalAnalyzer.analyze` and `PerformanceAttribution.analyze` SHALL accept an
optional `pit_provider`; when supplied, their qlib price fetches
(`_fetch_returns` / `_get_instrument_returns`) SHALL route through
`pit_provider.get_features` (post-delist masking applied), and when absent the
behavior SHALL remain bit-identical to the pre-change implementation (direct
`D.features` + the existing WARN log), so independent callers are not broken.
The provider SHALL be alignment-validated against the canonical qlib runtime
the same way the existing `backtest_runner` opt-in does (no new mechanism).

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

`WalkForwardConfig` and `PipelineConfig` SHALL carry
`delisted_registry_path: str = ""`. When non-empty, the engine SHALL construct
ONE `PITDataProvider` at run start and pass it to both analyzers AND to
`BacktestRunner.run` (whose internal opt-in — alignment validation →
microstructure mask → equal-weight baseline — has existed since Phase
D.3/P0-3; the audit-P2 tail closed the P0-6 follow-up by threading the three
official call sites: walk-forward fold, single-fold pipeline, REGEN-2
replay). When empty, no provider is constructed and the WARN path runs (the
pre-change behavior, so the default is identity-preserving). A non-empty path
that does not exist SHALL fail loud at provider construction (never silently
degrade to the WARN path).

#### Scenario: default is identity-preserving
- **WHEN** a run executes with the default empty `delisted_registry_path`
- **THEN** behavior is bit-identical to the pre-change implementation (WARN
  path; the REGEN-2 anchor untouched)

#### Scenario: a bad registry path fails loud
- **WHEN** `delisted_registry_path` is non-empty but unreadable/missing
- **THEN** the run refuses at provider construction with an actionable error,
  rather than silently running the WARN path

#### Scenario: the backtest receives the same provider on official paths
- **WHEN** a canonical run executes with a configured registry
- **THEN** every official `BacktestRunner.run` invocation (walk-forward
  fold, single-fold pipeline, REGEN-2 replay) receives the run-level
  provider, so the microstructure mask and equal-weight baseline raw-field
  fetches route through the §4.3.2 layer instead of the WARN fallback (raw
  fields carry no window operators, so the mask is expected to be a no-op
  on NaN-correct bundles — the CI anchor leg judges, and any drift goes
  through the re-sign channel)

### Requirement: The REGEN-2 replay SHALL follow canonical semantics and the baseline SHALL be re-signed deliberately

The REGEN-2 replay SHALL construct the same PIT provider from the committed
frozen delisted-registry fixture (operator-signed full production snapshot,
sha256-pinned, freshness-guarded against the replay window), so the anchor
keeps measuring the CANONICAL semantics. Any baseline change SHALL be a
deliberate re-sign through the sanctioned channel
(`.github/workflows/regen-baseline.yml`, runner pinned `ubuntu-22.04`,
canonical dependency pins): the acceptance gate
(`scripts/regen/diff_baselines.py`, rules R1–R4 committed BEFORE numbers are
seen) SHALL require per-fold changes attributable to prediction-member
delisted instruments, bit-unchanged backtest metrics
(`annualized_return`/`max_drawdown`/`information_ratio`), horizon-consistent
aggregate changes, and identical fold windows; the re-sign PR SHALL commit the
diff table plus the evidence sidecar
(`walk_forward_baseline_metrics.evidence.json`), whose presence is
machine-enforced from the first re-sign onward via the frozen pre-channel
baseline pin.

#### Scenario: IC drift must be attributable
- **WHEN** a re-signed baseline differs from the old on a fold's IC
- **THEN** the diff table names the delisted, prediction-member instrument(s)
  whose masked post-delist fills explain that fold's change — an
  unattributable change aborts the re-sign

#### Scenario: backtest metrics must not move
- **WHEN** a re-signed baseline is compared to the old
- **THEN** per-fold `annualized_return`/`max_drawdown`/`information_ratio`
  are identical (the channel re-signs IC inputs only); any drift aborts

#### Scenario: a re-sign without evidence fails loud
- **WHEN** the committed baseline's content differs from the frozen
  pre-channel pin and no evidence sidecar is committed
- **THEN** the replay regression test fails the anchor leg red

### Requirement: The governance whitelist SHALL reflect the opt-in deliberately

The `PIT_FEATURES_BYPASS_ALLOWLIST` entries for the two analyzer modules SHALL
document the opt-in + WARN-fallback state (counts and comments) as an
itemized, deliberate change — the whitelist test's design purpose, not a
bypass.

#### Scenario: whitelist changes are itemized
- **WHEN** the whitelist entries change
- **THEN** each change carries a comment naming the opt-in and the retained
  WARN fallback call site
