# Delta for v2-run-center-page

## ADDED Requirements

### Requirement: A session-launched manual update SHALL be cancellable through the live handle only

The run center SHALL offer a controlled cancel for a manual update it
launched in THIS UI session, and the cancel channel SHALL be the live
`Popen` handle carried on the launch result — never a pid (pids are
recycled by the OS; killing by number can hit an unrelated process). The
scheduler's automatic runs are not children of the UI and SHALL remain out
of reach. A UI restart loses the handle and the old run SHALL simply be
non-cancellable — an honest degradation, not an error.

The control SHALL be visible only while the held process is still alive
(`poll()` is None; an exited handle retires silently — the run's own status
and ledger speak for it), and SHALL require a two-step confirmation before
acting.

Cancellation SHALL never write the orchestrator's status artifact: on
Windows the process is terminated forcibly (measured in this repository:
`CTRL_BREAK_EVENT` cannot reach a `CREATE_NO_WINDOW` child from the UI
console, and Python maps CTRL_BREAK to SIGBREAK — immediate death, no
KeyboardInterrupt), so the artifact stays `running`; the page SHALL present
that state honestly from its own handle evidence (the process is confirmed
exited) instead of fabricating a terminal record. On POSIX the page's
runner SHALL send SIGINT to the process group first — the orchestrator's
own BaseException path then writes the terminal record — falling back to
SIGKILL after a grace window.

Every cancel attempt on a live process SHALL leave dated `[run_center]`
markers (request and outcome) in the shared log, following the launch
marker convention; a no-op cancel (the process had already finished) SHALL
leave no marker and change nothing.

The live bundle is untouched by cancellation at any point OUTSIDE the
swap's two-rename window: the pipeline builds into a sidecar and only an
already-validated build is atomically swapped, so the serving data remains
the last successful update regardless of when the process dies. The swap
itself is crash-atomic by contract — a kill landing between its two renames
leaves the canonical directory momentarily absent, restored by the next
run's startup repair. The cancel SHALL therefore check the canonical
directory after the process exits and, when it is missing, report the swap
hit LOUDLY with the instruction to start another update immediately — it
SHALL NOT claim the online data was unaffected in that state. Leftover
partials are cleaned by the next run's startup repair; the single-flight
lock is OS-held and releases with the process.

After a forcible cancel the page SHALL keep presenting the orphaned
`running` record as cancelled ACROSS reruns — the evidence is bound to that
record's exact `started_at` stamp and retires the moment the status is
superseded — and the launch gate SHALL unlock; correcting only the first
post-cancel render would re-lock updates until the staleness threshold. A
failed cancel SHALL retain the live handle: it is the only permitted
cancellation credential, and discarding it would leave no retry path.

#### Scenario: an accidental click after the run already finished

- **GIVEN** the operator cancels a session-launched run that has already
  exited
- **WHEN** the cancel executes
- **THEN** nothing is killed, no marker is written, and the page says the
  run had already finished — its own status and ledger stand

#### Scenario: cancelling a live run on Windows

- **GIVEN** a live session-launched manual update on Windows
- **WHEN** the operator confirms the cancel
- **THEN** the process is terminated, the log carries the request and
  outcome markers, and the page states that the status artifact remains
  `running` because a force-killed process cannot write its terminal
  record — presented as handle-evidenced truth, not as a live run

#### Scenario: cancelling a live run on POSIX

- **GIVEN** a live session-launched manual update on POSIX
- **WHEN** the operator confirms the cancel
- **THEN** SIGINT reaches the process group first and, when the orchestrator
  exits within the grace window, the cancel reports a graceful outcome whose
  terminal record was written by the orchestrator itself

#### Scenario: a cancel landing inside the swap window

- **GIVEN** a cancel whose kill lands between the swap's two renames
- **WHEN** the cancel completes and the canonical directory is missing
- **THEN** the outcome reports the swap hit loudly, instructs an immediate
  re-run (startup repair restores the directory), and does not claim the
  online data was unaffected

#### Scenario: the cancelled record stays cancelled across reruns

- **GIVEN** a forcible cancel left the status artifact at `running`
- **WHEN** the page reruns any number of times while that record stands
- **THEN** it keeps presenting the record as cancelled on handle evidence,
  the launch gate stays unlocked, and the evidence retires when a new run
  writes a different stamp

#### Scenario: a failed cancel keeps the credential

- **GIVEN** a cancel attempt that reports failure with the process possibly
  alive
- **WHEN** the page renders the failure
- **THEN** the live handle is retained so the operator can retry

#### Scenario: the scheduler's run is out of reach

- **GIVEN** only the scheduler's automatic update is running
- **WHEN** the operator looks at the run center
- **THEN** no cancel control is offered — the UI holds no handle for a
  process it did not launch
