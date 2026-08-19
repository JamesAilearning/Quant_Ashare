# Delta for v2-run-catalog

## ADDED Requirements

### Requirement: The catalog SHALL be anchored, and SHALL refuse runs it cannot index honestly

The default catalog location SHALL be derived from the repository root,
not from the process working directory. A relative default means the same
code writes to a different file depending on where it was launched — the
same defect class as the inspectability anchoring fixed earlier for the
operator console.

Before appending, the writer SHALL check that the record's `output_dir`
lies inside the output tree **of the catalog it is about to write to**,
and SHALL skip the append with a warning naming the reason when it does
not. Anchoring the check to the catalog rather than to a hardcoded root
keeps a separate worktree's runs in that worktree's own catalog.

A run whose artifacts live outside the output tree can never be opened
from the console — that is the console's pinned read boundary — so
cataloguing it produces a row that is guaranteed to be set aside.
Measured on the operator's machine: **3560 rows, of which 3455 (97.1%)
point outside the tree** — 2279 at system temp directories and 1176 at
four hardcoded test fixture paths. All 105 in-tree rows still have their
directory on disk.

The skip SHALL be a warning, not an exception, matching this function's
existing contract (an `OSError` on append is already logged and swallowed
because "run results are still intact in the per-run directory"). The
catalog is a side record, not the run's product. A caller that genuinely
needs to index an out-of-tree run SHALL pass `catalog_path` explicitly.

#### Scenario: a run writing outside the output tree is not catalogued

- **GIVEN** a run whose `output_dir` is a temporary directory
- **WHEN** the engine appends its catalog record with the default path
- **THEN** nothing is appended, and the reason is logged

#### Scenario: the default location does not depend on the launch directory

- **GIVEN** the writer is invoked from any working directory
- **WHEN** no explicit `catalog_path` is given
- **THEN** the record lands in the repository's own catalog

#### Scenario: an explicit catalog path is honoured as given

- **GIVEN** a caller passing `catalog_path` explicitly
- **WHEN** the record's `output_dir` lies inside that catalog's tree
- **THEN** the record is appended
