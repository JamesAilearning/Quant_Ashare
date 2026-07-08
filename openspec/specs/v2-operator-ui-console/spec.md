# v2-operator-ui-console Specification

## Purpose

Define governance and safety requirements for the operator-facing
Streamlit console that launches CLI-compatible runs and displays
existing runtime artifacts.
## Requirements
### Requirement: Operator UI SHALL require explicit provider URI

The operator UI SHALL reject any run configuration that does not
include a non-empty `provider_uri`. The UI SHALL NOT silently fall
back to any machine-local default data bundle path.

#### Scenario: provider URI is omitted

- **WHEN** the operator fills a run configuration form without entering a `provider_uri`
- **THEN** the Run button is disabled
- **AND** a validation error is displayed

#### Scenario: provider URI is whitespace-only

- **WHEN** `provider_uri` is provided but consists only of whitespace
- **THEN** the Run button is disabled
- **AND** the same validation error is displayed

---

### Requirement: Operator UI SHALL launch official runs only through existing CLI-compatible entrypoints

The operator UI SHALL NOT import or call `Pipeline.run()` or
`WalkForwardEngine.run()` directly. All runs SHALL be executed by
launching the existing CLI scripts as subprocesses with `shell=False`.
The Streamlit server launcher SHALL bind to loopback by default unless
the operator explicitly supplies a different `--server.address`.

#### Scenario: pipeline run is launched

- **WHEN** the operator clicks Run for a pipeline configuration
- **THEN** a subprocess is started with arguments `[sys.executable, "main.py", config_path]`
- **AND** `shell=False` is used

#### Scenario: walk-forward run is launched

- **WHEN** the operator clicks Run for a walk-forward configuration
- **THEN** a subprocess is started with arguments `[sys.executable, "scripts/run_walk_forward.py", config_path]`
- **AND** `shell=False` is used

#### Scenario: UI launcher is started without an explicit address

- **WHEN** the operator runs `python scripts/run_ui.py`
- **THEN** the Streamlit command includes `--server.address 127.0.0.1`
- **AND** the UI is not exposed through an external network interface by default

#### Scenario: UI launcher is started with an explicit address

- **WHEN** the operator runs `python scripts/run_ui.py --server.address 0.0.0.0`
- **THEN** the launcher preserves the explicit address
- **AND** it does not add a competing default address flag

---

### Requirement: Operator UI SHALL derive accepted config keys from canonical config dataclasses

The operator UI SHALL derive Pipeline and WalkForward accepted config
keys from `PipelineConfig` and `WalkForwardConfig` rather than keeping
a hand-maintained duplicate allow-list that can drift from the CLI
contracts.

#### Scenario: PipelineConfig gains or removes a field

- **WHEN** `PipelineConfig` dataclass fields change
- **THEN** the UI pipeline config key set reflects the same dataclass fields
- **AND** unknown UI config keys continue to hard-fail

#### Scenario: WalkForwardConfig gains or removes a field

- **WHEN** `WalkForwardConfig` dataclass fields change
- **THEN** the UI walk-forward config key set reflects the same dataclass fields
- **AND** it additionally allows only the qlib runtime keys `provider_uri` and `region`
- **AND** unknown UI config keys continue to hard-fail

---

### Requirement: Operator UI SHALL NOT recompute official metrics

The operator UI SHALL present results by reading existing report and
chart artifacts. It SHALL NOT implement any new revenue, IC,
attribution, backtest, or factor metric calculation.

#### Scenario: results page loads

- **WHEN** the operator opens the Results page for a completed run
- **THEN** all displayed metrics are read from `pipeline_report.json` or `walk_forward_report.json`
- **AND** no new Python computation of `annualized_return`, `information_ratio`, `max_drawdown`, or IC is performed

#### Scenario: a metric field is absent

- **WHEN** a report artifact does not contain an expected metric field
- **THEN** the UI displays "unavailable" for that metric
- **AND** does not attempt to compute a substitute value

---

### Requirement: Operator UI SHALL read official results only from existing report and chart artifacts

The operator UI SHALL restrict file access to the `output/` and
`output/operator_ui/` directory trees. Path traversal outside these
roots SHALL be rejected.

#### Scenario: report path is inside allowed root

- **WHEN** `report_reader` is asked to read a report under `output/runs/xxxx/`
- **THEN** the path is accepted

#### Scenario: report path escapes allowed root

- **WHEN** `report_reader` is asked to read a path outside `output/`
- **THEN** a `ValueError` is raised

---

### Requirement: Operator UI SHALL store generated configs and job logs under output/operator_ui/jobs

Each UI-launched run SHALL create an isolated job directory under
`output/operator_ui/jobs/<job_id>/` containing at minimum:
`config.yaml`, `job.json`, `stdout.log`, and `stderr.log`.

#### Scenario: a job is started

- **WHEN** `JobManager.start()` is called
- **THEN** a job directory is created under `output/operator_ui/jobs/<job_id>/`
- **AND** `config.yaml` is written
- **AND** `job.json` is written with `status: "running"`
- **AND** `stdout.log` and `stderr.log` are opened for writing

---

### Requirement: Operator UI SHALL support stopping a running job

The operator UI SHALL support stopping a job launched through the UI.
Stopping SHALL terminate the runner process and its child CLI process
tree on Windows. The UI SHALL NOT mark a job as `stopped` unless the
termination command succeeds.

#### Scenario: a running job is stopped

- **WHEN** the operator clicks Stop for a job with status "running"
- **THEN** `taskkill /F /T /PID <runner_pid>` is executed with `shell=False`
- **AND** `job.json` is updated to `status: "stopped"` with `ended_at`

#### Scenario: stopping a running job fails

- **WHEN** the termination command exits non-zero
- **THEN** `JobManager.stop()` raises a typed job manager error
- **AND** `job.json` is updated to `status: "stop_failed"`
- **AND** the job is not represented as successfully stopped

#### Scenario: stopping a job without a recorded PID

- **WHEN** `job.json` has no runner process id
- **THEN** `JobManager.stop()` raises a typed job manager error
- **AND** `job.json` is updated to `status: "stop_failed"`
- **AND** no termination command is executed

---

### Requirement: Operator UI SHALL keep research and factor-mining non-canonical

Factor mining and research features SHALL NOT be enabled in this PR.
The UI MAY include a disabled placeholder labelled "Research Lab" and
explicitly marked as non-canonical and research-only.

#### Scenario: research placeholder is present

- **WHEN** the operator navigates the UI
- **THEN** a "Research Lab" or "Factor Mining" entry MAY be present
- **AND** it SHALL be disabled
- **AND** it SHALL be labelled as research-only / non-canonical

### Requirement: Operator UI SHALL NOT sanction training on a non-production inspection bundle

The operator UI SHALL NOT invite or accept using a one-off Tushare inspection
bundle (a `provider_uri` under `output/operator_ui/results/<job>/qlib_provider`)
as a training or backtest data source, because such a bundle is non-production
(no survivorship masking, ad-hoc adjust mode, no pipeline provenance) and
training on it silently diverges from the production bundle built by the
data-pipeline scripts. No UI copy SHALL direct the operator to paste an
inspection bundle's path into a training `provider_uri`; any UI surface that
references such a bundle SHALL carry an explicit do-not-train warning. EVERY
launch path — single-fold pipeline AND walk-forward (rolling) — SHALL fail loud
and refuse a `provider_uri` that points at an
`operator_ui/results/<job>/qlib_provider` inspection bundle (the refusal SHALL
NOT be limited to a single mode's guard), while a production bundle (not under
`operator_ui/results`) SHALL pass unaffected.

#### Scenario: a UI inspection bundle is rejected as a training source
- **WHEN** a training run's `provider_uri` points at an
  `…/operator_ui/results/<job>/qlib_provider` inspection bundle
- **THEN** the training-input guard fails loud with an explicit error and the
  run is refused
- **AND** the error directs the operator to use a production bundle built by the
  data-pipeline scripts

#### Scenario: the walk-forward launch path also refuses an inspection bundle
- **WHEN** an operator selects walk-forward (rolling) validation and points the
  run's `provider_uri` at an `…/operator_ui/results/<job>/qlib_provider`
  inspection bundle
- **THEN** the launch is refused with the same explicit error — the refusal is
  not limited to the single-fold pipeline path

#### Scenario: a production bundle is accepted
- **WHEN** a training run's `provider_uri` points at a production bundle that is
  NOT under `operator_ui/results`
- **THEN** the non-production guard does not fire and the run proceeds through
  the remaining date / instrument / embargo checks

#### Scenario: UI copy does not invite training on an inspection bundle
- **WHEN** an operator-UI page references a Tushare inspection bundle's
  `qlib_provider` path
- **THEN** the copy carries an explicit do-not-train warning and never tells the
  operator to use it as a training `provider_uri`

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

