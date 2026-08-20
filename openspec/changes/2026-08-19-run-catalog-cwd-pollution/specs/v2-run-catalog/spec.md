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

The base a relative row is anchored against SHALL be named by the caller
and SHALL NOT be derived from the output tree's path. Deriving it repeats
the defect this change already removed twice: a tree named through a link
alias would anchor legitimate relative rows beside the alias and mark them
as debris. No behaviour in this feature is decided by where a path sits or
how it is spelled.

#### Scenario: the tree is named through a link alias

- **GIVEN** the output tree named through a symlink or junction, and a
  relative row that resolves inside it
- **WHEN** the tool classifies the catalog
- **THEN** the row is retained

#### Scenario: a linked spelling of the tree is still inside it

- **GIVEN** a run directory reached through a symlink or junction whose
  target lies inside the output tree
- **WHEN** the record is appended
- **THEN** the record is appended

### Requirement: Every row the writer accepts is listable by the reader

The writer SHALL store a spelling the reader is guaranteed to accept, and
SHALL NOT record an alias merely because resolution proved it inside the
tree. Accepting a run and then recording a spelling the console sets
aside is the same pollution this change exists to stop — a row that can
never be opened.

The two sides SHALL NOT be expected to share one predicate here. The
reader filters **per row on every render** (3527 rows cost 771 ms when it
resolved), so it is purely lexical and recognises exactly two spellings of
the tree: the tree's own and its resolved target. The writer runs once per
run and can afford to resolve, so it accepts a **third** spelling — another
junction, an 8.3 short name, a second symlink. Where the writer accepts by
resolution alone, it SHALL store the resolved path, which lies under one of
the two spellings the reader knows.

This invariant SHALL be verified against the reader itself rather than
against a restatement of its rule. Hand-derived lists of "which spellings
count" are what produced six separate defects of this class in this change;
asking the real reader closes the class instead of enumerating it.

The invariant SHALL be that the reader resolves the stored spelling to the
**same directory the run wrote to**, not merely that the reader lists the
row. Listing is the weaker question and it misses the case that matters: a
row the console happily lists but opens one directory over shows another
run's results under this run's name.

#### Scenario: a run directory named through a third spelling

- **GIVEN** a run directory reached through a junction that resolves into
  the output tree
- **WHEN** the record is appended
- **THEN** the record is appended, and the console resolves the stored
  `output_dir` to that same directory

### Requirement: The boundary SHALL judge the exact string the producer used

The check SHALL NOT normalise the recorded `output_dir` before examining
it. Whitespace may be stripped to decide whether a value was given at all,
but the stripped form SHALL NOT be what gets parsed as a path.

The **writer** SHALL refuse a value carrying whitespace at either end,
because such a path cannot name one directory unambiguously: whether that
whitespace belongs to the name depends on who reads it. Measured on this
operator's Windows box, `mkdir("r1 ")` creates `r1`, so the record is
wrong from the start; on POSIX it really is a different directory. Either
way the row cannot identify the run, and declining to record it leaves the
run's artifacts untouched. A value that is only whitespace still counts as
absent rather than as such a name.

That rule is about what is worth **recording from now on**, and it
deliberately does NOT live in the containment predicate the maintenance
tool shares. The tool's question is whether the console can open a row;
once the reader was aligned to read paths exactly, it can — so classifying
such a historical row as debris and removing it would destroy a record
that opens fine. The shared predicate stays the containment rule; this one
is the writer's alone.

Leading whitespace is a valid filename character — on POSIX by
definition, and measured on this operator's Windows box a directory named
`" output"` is creatable — and the engines hand `config.output_dir` to
`Path` verbatim. A check that strips first therefore examines a different
directory than the one the run wrote to: artifacts at `<repo>/ output/run`
lie outside the tree, while the stripped `<repo>/output/run` lies inside
and is accepted. The original string is what gets stored, so the console
would then offer an unrelated in-tree directory under that run's name.

#### Scenario: an output directory whose name begins with a space

- **GIVEN** a run whose `output_dir` is `" output/run"`, launched from the
  repository root
- **WHEN** the record is appended to the default catalog
- **THEN** nothing is appended

#### Scenario: the tool keeps a historical row the writer would now refuse

- **GIVEN** an existing catalog row whose in-tree `output_dir` ends with a
  space
- **WHEN** the maintenance tool classifies the catalog
- **THEN** the row counts as verified in-tree and is retained

#### Scenario: an output directory whose name ends with a space

- **GIVEN** a run whose `output_dir` is `"output/run "`, inside the tree
- **WHEN** the record is appended to the default catalog
- **THEN** nothing is appended

#### Scenario: an output directory that is only whitespace

- **GIVEN** a run whose `output_dir` is `"   "`
- **WHEN** the record is appended
- **THEN** nothing is appended, as for a missing value

### Requirement: A relative output directory SHALL be read against the producer's launch directory

The writer SHALL resolve a relative `output_dir` against the process
working directory of the run that produced it, not against the repository
root. The engines create artifacts relative to their own CWD, and
`WalkForwardConfig.output_dir` defaults to the relative
`"output/walk_forward"` — measured on the operator's catalog, **101 of
the 105 legitimate rows are relative paths**, so this is the common case
rather than an edge one.

Reading them against the repository root would accept a run launched
elsewhere — an actual `/tmp/output/walk_forward/...` run would be
recorded as though it were `<repo>/output/walk_forward/...`. The catalog
would stay polluted, and the console would offer to open unrelated
repository artifacts under that run's name.

Readers — the console and the maintenance tool — cannot know the
producer's CWD after the fact and SHALL keep reading relative rows as
repository-root-relative. Where the two readings agree, which is every
run launched from the repository root, the writer SHALL store the
recorded text **unchanged**, so the catalog stays portable rather than
pinned to one machine's layout. Only where they disagree SHALL the writer
store the absolute path, so the row never names a directory that does not
exist.

#### Scenario: a run launched outside the repository

- **GIVEN** an engine launched from a directory outside the repository,
  recording the relative default `output/...`
- **WHEN** the record is appended to the default catalog
- **THEN** nothing is appended, and the reason is logged

#### Scenario: the ordinary run stores exactly what it recorded

- **GIVEN** an engine launched from the repository root recording
  `output/wf/r1`
- **WHEN** the record is appended
- **THEN** the stored `output_dir` is still `output/wf/r1`

### Requirement: The maintenance tool SHALL NOT lose rows it did not account for

The tool SHALL report by default and modify the catalog only when
explicitly asked. When it does modify, it SHALL write the removed lines
verbatim to a sidecar file **before** rewriting the catalog, and it SHALL
replace the catalog atomically.

Between classifying and rewriting, a concurrent run may append a row that
is in neither the retained set nor the sidecar; rewriting would destroy it
permanently. An atomic replace does not close this window — what is atomic
is swapping the file, not the read-modify-write around it.

The writer and the tool SHALL therefore serialize through a shared
cross-process advisory lock, and the tool SHALL classify **inside** that
lock rather than acting on a snapshot taken before it. A tool that cannot
take the lock SHALL modify nothing and say so. OS-level advisory locks are
released by the kernel when a process dies, so no stale-lock heuristic is
needed.

A writer that cannot take the lock SHALL NOT append without it. An
unlocked append can land after the tool's final verification and before
its replace, where no check can see it and the replace discards it — so a
deliberate bypass path reopens exactly the window the lock exists to
close. Such a writer SHALL instead emit the complete record to the log and
skip the append, so the row is visible rather than silently dropped, and
the catalog is explicitly a side record rather than the run's product.

The tool SHALL additionally compare the catalog against its state at the
start of the critical section before replacing it. With no bypass path
this is a belt against a future writer that does not honour the lock, not
protection for a designed one.

The tool SHALL read the catalog byte-faithfully. Decoding with a strict
UTF-8 codec makes a single invalid byte abort even report-only mode, and
splitting on every character Unicode calls a line break — `U+2028`,
`U+0085` — tears one valid record into malformed fragments, which a later
prune then writes back separated by real newlines, destroying it. The
catalog SHALL be split on newlines alone and decoded reversibly, and the
bytes of retained lines, including their line endings, SHALL survive a
rewrite unchanged. Measured on this operator's catalog: all 3560 rows end
with CRLF, so a rewrite that normalises endings would silently rewrite
every line.

The tool SHALL read the catalog byte-faithfully. Decoding it strictly as
UTF-8 aborts the whole scan on a single invalid byte — including the
report-only pass — for exactly the corrupted or foreign data the tool
exists to tolerate; a reversible decoding SHALL be used so an undecodable
row can stay unclassified instead. Records SHALL be split on newlines
only: `str.splitlines()` also breaks on `U+2028` and `U+0085`, which
`json.dumps(ensure_ascii=False)` emits verbatim, so one valid record
becomes several malformed fragments and a later prune rewrites them with
real newlines, destroying it permanently. Line endings SHALL survive the
rewrite unchanged — measured on this operator's catalog, all 3560 rows end
CRLF, and normalising on read while translating back on write silently
rewrites a mixed-ending file.

Each row's terminator SHALL travel with that row, not as one file-level
flag applied after partitioning. When the last row has no terminator and
that row is the one removed, a file-level flag is wrong at both ends: the
retained set loses the newline that ended its last row, while the sidecar
gains one on a row that never had it — and the sidecar is the copy that
claims to be verbatim. An empty catalog SHALL yield zero rows rather than
one blank one, so a freshly created or truncated file is not reported as
having a row.

Lines the tool cannot interpret SHALL be retained, including blank
separator lines and valid JSON that is not a record object (`null`,
arrays, scalars). A blank line that is in neither the retained nor the
removed set would be deleted by a prune without appearing in the sidecar,
breaking the evidence promise for a line the tool never classified.

Retained lines SHALL be reported in two groups: those whose artifacts were
**verified** inside the tree, and those retained only because the tool
could not interpret them. Counting them together makes a catalog of
nothing but `null` report as 100% in-tree — and this report is what the
operator decides `--prune` on. Such a value SHALL
NOT abort the report — the criterion applies only to rows provably
identifiable as debris; anything else is the operator's data.

Nor SHALL a row whose `output_dir` cannot name a file at all abort it. A
string containing an embedded NUL makes `Path.resolve()` raise
`ValueError` — not `OSError` — and an uncaught one ends even the
report-only pass. A string that cannot name a file is certainly not
inside the tree, so it is classified as debris rather than crashing the
scan the tool exists to complete over foreign data.

Every file the tool creates beside the catalog SHALL carry the catalog's
access mode **before any content is written into it**. Setting the mode
after filling the file leaves a window in which other local users can read
the records, and an interruption before the later `chmod` leaves the wide
copy on disk permanently. Ordering is part of the rule, so creation and
mode SHALL happen in one place rather than being repeated at each site —
repeating them is how the two sites came to disagree.

The mode requirement covers every file created — the sidecar holds the removed records, the staged file holds
the retained ones, and both are as sensitive as the catalog itself. The
lock file holds no rows, but takes the same mode so that the guard needs
no table of exceptions: a hand-kept list of "which files matter" is how
the sidecar was missed after the staged file was fixed.

Rewriting by replacement SHALL preserve what it replaces beyond the rows
themselves. `Path.write_text` creates the staged file under the process
umask, and `os.replace` carries that mode onto the live catalog, so a
catalog kept at `0600` would be widened to `0644` by a maintenance run;
the tool SHALL copy the catalog's access mode onto the staged file before
replacing. Ownership is **not** preserved and cannot be without
privileges — the tool is meant to be run by the catalog's owner.

Evidence SHALL never be overwritten by later evidence. Two prunes within
one wall-clock second derive the same sidecar name, and writing it would
silently truncate the first run's copy — the exact promise the sidecar
exists to keep. The tool SHALL create the sidecar exclusively and take a
fresh name on collision.

The identity a lock is taken on, and the target the tool replaces, SHALL
be the catalog's canonical path rather than the string a caller supplied.
Mutual exclusion only holds if both sides compute the same lock, so
deriving the lock name from the given spelling hands exclusion to spelling:
a symlink alias yields a different lock file, and replacing through that
alias overwrites the link while the real catalog keeps its old content and
the tool reports success. The sidecar and the staging file SHALL sit
beside the canonical path so the replace stays on one filesystem.

Hard links cannot be collapsed this way — several names share one inode
and none is canonical. Since the tool rewrites by replacing a name, the
other names would keep pointing at the old content, so the tool SHALL
refuse to prune a catalog that has more than one name.

#### Scenario: the catalog is given through a symlink alias

- **GIVEN** `--catalog` naming a symlink to the live catalog
- **WHEN** the tool prunes
- **THEN** the live catalog is the file rewritten, and the symlink is
  still a symlink

#### Scenario: the catalog has more than one name

- **GIVEN** a catalog with a hard link
- **WHEN** the tool is asked to prune
- **THEN** nothing is written and the tool reports why

#### Scenario: the tool cannot take the lock

- **GIVEN** another process holding the catalog lock
- **WHEN** the tool is asked to prune
- **THEN** nothing is written and the tool reports why

#### Scenario: an append that did not honour the lock

- **GIVEN** a row appended inside the tool's critical section
- **WHEN** the tool is about to replace the catalog
- **THEN** nothing is written and the tool reports why

#### Scenario: a writer that cannot take the lock does not bypass it

- **GIVEN** the lock held elsewhere for longer than the writer's timeout
- **WHEN** a run appends its record
- **THEN** nothing is appended and the complete record is logged

#### Scenario: a JSON value that is not a record

- **GIVEN** a catalog line containing `null`
- **WHEN** the tool classifies the catalog
- **THEN** the line is retained, counted as unclassified rather than
  verified, and the report completes

#### Scenario: a record containing a Unicode line separator

- **GIVEN** a row whose text carries `U+2028`
- **WHEN** the tool classifies and prunes the catalog
- **THEN** the row stays one record, counted as in-tree

#### Scenario: a byte that is not valid UTF-8

- **GIVEN** a catalog line containing an undecodable byte
- **WHEN** the tool classifies the catalog
- **THEN** the scan completes and that line is retained

#### Scenario: a record containing a Unicode line separator

- **GIVEN** a row whose text carries `U+2028`
- **WHEN** the tool classifies the catalog
- **THEN** it counts as one row, not as several fragments

#### Scenario: a byte that is not valid UTF-8

- **GIVEN** a catalog line containing an undecodable byte
- **WHEN** the tool classifies the catalog
- **THEN** the scan completes and that line is retained unclassified

#### Scenario: the last row has no terminator and is removed

- **GIVEN** a catalog whose final row lacks a newline and lies outside the
  tree
- **WHEN** the tool prunes it
- **THEN** the retained rows keep their bytes, and the sidecar holds the
  removed row without adding a terminator

#### Scenario: a zero-byte catalog

- **GIVEN** a catalog of zero bytes
- **WHEN** the tool classifies it
- **THEN** it reports no rows at all

#### Scenario: a catalog written with CRLF endings

- **GIVEN** a catalog whose rows end CRLF
- **WHEN** the tool prunes it
- **THEN** the retained rows still end CRLF

#### Scenario: a blank separator line

- **GIVEN** a catalog containing a blank line
- **WHEN** the tool prunes
- **THEN** the blank line is still in the catalog afterwards

#### Scenario: an `output_dir` that cannot name a file

- **GIVEN** a row whose `output_dir` contains an embedded NUL
- **WHEN** the tool classifies the catalog
- **THEN** the scan completes and that row counts as debris

#### Scenario: a catalog kept at a restrictive mode

- **GIVEN** a catalog readable only by its owner
- **WHEN** the tool prunes it
- **THEN** the catalog keeps that mode afterwards, and every file the run
  leaves beside it carries that mode too

#### Scenario: content written into a freshly created file

- **GIVEN** a prune that writes records into a file it just created
- **WHEN** that content is written
- **THEN** the file already carries the catalog's mode

#### Scenario: a run that cannot allocate its files

- **GIVEN** every sidecar name for this second already taken
- **WHEN** the tool prunes
- **THEN** nothing is written and no partly-created file is left behind

#### Scenario: a sidecar name already taken

- **GIVEN** an existing sidecar for this second
- **WHEN** the tool prunes
- **THEN** the existing file is untouched and the new evidence takes a
  fresh name
