# Delta for v2-daily-data-update

## ADDED Requirements

### Requirement: A failing stage SHALL carry its own error line into the status artifact

The orchestrator SHALL capture the ERROR records a stage emits while that stage
runs, and SHALL compose them into the status artifact's `detail` alongside the
existing exit-code summary. Every CLI-shaped stage SHALL be covered, not only
the fetch. Capture SHALL NOT alter the run's exit code, suppress the stage's
own log output, or leave its handler attached after the stage returns.

`Runner = Callable[[list[str]], int]` lets only an exit code cross back, so a
stage's actionable sentence stays in the log. Measured: three consecutive
failures (2026-08-17 / 08-20 / 08-21) all recorded `fetch failed hard (exit 1)`
while 01 had already logged the cause AND the remedy on every one of them.

`startup_repair` and `swap` are deliberately excluded: their reason already
arrives as the caught exception and is already in `detail`.

#### Scenario: the stage's reason reaches the artifact
- **WHEN** a stage logs an ERROR and returns a failing exit code
- **THEN** the status artifact's `detail` contains that error line as well as
  the exit-code summary

#### Scenario: the orchestrator's narration is not mistaken for the reason
- **WHEN** the orchestrator logs its own stage-failed line after the stage call
  returns
- **THEN** that line does not appear in `detail`, because capture is scoped to
  the stage call rather than filtered by logger name

#### Scenario: an error raised inside a helper module still counts
- **WHEN** the stage fails because a module it called logged the ERROR
- **THEN** that line is carried, since the stage's reason is not required to
  originate from the stage's own logger

#### Scenario: capture never changes the outcome
- **WHEN** a stage makes a malformed logging call whose message cannot be
  rendered
- **THEN** the run still returns the stage's exit code, `detail` falls back to
  the exit-code summary, and no exception escapes

#### Scenario: nothing captured means nothing invented, and says so
- **WHEN** a stage fails without logging any ERROR
- **THEN** `detail` carries the exit-code summary unchanged plus a marker saying
  the stage recorded no reason, so a reader can tell a fallback from a captured
  cause

Without the marker a reader cannot distinguish the two and renders the summary
as if it were the reason — dressing up "we only have an exit code" as an
explanation, which is worse than saying nothing.

#### Scenario: a failed validation check is logged at ERROR
- **WHEN** the PIT validator records a check that did not pass
- **THEN** it logs that check and its error text at ERROR level, so the capture
  window carries it; warnings stay at INFO because warnings-only is a pass

### Requirement: The composed detail SHALL stay one line and SHALL declare truncation

`detail` SHALL remain a single line, folding embedded newlines rather than
dropping the text around them, and SHALL be bounded. When lines are dropped to
respect the bound, `detail` SHALL keep BOTH the first and the last captured
line and SHALL state how many middle lines were dropped and where the full text
lives. The bound SHALL be large enough to carry a stage's remedy sentence, not
merely its complaint.

The message this exists for is about 350 characters and its SECOND half is the
remedy, so the 200-character cap used for the Jobs page table cell would keep
the complaint and cut the fix.

#### Scenario: a multi-line error is folded, not truncated to its first line
- **WHEN** a captured error contains newlines
- **THEN** `detail` contains every non-empty part on one line

#### Scenario: dropped lines are counted out loud, and the ends survive
- **WHEN** the captured errors exceed the bound
- **THEN** `detail` keeps the first and last lines, names how many middle lines
  were not listed, and points at the log

A stage's first ERROR is usually WHY and its last is usually WHAT TO DO — 01's
hole report is exactly that shape, ending with "Re-run with the same
--output-dir to fill the holes". Filling from the front until the budget runs
out drops precisely the remedy, which is the same "keep the complaint, cut the
fix" failure this requirement rejects the 200-character cap for.

#### Scenario: a single over-long error is kept rather than dropped
- **WHEN** the first captured line alone exceeds the bound
- **THEN** it is kept and marked truncated, so the artifact never falls back to
  carrying only an exit code

### Requirement: One exit code SHALL name a stage, never a single presumed cause

Each exit code's published meaning SHALL identify the stage that failed and
SHALL NOT assert one specific cause when the code covers a class of failures.
The three copies of the table (the UI constant, the runbook, and the
orchestrator's module docstring) SHALL list the same set of codes as the
`EXIT_*` constants, enforced by a test.

Exit 11 fires whenever 01 exits anything other than 0 or 3, but all three
copies read as a token/network problem. Across 2026-08-17 / 08-20 / 08-21 every
exit 11 was a fetch-manifest scope refusal; token and network were fine
throughout, and the wording sent the operator to the wrong place.

#### Scenario: the fetch-failure code points at the detail
- **WHEN** an operator reads the meaning of exit 11
- **THEN** it names the stage and directs them to the run's `detail`, rather
  than asserting token or network as the cause

#### Scenario: adding an exit code without publishing it fails the build
- **WHEN** a new `EXIT_*` constant exists that any of the three tables omits
- **THEN** the consistency test fails
