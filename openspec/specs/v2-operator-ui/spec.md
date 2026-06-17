# v2-operator-ui Specification

## Purpose
TBD - created by archiving change 2026-06-10-thin-production-inspector. Update Purpose after archive.
## Requirements
### Requirement: The UI SHALL provide a read-only inspector of the production bundle

The operator UI SHALL provide a 数据检视 page that only INSPECTS the production
qlib bundle and SHALL NOT build, ingest, or mutate any data. The page SHALL
surface: the bundle's fetch-integrity stamp (P3-4c) — clean, holey (with the
recorded holes), missing, or corrupt, each with its operator consequence; the
bundle-health summary; and an on-demand, read-only run of the PIT validator
rendered as a per-check report. The page copy SHALL state explicitly that it
inspects production data and that bundles are produced by the data pipeline,
not the UI. Read-only is machine-enforced: the page source SHALL contain no
write-side filesystem API and SHALL NOT import builder / fetcher /
orchestrator machinery.

#### Scenario: a holey bundle is surfaced with its holes
- **WHEN** the inspected bundle's integrity stamp says built-from-holey-fetch
- **THEN** the page shows the holes and states the recommend boundary refuses
  the bundle by default

#### Scenario: an unstamped or corrupt stamp is surfaced loudly
- **WHEN** the bundle has no integrity stamp, or the stamp is unreadable
- **THEN** the page says completeness cannot be confirmed (or the stamp is
  corrupt) rather than implying the bundle is clean

#### Scenario: the validator runs read-only on demand
- **WHEN** the operator triggers validation
- **THEN** the 06 PIT checks run against the production bundle and render as a
  report, and nothing on disk is written

#### Scenario: the read-only contract is machine-checked
- **WHEN** the governance suite runs
- **THEN** a source-level test fails on any write-side filesystem API or any
  builder / fetcher / orchestrator import in the page

