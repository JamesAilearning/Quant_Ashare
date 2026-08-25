# Delta for v2-today-workbench

## ADDED Requirements

### Requirement: The identity card SHALL show model age from the cockpit's own derivation

When the incumbent is a resolvable ensemble, the identity card SHALL append
model-age rows (newest `fit_end`, age in days, and the acceptable window for
the next member's `fit_end`) and SHALL take every one of those values from
the SAME `retrain_window` helper the ops cockpit renders, without deriving
any of them a second time.

A second implementation is the defect this repository has already paid for:
the workbench's first freshness card (`3a585e3`) re-derived an existing
verdict and got all three embedded decisions wrong. The wiring SHALL be
pinned at source level (the page calls the shared helper), because on clean
data an equal-looking copy is indistinguishable from reuse.

When the window cannot be derived, the card SHALL say so with the reason —
never a blank, a default, or a stale previous answer.

#### Scenario: an incumbent that resolves to an ensemble

- **GIVEN** an incumbent whose manifest resolves and a known retrain window
- **WHEN** the identity card renders
- **THEN** it shows newest fit_end, the age in days, and the opens/closes
  window with its state — all equal to what the ops cockpit derives from the
  same inputs

#### Scenario: an incumbent that is not a resolvable ensemble

- **GIVEN** `retrain_window` reports `known=False` with an error
- **WHEN** the identity card renders
- **THEN** a single row states the age cannot be derived, with the reason

### Requirement: The window row SHALL disclose its derived identity in visible copy

The window's derived identity SHALL be disclosed in operator-visible text
(label or value) on every surface that shows it: the repository holds no
machine-readable retrain due date — the displayed window is DERIVED from the
serving validator's member-spacing pin, and comments or docstrings do not
render. The visible copy SHALL name the spacing pin values and state that no
machine-readable due-date anchor exists.

#### Scenario: a known window on the identity card

- **GIVEN** a known retrain window
- **WHEN** the window row renders
- **THEN** its label marks the value as derived and its value carries the
  spacing-pin numbers and the missing-anchor caveat
