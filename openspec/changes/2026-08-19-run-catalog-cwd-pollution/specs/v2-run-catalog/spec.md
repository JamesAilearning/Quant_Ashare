# Delta for v2-run-catalog

## ADDED Requirements

### Requirement: The catalog SHALL be anchored, and SHALL refuse runs it cannot index honestly

The default catalog location SHALL be derived from the repository root,
not from the process working directory. A relative default means the same
code writes to a different file depending on where it was launched — the
same defect class as the inspectability anchoring fixed earlier for the
operator console.

Before appending **to that default catalog**, the writer SHALL check that
the record's `output_dir` lies inside the repository's output tree, and
SHALL skip the append with a warning naming the reason when it does not.

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
catalog is a side record, not the run's product.

#### Scenario: a run writing outside the output tree is not catalogued

- **GIVEN** a run whose `output_dir` is a temporary directory
- **WHEN** the engine appends its catalog record with the default path
- **THEN** nothing is appended, and the reason is logged

#### Scenario: the default location does not depend on the launch directory

- **GIVEN** the writer is invoked from any working directory
- **WHEN** no explicit `catalog_path` is given
- **THEN** the record lands in the repository's own catalog

### Requirement: The boundary SHALL be named, not inferred from where the catalog file sits

The output tree the check enforces SHALL be a named location, and SHALL
NOT be derived by walking up from the catalog file's path. Deriving it
turns file placement into a hidden governance contract: a catalog at
`/tmp/catalog.jsonl` would yield the filesystem root and accept every
absolute path, while one at `<repo>/output/custom-index.jsonl` would
yield `<repo>` and accept runs outside `output/` — in both cases silently.

An explicit `catalog_path` is therefore the documented escape hatch for
indexing a run deliberately elsewhere, and SHALL NOT be second-guessed by
a boundary the caller never named. This does not weaken the fix: the
pollution arrives through the no-argument default path, which is exactly
where the boundary sits.

#### Scenario: an explicit catalog path is honoured as given

- **GIVEN** a caller passing `catalog_path` explicitly
- **WHEN** the record is appended
- **THEN** the record is appended without a tree inferred from that path

### Requirement: Containment SHALL hold across differing spellings of the same directory

The containment check SHALL compare the run directory and the tree in a
form that survives symbolic links, Windows junctions, and 8.3 short
names, by resolving **both** sides rather than normalising text alone.

Comparing lexically is not a conservative approximation here — it fails
in the dangerous direction. When `output/` is a link, or when the tree is
spelled long while the run directory is spelled short, a purely textual
check rejects **every** legitimate run. This is not hypothetical: this
repository's Windows CI runners set `TEMP` to `C:/Users/RUNNER~1/...`,
and the first implementation of this check turned all three Windows legs
red for exactly that reason.

Resolving is affordable at this seam because it runs once per run, unlike
the console's per-row read filter, which must stay purely lexical.

The maintenance tool SHALL apply the **same** predicate function as the
writer. Two copies of a containment rule are how the tool comes to
classify legitimate rows for removal.

#### Scenario: a linked spelling of the tree is still inside it

- **GIVEN** a run directory reached through a symlink or junction whose
  target lies inside the output tree
- **WHEN** the record is appended
- **THEN** the record is appended

### Requirement: The maintenance tool SHALL NOT lose rows it did not account for

The tool SHALL report by default and modify the catalog only when
explicitly asked. When it does modify, it SHALL write the removed lines
verbatim to a sidecar file **before** rewriting the catalog, and it SHALL
replace the catalog atomically.

Between classifying and rewriting, a concurrent run may append a row that
is in neither the retained set nor the sidecar; rewriting would destroy it
permanently. The tool SHALL detect that the catalog changed under it and
SHALL refuse to modify anything rather than swallow that row.

Lines the tool cannot interpret SHALL be retained, including valid JSON
that is not a record object (`null`, arrays, scalars). Such a value SHALL
NOT abort the report — the criterion applies only to rows provably
identifiable as debris; anything else is the operator's data.

#### Scenario: the catalog changed during the report

- **GIVEN** a row appended after classification and before the rewrite
- **WHEN** the tool is asked to prune
- **THEN** nothing is written and the tool reports why

#### Scenario: a JSON value that is not a record

- **GIVEN** a catalog line containing `null`
- **WHEN** the tool classifies the catalog
- **THEN** the line is retained and the report completes
