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
