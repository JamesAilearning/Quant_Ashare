# Delta for v2-today-workbench

## ADDED Requirements

### Requirement: A failed update SHALL show why it failed, not only where

The failure card and the today decision queue SHALL render the failed run's
`detail` alongside the exit-code meaning and the failing stage, and SHALL both
read it from one shared helper. When the record carries no reason, they SHALL
say so explicitly rather than render a shorter line.

`detail` now carries the stage's own error line, which is the only part an
operator can act on; both surfaces previously discarded it and showed the same
sentence on all three nights of a repeated failure.

A blank where the reason belongs reads as "there is nothing more to say", while
the truth is "this run did not write down its reason" — the two lead the
operator to opposite next steps.

#### Scenario: the reason reaches the operator
- **WHEN** a failed run's `detail` carries the stage's error line
- **THEN** both the failure card and the queue item show it

#### Scenario: a missing reason is stated, not implied
- **WHEN** the record's `detail` is empty or whitespace
- **THEN** both surfaces say the run recorded no reason and point at the log

#### Scenario: the two surfaces cannot drift apart
- **WHEN** the failure line is composed
- **THEN** both surfaces obtain it from the same helper, and the page holds no
  hand-written failure line of its own
