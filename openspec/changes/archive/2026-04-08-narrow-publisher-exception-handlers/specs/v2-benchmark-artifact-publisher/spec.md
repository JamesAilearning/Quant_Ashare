## ADDED Requirements

### Requirement: Publisher SHALL NOT swallow unexpected exceptions inside frame flattening

`BenchmarkArtifactPublisher._flatten_close_frame` SHALL only catch
narrow, expected pandas/qlib API exception types
(`AttributeError`, `TypeError`, `ValueError`) when applying its
shape-tolerance fallbacks. It SHALL NOT use bare `except Exception:`,
which would mask programmer bugs (e.g. ImportError, NameError) and
turn them into a misleading "qlib provider returned no rows" error
later in the publish flow.

#### Scenario: minimal duck-typed frame is parsed correctly
- **WHEN** `_flatten_close_frame` receives an object that exposes
  `columns`, `iterrows()`, `reset_index()`, and the expected
  `datetime` / `$close` columns
- **THEN** it returns a list of `(iso_date, close_value)` tuples
  sorted ascending by date

#### Scenario: None input is treated as empty
- **WHEN** `_flatten_close_frame(None)` is called
- **THEN** the result is an empty list

#### Scenario: input without `reset_index` is treated as empty
- **WHEN** the frame argument is an object that lacks `reset_index`
- **THEN** the result is an empty list (an `AttributeError` is caught)
- **AND** no other exception types are silently swallowed
