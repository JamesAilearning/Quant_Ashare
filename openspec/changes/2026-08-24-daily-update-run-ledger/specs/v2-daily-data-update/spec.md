# Delta for v2-daily-data-update

## ADDED Requirements

### Requirement: 每次运行 SHALL 向只可追加的运行台账追加一行

`run_daily_update` SHALL append one JSON line per run to
`<provider_dir>.daily_update_ledger.jsonl` at the run's terminal state, and
SHALL NEVER rewrite or truncate that file. The line SHALL carry the schema
version, the normalized `provider_dir` that identifies whose run it was, the
run date, the start and finish timestamps, the exit code, the failing stage key
(`null` on success) and the `detail`. The path SHALL be derived from the
provider directory's name with NO command-line override.

The run-status artifact is a SINGLE file rewritten by every run, so the operator can
only ever see the LAST run. Three consecutive nightly failures (2026-08-17 /
08-20 / 08-21) went unnoticed until the third because nothing recorded the
PATTERN; the queue's severity escalation infers pressure from the bundle's date,
which only advances on success and is therefore indirect evidence at best.

The ledger does not replace the status artifact: that one answers "how did THIS
run go", the ledger answers "what shape have the recent runs had".

Elapsed time SHALL NOT be stored: it is the difference of two timestamps the
line already carries, and a third stored copy is one more place to diverge from
the derived value.

#### Scenario: a terminal run appends exactly one line
- **WHEN** the orchestrator reaches any terminal state, success or failure,
  including the non-trading-day no-op
- **THEN** exactly one line is appended and no earlier line is altered

#### Scenario: history survives across runs
- **WHEN** several runs complete in sequence
- **THEN** every one of them is still readable in order, unlike the status
  artifact which retains only the last

#### Scenario: a dry run records nothing
- **WHEN** the run is a `--dry-run`
- **THEN** no ledger line is appended, because a dry run mutates nothing

#### Scenario: a run that never enters the orchestrator records nothing
- **WHEN** the CLI exits on a configuration error or on the single-flight
  conflict
- **THEN** no ledger line is appended, so a refused second run cannot pollute
  the history of the run holding the lock

#### Scenario: the append never follows a symlink, atomically where possible
- **WHEN** the derived ledger path is (or becomes, between check and open) a
  symlink to somewhere else — another provider's history included
- **THEN** the append refuses: the open itself carries `O_NOFOLLOW` where the
  platform provides it (failing atomically on a link), with a pre-check as the
  primary line on Windows where symlink creation is privileged; the run's exit
  code is unaffected either way

#### Scenario: a torn tail cannot swallow the new line
- **WHEN** a previous process died mid-write leaving a final line without its
  newline
- **THEN** the new line still lands as its own readable line, the fragment
  stays isolated as its own malformed line, and the new entry is not fused onto
  it

#### Scenario: the ledger path cannot alias anything else the run touches
- **WHEN** `--delisted-registry`, `--reference-cases`, or an explicit
  `--status-path` resolves to the derived ledger path, or the derived path
  falls inside the provider / tushare trees or the swap staging siblings
- **THEN** the configuration is rejected at construction, before any stage
  executes — an append into a canonical input, or a status replace truncating
  the append-only ledger, must be impossible rather than merely unlikely

#### Scenario: the ledger name shape is reserved across providers
- **WHEN** any configurable path ends with the derived-ledger name shape
  `*.daily_update_ledger.jsonl` — including a SIBLING provider's ledger, which
  this configuration cannot know about
- **THEN** it is rejected at construction: the shape is reserved for ledger
  writers, so no status replace can ever destroy any provider's append-only
  history — and the mutable ROOTS (`--provider-dir`, `--tushare-dir`) are in
  the same reservation, because a provider root sitting on a ledger name is
  renamed away wholesale by the swap machinery; the reservation examines EVERY
  path component, not the leaf alone — `<ledger-name>/status.json` would mkdir
  the ledger's name as a directory and leave the run unable to enter history

#### Scenario: a ledger failure never changes the exit code
- **WHEN** the ledger append fails for any reason
- **THEN** the failure is logged as an ERROR and the run's exit code is
  unchanged — the ledger is observability, never a canonical input, and no
  module inside `src/` outside `src/data_pipeline/daily_update.py` consumes it

#### Scenario: the failure streak is honest about what it can assert
- **WHEN** an unreadable row sits between valid runs, at the newest position,
  or the recent window truncates a longer run of failures
- **THEN** the streak is reported respectively as a lower bound ("at least N"),
  as unavailable (the newest row may have been a success), or as a lower bound
  — never as an exact count the ledger cannot actually support

#### Scenario: corruption is disclosed as corruption, not as a foreign run
- **WHEN** a reader meets a line that is JSON but not a valid v1 record — `{}`,
  a wrongly-typed provider field, an empty identity or time field
- **THEN** it counts as malformed, never as another provider's run — the
  "foreign" label is reserved for a fully valid record whose only difference
  is its identity

#### Scenario: a non-normalized provider identity is corruption, not a foreign run
- **WHEN** a row's `provider_dir` is not the normalized absolute form the
  writer's `_norm` exclusively produces — a relative path, an un-normalized
  spelling
- **THEN** the row counts as malformed instead of being described as another
  provider's run, which would disguise ledger corruption as foreign history

#### Scenario: unparseable dates and timestamps are corruption, not history
- **WHEN** a row's `run_date` is not an ISO date, a timestamp is not a
  timezone-aware ISO datetime, or `finished_at` precedes `started_at` — none
  of which the writer ever emits
- **THEN** the row counts as malformed rather than being rendered as a real
  run with nonsense dates

#### Scenario: a bad byte inside a JSON string is corruption, not data
- **WHEN** a ledger line contains invalid UTF-8 inside a string field, where
  replacement decoding would yield syntactically valid JSON
- **THEN** the line counts as malformed — decoding is strict per line, so
  silently rewritten data is never presented as a real run

### Requirement: 每次运行 SHALL 在日志里落一个带日期的运行边界

`run_daily_update` SHALL write one boundary line into the shared log at the
start of every non-dry run, carrying a full date-and-time stamp and the
normalized provider directory. A reader SHALL attribute the log lines that
follow a boundary to that run ONLY when the window it read covers the WHOLE log
AND every boundary in it names this provider, and SHALL report attribution as
UNKNOWN otherwise — including when no boundary is visible at all, and including
when the window is truncated — rather than guessing. Absence of a foreign
boundary from a truncated window is not evidence of absence: an earlier-started
sibling whose boundary fell outside the window may still be writing.

The shared log's own lines carry only `HH:MM:SS` with no date, so "21:00
yesterday" and "21:00 today" are indistinguishable in the data. Four heuristics
were tried and rejected on that ground — discarding by log mtime, treating a
wall-clock regression as a boundary, requiring the progress stamp to be at or
after the start stamp, and inferring a day rollover from mtime — and the
conclusion recorded in `update_progress` was structural: precise attribution
needs the WRITER to emit a dated boundary first.

No closing marker is written. A segment ends where the next one begins or at
end of file, and whether a run finished — and how — is already answered by the
status artifact and the ledger; a third statement of the same fact is exactly
the duplication this repository keeps paying for.

#### Scenario: the boundary carries a date and the provider
- **WHEN** a non-dry run starts
- **THEN** the log gains one line carrying a full timestamp and the normalized
  provider directory, before any stage runs

#### Scenario: lines after a boundary belong to that run when nobody else wrote
- **WHEN** the examined window covers the whole log and every boundary in it
  names this provider
- **THEN** the lines after the last one are attributed to that run with
  certainty, because a provider never runs concurrently with itself

#### Scenario: a truncated window never claims exclusivity
- **WHEN** the window a reader examined does not cover the whole log — the
  normal case for a tail read of a growing log
- **THEN** attribution is reported as unknown even if every visible boundary
  names this provider, because an earlier sibling's boundary may lie outside
  the window while its run is still writing

#### Scenario: a concurrent sibling makes attribution unknown in both orders
- **WHEN** another provider's boundary appears in the window — whether it is the
  LAST boundary, or an EARLIER one whose run may still be writing progress
- **THEN** attribution is reported as unknown, because progress lines carry no
  provider of their own and the log cannot say whether that run has ended

#### Scenario: no boundary in the window means unknown, not a guess
- **WHEN** the window a reader examined contains no boundary
- **THEN** attribution is reported as unknown, matching the behaviour that
  existed before this change rather than substituting a heuristic

#### Scenario: the reason attribution is unknown is the true one
- **WHEN** attribution is withheld — because the window is truncated, because a
  foreign boundary is present, or because no boundary is visible
- **THEN** the operator is told which of those it was, not a blanket "no
  boundary in the window" that is false in the first two cases

#### Scenario: certainty additionally requires the boundary to match the shown status
- **WHEN** progress is attributed to a boundary whose timestamp differs from
  the `started_at` of the status record the page is displaying — possible
  because status writes are best-effort and the log and the artifact advance
  independently
- **THEN** the page says the two disagree and that the progress belongs to the
  boundary's run, instead of presenting it under the displayed record's
  identity — the writer stamps status and boundary with the same
  `started_at.isoformat()`, so exact equality identifies the same run

#### Scenario: another provider's boundary is not adopted
- **WHEN** the boundary names a different provider directory
- **THEN** it is not treated as this provider's run boundary

#### Scenario: a dry run writes no boundary
- **WHEN** the run is a `--dry-run`
- **THEN** no boundary is written
