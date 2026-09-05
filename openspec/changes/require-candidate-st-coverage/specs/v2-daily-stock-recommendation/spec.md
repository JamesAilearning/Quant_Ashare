## ADDED Requirements

### Requirement: Daily recommendation SHALL require unambiguous current names for every otherwise eligible scored candidate

The recommendation path SHALL require exactly one row with an original non-blank string `name` in the validated current-ST snapshot for every non-NaN-scored instrument not excluded by the authoritative entry-day microstructure mask. This check SHALL precede name coercion, current-ST classification, and Top-K selection and SHALL reuse the same snapshot dataframe as the existing whole-snapshot guards. Missing rows, duplicate required `ts_code` rows (even if names agree), or null, non-string, empty, or whitespace-only required names SHALL raise `DailyRecommendationError` identifying affected codes and the name source, with no recommendation result. The system SHALL NOT infer non-ST, substitute historical names, or silently exclude unknown candidates to continue.

The existing whole-snapshot schema/freshness/bundle-consistency checks SHALL remain mandatory, including when all candidates are already masked. Missing or invalid name rows for already-masked or unscored instruments SHALL NOT cause this candidate-level refusal. Valid snapshots SHALL preserve ranking, current-ST exclusions, microstructure reason precedence, counts, output schema and cadence/HOLD semantics.

#### Scenario: an unmasked candidate has no usable name
- **WHEN** an otherwise valid snapshot omits an unmasked scored code, or its sole name is null, non-string, empty or whitespace-only
- **THEN** recommendation refuses with a classified code/source diagnostic before producing a result
- **AND** this applies even when the candidate's score is below the eventual Top-K cutoff

#### Scenario: a required code occurs more than once
- **WHEN** a required code has duplicate rows, whether their names agree or include conflicting ST and ordinary names
- **THEN** recommendation refuses independently of row order instead of selecting a row by dictionary overwrite

#### Scenario: a missing name belongs only to an already unavailable stock
- **WHEN** every unmasked scored candidate has valid unique name evidence but an entry-day suspended, one-price-locked, or otherwise authoritatively masked stock does not
- **THEN** the run succeeds with that stock excluded and its original microstructure audit reason preserved
- **AND** no current name is required for an instrument whose score was dropped as NaN

#### Scenario: complete valid evidence preserves selection
- **WHEN** a complete valid snapshot contains both ST-family and ordinary names, including a stock that is both ST and microstructure-masked
- **THEN** Top-K, audit reasons and counts match the established current-ST and entry-day microstructure behavior

#### Scenario: all candidates are masked but the whole snapshot is unusable
- **WHEN** all scored candidates are masked and the current-ST snapshot is missing, empty, malformed, stale, or inconsistent with the bundle
- **THEN** the existing whole-snapshot guard still refuses the run
