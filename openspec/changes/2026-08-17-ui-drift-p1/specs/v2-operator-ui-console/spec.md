# Delta for v2-operator-ui-console

## ADDED Requirements

### Requirement: A listed run SHALL be reachable from the row that lists it

Every run the jobs list renders SHALL be openable from its detail
action. Detail pages SHALL therefore accept runs from BOTH launch
sources — the UI job directory and the CLI run catalog — because the
jobs list already merges them; a detail page that only knows one source
turns the majority of rows into dead ends that read as "the history is
gone".

Rows whose artifacts lie outside the console's read boundary
(`output/` and `output/operator_ui/`) SHALL NOT be listed, because they
can never be opened. Their count SHALL be disclosed on the page — a
silently truncated list reads as full coverage. The inspectability
verdict SHALL be pure path arithmetic (no per-row filesystem I/O) and
SHALL anchor relative paths at the repository root, not the process
working directory.

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

The page SHALL render the engine's `metric_status` stamp. A MISSING
stamp SHALL be labelled as missing and explicitly NOT treated as
`official` — runs predating the stamp are the common case, and
defaulting them to official voids the guard the stamp exists for. A
non-official status SHALL be surfaced as a warning stating the numbers
are not usable for promotion adjudication. When `metrics_purpose`
disagrees with `metric_status`, both SHALL be shown (a declared purpose
can only worsen the verdict, never improve it).

#### Scenario: certified and reference runs are distinguishable

- **GIVEN** two runs identical except `rebalance_anchor`
- **WHEN** each is opened
- **THEN** the page names the anchor of each, identifying `fold_phase`
  as the certified winner's anchor and `iso_week` as the separately
  gated production-serving anchor

#### Scenario: a missing metric stamp is never read as official

- **GIVEN** a report with no `metric_status` key
- **WHEN** the page renders
- **THEN** it states the stamp is absent and that absence ≠ official
