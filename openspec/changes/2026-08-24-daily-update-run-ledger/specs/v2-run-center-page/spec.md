# Delta for v2-run-center-page

## ADDED Requirements

### Requirement: 进度归属 SHALL 以运行边界为准，取不到边界时如实说不知道

The run-center page SHALL attribute the fetch-progress line it surfaces by
locating the most recent run boundary in the log text it read, and SHALL treat
a boundary naming another provider as not this run's. When the text contains no
boundary the page SHALL say attribution is unknown, exactly as it did before
boundaries existed, and SHALL NOT substitute any heuristic for the missing
boundary.

The progress reader previously could not attribute at all: log lines carry only
`HH:MM:SS`, so a line from yesterday's 21:00 run is indistinguishable from
today's. Four heuristics were tried and rejected; the recorded conclusion was
that the writer had to emit a dated boundary first. It now does.

The page SHALL NOT turn this into a percentage bar. The reason recorded when
that was declined has not changed: the progress line's denominator is one
endpoint-year's ticker count, while fetch is only the second of six stages, so
rendering it as a bar tells the operator the run is fractionally complete when
it is not.

#### Scenario: progress after a boundary is attributed to that run
- **WHEN** the text read contains a boundary for this provider and a progress
  line after it
- **THEN** the page presents that progress as belonging to the current run

#### Scenario: no boundary in the window keeps the honest disclosure
- **WHEN** the text read contains no boundary
- **THEN** the page keeps saying which run the line belongs to is unknown

#### Scenario: a foreign boundary is ignored
- **WHEN** the only boundary present names a different provider directory
- **THEN** it is not used to attribute this provider's progress

#### Scenario: progress is still not a percentage
- **WHEN** a progress line is surfaced
- **THEN** it is shown as the counts it actually is, not as a completion bar
