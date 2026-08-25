# Delta for v2-run-center-page

## ADDED Requirements

### Requirement: 进度归属 SHALL 以运行边界为准，取不到边界时如实说不知道

The run-center page SHALL attribute the fetch-progress line it surfaces to a
run ONLY when the window it read covers the whole log and every boundary in it
names this provider. When those hold and the boundary's stamp equals the
`started_at` of the status record the page is displaying, the progress is
presented as that displayed run's; when the stamp differs, the page SHALL say
the log and the status artifact disagree and that the progress belongs to the
BOUNDARY's run, not the displayed record — attribution to that run stays
certain, its identity is just not the one on screen. When the window or
exclusivity precondition fails the page SHALL say attribution is unknown AND
which precondition failed — truncated window, foreign boundary present, no
boundary — exactly as honestly as it did before boundaries existed, and SHALL
NOT substitute any heuristic.

The progress reader previously could not attribute at all: log lines carry only
`HH:MM:SS`, so a line from yesterday's 21:00 run is indistinguishable from
today's. Four heuristics were tried and rejected; the recorded conclusion was
that the writer had to emit a dated boundary first. It now does.

The page SHALL NOT turn this into a percentage bar. The reason recorded when
that was declined has not changed: the progress line's denominator is one
endpoint-year's ticker count, while fetch is only the second of six stages, so
rendering it as a bar tells the operator the run is fractionally complete when
it is not.

#### Scenario: progress is attributed only under the full preconditions
- **WHEN** the window read covers the whole log, every boundary in it names
  this provider, a progress line follows the last one, and that boundary's
  stamp equals the displayed status record's `started_at`
- **THEN** the page presents that progress as certainly belonging to that run

#### Scenario: any failed precondition keeps the honest disclosure, with the reason
- **WHEN** the window is truncated, a foreign boundary is present, or no
  boundary is visible
- **THEN** the page says attribution is unknown and states which of those it
  was — a truncated window is the common case and must not be described as
  "no boundary"

#### Scenario: a status mismatch is a disagreement disclosure, not unknown
- **WHEN** the preconditions hold but the boundary's stamp differs from the
  displayed status record's `started_at` — the two artifacts advanced
  independently
- **THEN** the page says they disagree and that the progress belongs to the
  boundary's run rather than the displayed record, matching
  `v2-daily-data-update`'s scenario — one semantic, stated once

#### Scenario: a foreign boundary is ignored, and defeats certainty
- **WHEN** a boundary naming a different provider directory appears anywhere
  in the window
- **THEN** it is never used to attribute this provider's progress, and its
  mere presence makes attribution unknown — the providers share one log and
  their lines can interleave

#### Scenario: progress is still not a percentage
- **WHEN** a progress line is surfaced
- **THEN** it is shown as the counts it actually is, not as a completion bar
