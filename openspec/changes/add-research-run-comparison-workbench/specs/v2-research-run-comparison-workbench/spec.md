## ADDED Requirements

### Requirement: Select bounded research runs from the existing catalog

The operator UI SHALL provide a research-only comparison workbench that lets
an operator select two through five existing pipeline or walk-forward runs
from the existing unified job catalog.  It SHALL keep the selected full run IDs
in the URL and restore a valid selection from that URL.  The workbench SHALL
not launch, cancel, mutate, delete, or otherwise operate on a run.

#### Scenario: A valid URL restores two selected runs

- **WHEN** an operator opens the comparison page with two known selected run
  IDs in its URL parameter
- **THEN** the page selects those exact run IDs
- **AND** renders only read-only comparison controls and artifacts

#### Scenario: The selection is outside the allowed size

- **WHEN** fewer than two or more than five runs are selected
- **THEN** the page explains the two-to-five requirement
- **AND** does not assign a comparison rank

### Requirement: Establish comparability before controlled ordering

Before assigning a controlled research ordering, the workbench SHALL compare
the selected runs' universe, training/validation/testing windows, benchmark,
signal-to-execution lag, canonical cost/exchange controls, and data
provenance.  A missing, malformed, or unequal required value SHALL block the
ordering and identify the affected field and run ID.  The workbench SHALL NOT
invent a missing value, recompute metrics, or call a non-comparable selection
equivalent.

#### Scenario: Equal complete contracts permit an ordering

- **WHEN** two or more selected runs have complete and equal required
  comparison-contract values
- **THEN** the page may show a stable read-only ordering by the existing
  reported information ratio
- **AND** labels the ordering as research-only and `扣费后超额` in meaning

#### Scenario: A data-provenance value is unavailable

- **WHEN** one selected run has no canonical runtime provenance in its report
- **THEN** the page labels that value unavailable with its reason
- **AND** blocks metric ordering for the whole selection

### Requirement: Present existing evidence and precise read-only references

For every selected run, the workbench SHALL display only values already present
in its artifacts, including available metrics, model/configuration identity,
data provenance, and walk-forward fold stability evidence where applicable.
It SHALL distinguish unavailable values from zero or success.  It SHALL offer
the exact run ID's Results link, Walk Forward link when applicable, and
read-only configuration and log references.

#### Scenario: A selected walk-forward run has fold evidence

- **WHEN** a selected run has a valid `walk_forward_report.json` with fold
  records
- **THEN** the page displays its existing aggregate metrics and fold stability
  evidence without calculating replacement fold metrics

#### Scenario: A required artifact is missing or invalid

- **WHEN** a selected run's configuration or report artifact is missing,
  unreadable, malformed, or outside the output read boundary
- **THEN** the page presents an explicit needs-verification issue
- **AND** blocks controlled ordering rather than treating the absent artifact
  as an empty or comparable result
