## ADDED Requirements

### Requirement: The sidebar SHALL surface REGEN-2 anchor health

The operator UI sidebar SHALL render a persistent anchor-health badge showing:
(1) the canonical baseline's content identity — the short (8-hex) CRLF→LF
normalized SHA-256 of `tests/regression/fixtures/walk_forward_baseline_metrics.json`,
computed with the SAME algorithm the anchor regression test pins; (2) the last
re-sign — the date and short commit of the baseline file's last-touch commit;
(3) whether the `walk_forward_baseline_metrics.evidence.json` sidecar is
present (absent renders an explicit legacy marker, since the evidence channel
is mandatory from the next re-sign onward); and (4) the latest completed
conclusion of the CI anchor leg (the `test (ubuntu-latest, 3.12)` job of the
`test.yml` workflow on `main`), resolved via the local `gh` CLI.

#### Scenario: healthy anchor renders identity and green leg
- **WHEN** the baseline file is readable, its last-touch commit is resolvable
  and the latest completed anchor-leg conclusion is `success`
- **THEN** the badge shows the sha8, the re-sign date+commit and a green
  state for the CI leg

#### Scenario: missing evidence sidecar is marked, not hidden
- **WHEN** the evidence sidecar does not exist next to the baseline
- **THEN** the badge renders an explicit legacy/no-evidence marker

### Requirement: Anchor-health probes SHALL be fail-soft, cached and non-blocking

Badge probes SHALL never block or crash the page: the `gh` CLI is an OPTIONAL
dependency — absence, authentication failure, subprocess timeout or unparsable
output SHALL degrade the CI element to an explicit "unknown" state carrying an
honest reason, never a fabricated or stale-presented conclusion. A shallow
clone or unavailable `git` SHALL degrade the re-sign element to "unknown"
rather than guessing. Probes SHALL run only on page render behind a TTL cache
(pull-based); the badge SHALL NOT introduce any background polling loop, and
SHALL perform no write or run-triggering operation of any kind.

#### Scenario: gh unavailable degrades honestly
- **WHEN** the `gh` executable is missing or times out
- **THEN** the CI element renders "unknown" with the reason, and the rest of
  the badge (sha / re-sign / evidence) still renders from local data

#### Scenario: no background polling
- **WHEN** the operator leaves the console open without interacting
- **THEN** no probe fires until the next rerender after the cache TTL expires
