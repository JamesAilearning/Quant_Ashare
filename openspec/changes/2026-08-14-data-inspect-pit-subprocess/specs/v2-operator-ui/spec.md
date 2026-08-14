# Delta for v2-operator-ui

## MODIFIED Requirements

### Requirement: The UI SHALL provide a read-only inspector of the production bundle

The operator UI SHALL provide a 数据检视 page that only INSPECTS the production
qlib bundle and SHALL NOT build, ingest, or mutate any data. The page SHALL
surface: the bundle's fetch-integrity stamp (P3-4c) — clean, holey (with the
recorded holes), missing, or corrupt, each with its operator consequence; the
bundle-health summary; and an on-demand, read-only run of the PIT validator
rendered as a per-check report. The validator SHALL run in a subprocess — a
fresh Python interpreter invoking the 06 CLI with `--report-json` — never in
the UI process, because qlib is a per-process singleton and in-process
validation hard-fails once the UI session has initialized qlib for another
provider. The page SHALL render the CLI's structured report (exit code +
per-check results); a process exit code of 2 WITH a parseable report SHALL be
rendered as validation failures (a result), not as a runner error. Runner-level
failures — timeout, interpreter launch failure, CLI death before a report, or
a missing / unparseable / shape-invalid report — SHALL surface loudly with
their failure kind, never as a silent default. The transient report file SHALL
live in a temporary directory removed when the run returns; nothing in the
inspected bundle SHALL be written. The page copy SHALL state explicitly that it
inspects production data and that bundles are produced by the data pipeline,
not the UI. Read-only is machine-enforced: the page source SHALL contain no
write-side filesystem API and SHALL NOT import builder / fetcher /
orchestrator machinery, nor the validator or the qlib runtime itself.

#### Scenario: a holey bundle is surfaced with its holes
- **WHEN** the inspected bundle's integrity stamp says built-from-holey-fetch
- **THEN** the page shows the holes and states the recommend boundary refuses
  the bundle by default

#### Scenario: an unstamped or corrupt stamp is surfaced loudly
- **WHEN** the bundle has no integrity stamp, or the stamp is unreadable
- **THEN** the page says completeness cannot be confirmed (or the stamp is
  corrupt) rather than implying the bundle is clean

#### Scenario: the validator runs read-only on demand in a subprocess
- **WHEN** the operator triggers validation
- **THEN** the 06 PIT checks run in a fresh Python subprocess against the
  selected bundle and render as a per-check report parsed from the CLI's
  `--report-json` output — regardless of which provider, if any, the UI
  process itself has initialized qlib for, and repeatably within one session

#### Scenario: runner-level failures are loud
- **WHEN** the validation subprocess times out, cannot launch, dies before
  writing its report, or yields an unparseable / shape-invalid report
- **THEN** the page shows a prominent error naming the failure kind —
  never an empty table or a defaulted pass

#### Scenario: the read-only contract is machine-checked
- **WHEN** the governance suite runs
- **THEN** a source-level test fails on any write-side filesystem API, any
  builder / fetcher / orchestrator import, or any direct validator /
  qlib-runtime import in the page
