# Delta for v2-run-center-page

## ADDED Requirements

### Requirement: A stamped progress line SHALL be attributed by its stamp

The reader SHALL attribute a progress line to this run when it follows this run's own boundary and carries this provider's stamp, and SHALL NOT additionally require that the read window be complete or that every boundary in it be this provider's.

Those two requirements exist only because unstamped lines carry no identity of
their own. They cost the page almost every real attribution: production logs are
read tail-first, so the window is truncated by construction, and a sibling
provider's boundary can land in it at any time. A stamped line answers "whose
line is this" directly, and single-flight guarantees a provider never runs
concurrently with itself — so this provider's own boundary still fixes WHICH
run, while the stamp fixes WHOSE lines.

The stamp SHALL be validated by full round trip against this provider's
identity, the same way the boundary's identity is validated. Normalising,
trimming, or case-folding the stamp before comparison is FORBIDDEN: it lets a
spelling the writer cannot produce be laundered into this provider's identity,
and the page then attributes another provider's progress in a definite voice.

An unstamped line SHALL NOT be attributed by the stamped path. Treating it as
this provider's would reinstate exactly the guess that was disproven and
guarded against.

The stamped and unstamped paths SHALL share one boundary-validation
implementation. Two copies drift, and the drifting one accepts a corrupt
boundary as a legitimate starting point.

Where attribution fails, the reported reason SHALL be the most specific one
available across both paths: a corrupt boundary SHALL be reported as corrupt
rather than as a truncated window, which would send the operator to widen a
read window when the problem is elsewhere.

#### Scenario: a truncated window still attributes stamped lines

- **GIVEN** a read window that does not cover the whole log, containing this
  run's boundary followed by progress lines stamped with this provider
- **WHEN** the page reports progress
- **THEN** it attributes that progress to this run

#### Scenario: an interleaved sibling no longer blocks attribution

- **GIVEN** this run's boundary, then this provider's stamped progress, then
  another provider's boundary and stamped progress, then more of this
  provider's stamped progress
- **WHEN** the page reports progress
- **THEN** it reports this provider's latest stamped line as attributed

#### Scenario: unstamped lines keep the previous behaviour

- **GIVEN** this run's boundary followed by progress lines with no stamp, in a
  truncated window
- **WHEN** the page reports progress
- **THEN** it shows the last line in the window and states that attribution
  cannot be established

### Requirement: Progress attribution SHALL NOT depend on the log read window

The reader SHALL attribute a progress line to this run when the line carries
this run's own one-time run identity, without requiring a run boundary to be
present in the read window at all.

The provider stamp answers "whose line is this". It cannot answer "which run
wrote it" — that still needs the run boundary to separate one run from the
next. And the boundary is not readable in production: the page reads only the
log's trailing window, while a fetch writes a progress line every 200 tickers,
so a single endpoint-year of progress overruns the window. The boundary is
pushed out shortly after a run starts, and stamped attribution degrades to
"window truncated" for the whole of the workload it exists to serve.

The run identity SHALL be recorded at BOTH ends outside the log window: in the
run status artifact, and on every progress line. Comparison SHALL be exact
equality of the two. Because neither end travels through the trailing window,
truncation cannot affect the verdict.

This also settles what the provider stamp cannot: a line left in the window by
this provider's PREVIOUS run carries an identical provider stamp, and only a
per-run identity distinguishes the two.

A malformed run stamp on a line SHALL simply fail to match; it SHALL NOT be
reported as log corruption, because one dirty line must not destroy attribution
for the whole page. A malformed run identity in the STATUS ARTIFACT is the
opposite: it SHALL make the record corrupt, because reading it as "no identity"
would silently drop attribution back to a path that cannot answer.

Boundary attribution SHALL remain as the fallback for logs written before the
run identity existed and for hand-run fetches.

The page SHALL dispatch on WHICH source established attribution, not on whether
a boundary stamp equals the artifact's start time. The run-identity path reads
no boundary and therefore carries no boundary stamp; comparing that empty value
against the artifact would report a definitely-attributed run as a mismatch
between log and artifact.

Where a run identity is known and the window's progress lines carry only OTHER
runs' identities, the reported reason SHALL say so rather than "window
truncated" — the latter sends the operator to widen a read window when this
run simply has not written a progress line yet.

#### Scenario: a window with no boundary at all still attributes

- **GIVEN** a read window containing progress lines stamped with this run's
  identity and no run boundary whatsoever
- **WHEN** the page reports progress
- **THEN** it attributes the latest such line to this run

#### Scenario: the previous run's leftover line is not collected

- **GIVEN** a window holding this provider's line from an earlier run followed
  by a line from this run
- **WHEN** the page reports progress
- **THEN** it reports this run's line, not the earlier one

#### Scenario: only other runs' lines are present

- **GIVEN** a known run identity and a window whose progress lines all carry a
  different run identity
- **WHEN** the page reports progress
- **THEN** it states that no line belongs to this run, rather than reporting a
  truncated window
