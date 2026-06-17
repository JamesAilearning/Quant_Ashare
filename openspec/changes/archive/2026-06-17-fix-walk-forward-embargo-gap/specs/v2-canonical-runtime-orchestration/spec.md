## ADDED Requirements

### Requirement: Walk-forward fold generation SHALL embargo the Alpha158 label lookahead

`WalkForwardEngine` SHALL generate fold boundaries such that, for every
fold, there are at least `LABEL_LOOKAHEAD_DAYS` trading days strictly
between `train_end` and `valid_start`, and at least `LABEL_LOOKAHEAD_DAYS`
trading days strictly between `valid_end` and `test_start`. The gap SHALL
be created by an embargo gap (the gap trading days belong to no segment),
NOT by weakening, lowering, or bypassing the embargo guard
(`src/data/_segment_embargo.py`). The gap size SHALL be read from
`LABEL_LOOKAHEAD_DAYS` so the generator and the guard cannot drift.

The month-aligned start anchors (`train_start`, `valid_start`,
`test_start`) and `test_end` SHALL be preserved; the embargo gap SHALL be
created by pulling the segment end boundaries (`train_end`, `valid_end`)
back onto the trading calendar.

#### Scenario: generated folds satisfy the embargo guard

- **WHEN** `WalkForwardEngine` generates folds for a config whose nominal
  month-aligned boundaries would be adjacent
- **THEN** every generated fold passes `validate_segment_embargo`
  (both `train_end→valid_start` and `valid_end→test_start` have at least
  `LABEL_LOOKAHEAD_DAYS` trading days between them)
- **AND** `FeatureDatasetBuilder.build` does not reject any fold for an
  embargo violation

#### Scenario: train label window does not reach into the valid segment

- **WHEN** a fold is generated with `LABEL_LOOKAHEAD_DAYS = 2`
- **THEN** the trading days the last train row's Alpha158 label reads
  (`train_end` + 1 and + 2 trading days) lie strictly inside the
  discarded embargo gap
- **AND** none of those days fall within `[valid_start, valid_end]`

#### Scenario: the embargo guard is not weakened

- **WHEN** this change is applied
- **THEN** `src/data/_segment_embargo.py`, `LABEL_LOOKAHEAD_DAYS`, and
  `FeatureDatasetBuilder`'s embargo validation are unchanged
- **AND** the fold generator obtains its gap size from
  `LABEL_LOOKAHEAD_DAYS` rather than a hardcoded constant

#### Scenario: quarter-grid fold anchors are preserved

- **WHEN** folds are generated over a multi-year range with quarterly
  stepping
- **THEN** `valid_start` and `test_start` remain on their month-aligned
  nominal dates (the embargo gap is taken from the segment tails, not by
  shifting the start anchors)
