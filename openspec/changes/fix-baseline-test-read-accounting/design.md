## Context

History gaps stop baseline lookup before reading the next artifact. The local
smoke test conflates this with an invalid artifact discovered after reading.

## Goals / Non-Goals

**Goal:** test actual read accounting in both gap positions without weakening
the fail-closed verdict. **Non-goals:** changing search, UI, or artifact data.

## Decisions

Record reader callback invocations and compare them directly with `scanned`.
Keep terminal-state and per-reason assertions. Synthetic gap tests assert exact
read paths (one before an interior gap, zero before an initial gap), so coverage
does not depend on files available only on this machine.

## Risks / Trade-offs

The optional real-file test still skips on clean machines. The two deterministic
synthetic tests therefore provide the automated accounting regression coverage.
No deployment or data migration is required.
