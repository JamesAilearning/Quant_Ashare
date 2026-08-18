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

### Requirement: The config page SHALL emit every precondition its universe demands

A configuration the page reports as valid SHALL be constructible by the
engine it is submitted to. Where a universe carries construction
preconditions — `instruments="csi800"` requires
`attribution_sleeve_grouping=True`, `risk_constraints_enabled=True` and
`risk_constraints_calibration="campaign_v1"`, because official csi800
metrics without the sleeve report and the campaign constraint are not
comparable to the certified numbers — the page SHALL carry those fields in
the emitted config, expose them as controls, and refuse loudly before
launch when they are unmet.

The refusal verdict SHALL be delegated to the canonical validator rather
than restated in the UI (a UI copy of the rule is the drift that produced
the defect), and SHALL run on BOTH the render path and the submit recheck
(the operator can flip a control and click Run inside the still-enabled
frame). Preset switching SHALL normalize these fields through the SAME
reset map the cost fields use, so neither family can carry a stale value
from a previously selected preset.

Defaults for these fields SHALL mirror the canonical dataclass defaults,
NOT the csi800 contract values: a default that silently stamps campaign
semantics onto a non-csi800 run changes what that run measures.

#### Scenario: csi800 without the guard triple is refused before launch

- **GIVEN** `instruments=csi800` with any of the three guards unset
- **WHEN** the page renders, or the operator submits
- **THEN** the page refuses with the canonical validator's message naming
  every unmet precondition, and no job is started

#### Scenario: a valid csi800 config is constructible

- **GIVEN** `instruments=csi800` with all three guards set to the contract
  values
- **WHEN** the config is submitted
- **THEN** the emitted config carries the triple and the engine constructs
  it without raising

#### Scenario: other universes are not forced into campaign semantics

- **GIVEN** `instruments` is not `csi800`
- **WHEN** the page renders
- **THEN** the guards stay at their canonical defaults and no refusal fires

### Requirement: A listed run SHALL be reachable from the row that lists it

Every run the jobs list renders SHALL either be openable from its
detail action, or have that action **visibly unavailable with a
stated reason**. A row SHALL NEVER route to a page that answers "run
not found" — the record exists, so that answer is false, and it reads
as "the history is gone".

Detail pages SHALL load the **whole** filtered catalog, not a page of
it. A guessed ceiling (`page_size=<big number>`) silently drops rows the
jobs list can still paginate to, recreating the dead end this
requirement exists to remove; the loader SHALL page until it has the
filtered total and SHALL fail loudly if it cannot.

Detail pages SHALL therefore accept runs from BOTH launch sources —
the UI job directory and the CLI run catalog — because the jobs list
already merges them; a detail page that only knows one source turns
the majority of rows into dead ends.

Run types with no detail view at all (the retired data-source
inspection) SHALL keep their rows listed — the records and logs are
still real — but SHALL disable the detail action and say why.

Rows whose artifacts lie outside the console's read boundary
(`output/` and `output/operator_ui/`) SHALL NOT be listed, because they
can never be opened. Their count SHALL be disclosed on the page — a
silently truncated list reads as full coverage. The inspectability
verdict SHALL be pure path arithmetic (no per-row filesystem I/O) and
SHALL anchor relative paths at the repository root, not the process
working directory. "No per-row I/O" is load-bearing rather than
aspirational: the filter runs over every catalog row on every rerun, so
a per-row `resolve()` costs hundreds of milliseconds and stalls on a
slow or disconnected share. Lexical containment is therefore the
verdict; the resolving guard stays at the **artifact-read** boundary,
where the file access actually happens.

A lexical verdict sees through the root's own spelling and its resolved
target, but not a **third** spelling of the same directory (another
junction, an 8.3 short name, a second symlink). Such a row SHALL be set
aside and counted rather than admitted — a false negative that the
disclosure already reports, versus a false positive that would admit a
row outside the read boundary. This limit SHALL be stated where the
verdict is defined, so it is not later "fixed" by reintroducing
per-row `resolve()`.

#### Scenario: a CLI walk-forward run opens from the jobs list

- **GIVEN** a walk-forward run recorded in the CLI catalog with artifacts
  under `output/`
- **WHEN** the operator clicks through from the jobs list
- **THEN** the detail page renders that run

#### Scenario: a CLI pipeline run opens from the jobs list

- **GIVEN** a pipeline run recorded in the CLI catalog with artifacts
  under `output/`
- **WHEN** the operator clicks through from the jobs list
- **THEN** the results page renders that run rather than "run not found"

#### Scenario: an overwritten row lands on the directory that overwrote it

- **GIVEN** a catalog run id whose artifacts were overwritten
- **WHEN** the operator opens it
- **THEN** the page selects the run **now occupying that same directory**
  and names it, rather than defaulting to the first run in the list —
  which may sit in an unrelated directory
- **AND** the overwrite warning is still shown; locating the directory
  SHALL NOT be treated as having found the requested run

#### Scenario: lexically equivalent directories are one run, not two

- **GIVEN** two catalog rows spelling the same directory as
  `output/runs/a` and `output/x/../runs/a`
- **WHEN** the fold runs
- **THEN** they collapse to a single run, because both resolve to the
  same artifacts under the page's path guard

#### Scenario: the two detail pages agree on which run a row means

- **GIVEN** a run whose artifacts were overwritten by a later run writing
  to the same `output_dir`
- **WHEN** the operator opens it from either detail page
- **THEN** both pages state that the artifacts were overwritten and name
  the run now occupying that directory, rather than silently rendering
  the later run's report
- **AND** the fold that decides this — anchoring, newest-per-directory,
  and the refusal to alias overwritten ids — SHALL have a single
  implementation shared by both pages, because a per-page copy diverges

#### Scenario: a run type with no detail view says so instead of 404ing

- **GIVEN** a listed run whose type has no detail view
- **WHEN** the operator looks at its actions
- **THEN** the detail action is disabled and states why
- **AND** clicking through never reaches a "run not found" page

#### Scenario: unopenable rows are set aside and counted

- **GIVEN** catalog rows whose `output_dir` is outside the read boundary
- **WHEN** the jobs page renders
- **THEN** those rows are absent from the list
- **AND** the page states how many were set aside and why

#### Scenario: relative rows do not depend on the launcher's directory

- **GIVEN** a catalog row with a repo-relative `output_dir`
- **WHEN** the UI is started from any working directory
- **THEN** the row resolves to the same location

### Requirement: Status vocabulary SHALL be normalized at one seam

CLI-written statuses SHALL be translated to the UI vocabulary in the
normalization function, not at each display or filter site. `ok` SHALL
map to `completed` (mirroring the existing `success` → `completed`);
`partial` SHALL be preserved as itself (it is already a filter option, a
label and an icon, and folding it would erase "some folds lack IC");
unrecognized words SHALL pass through untouched rather than be mapped by
invention.

#### Scenario: a filter cannot drop the rows it just labelled

- **GIVEN** CLI rows written with status `ok`
- **WHEN** the operator filters by 已完成
- **THEN** those rows are returned

### Requirement: The walk-forward detail page SHALL disclose run identity and metric standing

The page SHALL show which run produced the numbers — universe,
benchmark, topk, cadence, **rebalance anchor**, ensemble window, label
horizon, slippage — plus the code identity (`git_commit`, with a dirty
tree stated as such). The anchor is load-bearing because it says WHICH
EVIDENCE CHAIN a report belongs to: the certified winner runs on
`fold_phase`, while `iso_week` is a separately gated re-check whose
positive net excess is one promotion condition, followed by a
serving-parameter binding. Two runs can be byte-identical in every other
field, so without the anchor the page cannot tell those chains apart.
The anchor alone SHALL NOT be presented as deciding what is production.

Before any governance wording is shown, the page SHALL establish that
the run belongs to the governed family, and SHALL derive that
membership test from the repository's own pinned artifacts rather than
restating their values in the UI. The authority is the union of

1. the certified winner's preset chain
   (`config/presets/csi800_cadence5_conservative.yaml` plus the base it
   `extends`), which pins the **experiment** semantics — cost
   convention, cadence, ensemble window, model and window sizes; and
2. the production serving parameters
   (`config/serving/csi800_n5_production.yaml`), the second level of the
   binding chain, which governance tests pin value-for-value against the
   `iso_week` re-check preset.

Membership SHALL be judged over the certified run's **resolved**
parameter set — the report-config contract's own defaults, overlaid with
the values those artifacts pin — excluding only `rebalance_anchor` and
`output_dir`, the two keys by which the certified pair itself differs.
Defaults are load-bearing: a field the YAML omits still takes a value at
run time, so judging only YAML-present keys leaves knobs like
`label_horizon_days` unguarded, and a run that flips one is a materially
different experiment wearing the certified label.

Keys whose pinned value is itself an environment template
(`${VAR:-default}`) name a data **location**, not experiment semantics.
Their literal path SHALL NOT be compared — re-expanding it under the
**viewer's** process environment would make one unchanged report gain or
lose its governance label depending on where the UI happens to run — but
they SHALL remain in the identity, compared as **configured versus
absent**. Leaving `delisted_registry_path` empty is a valid config that
disables the PIT provider and falls back to the legacy WARN masking
path: different masking and attribution semantics, therefore a different
experiment. Dropping such keys wholesale would let that run wear the
certified label. Which keys these are SHALL be derived from the value's
shape, not from a hand-kept list.

Every governance label SHALL be rendered **inside** the family gate, not
merely after it. A sibling branch is not a gate: a fully-recorded run
that fails membership can fall through into the anchor branch and be
labelled the certified winner immediately after being told it is outside
the family.

A key the candidate report does **not** record SHALL be judged against
its **contract default** — that run did not lack the knob, it ran on the
default. Where the default equals the certified value, absence is not a
disagreement (so historic reports are not ejected merely for predating a
field). Where it differs, absence IS a mismatch: a report predating
`delisted_registry_path` ran with the empty default, meaning no PIT
provider and legacy WARN masking — different semantics, not an unknown.
The page SHALL still disclose how many identity knobs were judged and
which ones the report did not record, so "in the family" is never read
as "compared item by item". Hand-picking a key list is
what this requirement exists to forbid: four review rounds each found
one more missing knob (`slippage_bps`, the constraint switches, `topk`
and `attribution_sleeve_grouping`, `ensemble_window`), and each gap let
a materially different experiment wear the certified-winner label.

If an authority artifact cannot be read, or does not parse as a
non-empty mapping, the page SHALL fail loudly. It MUST NOT fall back to
an empty requirement set — that inverts the guard, marking **every**
run as governed exactly when the authority is unavailable.

For a run outside the family the page SHALL say which knobs disagree,
rather than only that it is outside.

The page SHALL render the engine's `metric_status` stamp. A MISSING
stamp SHALL be labelled as missing and explicitly NOT treated as
`official` — runs predating the stamp are the common case, and
defaulting them to official voids the guard the stamp exists for. A
non-official status SHALL be surfaced as a warning stating the numbers
are not usable for promotion adjudication. When `metrics_purpose`
disagrees with `metric_status`, both SHALL be shown (a declared purpose
can only worsen the verdict, never improve it).

#### Scenario: certified and reference runs are distinguishable

- **GIVEN** two runs **inside the governed family** and identical
  except `rebalance_anchor`
- **WHEN** each is opened
- **THEN** the page names the anchor of each, identifying `fold_phase`
  as the certified winner's anchor and `iso_week` as the separately
  gated production-serving anchor

#### Scenario: a sensitivity arm is not labelled the certified winner

- **GIVEN** a run on the certified universe, benchmark, cadence and
  phase but at a different slippage than the promotion profile
- **WHEN** it is opened
- **THEN** the page withholds every governance label and states that
  the cost convention is what places it outside the family

#### Scenario: a missing metric stamp is never read as official

- **GIVEN** a report with no `metric_status` key
- **WHEN** the page renders
- **THEN** it states the stamp is absent and that absence ≠ official

### Requirement: The config page SHALL offer only presets it can actually run

The preset selector SHALL list only presets whose shape this page can
emit. Campaign-frozen configurations (pre-registration / certification
evidence) SHALL NOT appear as ordinary options: the page emits a
standalone config, does not resolve `extends`, and has no widgets for
the cadence / scope / output keys those files carry — selecting one
would display its name while emitting a daily pipeline config, i.e. the
cadence the operator reads is not the cadence that will run.

The runnable/frozen judgment SHALL be derived from a marker the two
runtimes reject and the UI always writes (`mode`), so it stays correct
without a hand-maintained registry. Frozen presets SHALL still be
visible read-only, with the reasons they cannot run and the command-line
way to reproduce them. Active-preset detection SHALL consider runnable
presets only, so the reported preset is always one the selector offers.

#### Scenario: campaign files are not selectable but are visible

- **WHEN** the config page renders
- **THEN** campaign-frozen presets are absent from the selector
- **AND** they are listed read-only with why they cannot run here

#### Scenario: a newly saved preset needs no registration

- **GIVEN** the operator saves a preset from this page
- **WHEN** the page reloads
- **THEN** it appears as a runnable option without any registry edit

### Requirement: The page SHALL NOT describe research presets as production

No preset SHALL be labelled as the production configuration. The page
SHALL state that it emits daily research configs, and SHALL name where
the production serving configuration actually lives. A preset whose
universe/benchmark/topk coincide with production SHALL still be
described by what it is, because coincidence on some fields is exactly
what makes an operator read it as a production copy.

#### Scenario: the full-market baseline is named honestly

- **WHEN** the operator reads the preset help
- **THEN** the all-market preset is described as a baseline, not as
  production
- **AND** the production serving configuration is named

### Requirement: Result surfaces SHALL declare their cost/benchmark convention

Every rendered performance number SHALL state its convention. Numbers
derived from the engine's return series are **absolute gross** (neither
benchmark nor cost removed — the upstream series adds realized cost back
into the already-cost-deducted return); numbers derived from the risk
analysis are **net excess**. Where both conventions appear on one card,
each SHALL carry its own label.

Where two surfaces render the same-sounding quantity under different
conventions — notably the max-drawdown card (net excess, arithmetic
accumulation) versus the drawdown chart (absolute gross, geometric
accumulation) — the page SHALL state that they are not the same number
and SHALL name EVERY axis of the difference (benchmark, cost,
accumulation method). Attributing the gap to cost alone is prohibited:
cost is typically the smallest of the three, so a cost-only explanation
leaves the discrepancy unexplained.

#### Scenario: gross numbers are not read as net

- **WHEN** the results page renders the primary metric, total return,
  NAV curve or monthly returns
- **THEN** each states it is absolute gross

#### Scenario: the two drawdowns are reconciled

- **WHEN** the risk card and the drawdown chart are both rendered
- **THEN** the page states they are not the same number
- **AND** names benchmark, cost AND accumulation method as the reasons

