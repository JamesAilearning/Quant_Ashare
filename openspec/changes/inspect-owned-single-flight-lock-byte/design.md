## Context

The canonical single-flight module opens one OS descriptor per sorted resource,
locks byte zero on Windows (or takes flock on POSIX), and currently yields None.
A recovery ledger's registry coordination file cannot be reread through another
handle while held. A contained real Windows probe reproduced PermissionError
for the second handle and success through the owning descriptor. Neither this
probe nor an inspection snapshot is market-data or production acceptance.

## Goals / Non-Goals

**Goals:** Preserve the actual lock and full byte evidence. Provide bounded,
read-only, same-owner inspection without releasing, reopening or replacing a
lock. Keep existing single-flight semantics and one acquisition implementation.

**Non-Goals:** Change data/stages/models/metrics, waive ledger bytes, add a retry
mechanism, transmit descriptors across processes, accept stale snapshots, expose
UI authorization, or alter production tasks. No general-purpose file reader.

## Decisions

- Add `single_flight_with_inspection(*resources)` yielding a
  `SingleFlightLockInspection`. Both public APIs delegate to one private
  acquisition body; existing `single_flight(*resources)` continues yielding None. Retain
  identical sorting, path normalization, parent setup, OS primitives, exceptions,
  partial-acquisition cleanup and final unlocking/closing. Do not duplicate locks.
  On Windows only the opt-in descriptor adds O_BINARY at its initial open, so
  every byte is preserved before inspection; switching mode only at read is too
  late because a text-mode O_RDWR open can strip trailing Ctrl-Z. The legacy
  entry retains its original open flags. All 256 byte values receive real-lock
  regression coverage, not just an ordinary nonzero example.
- Only opt-in inspection records pre-open path identity/metadata. Missing, empty
  or changed pre-open files remain ineligible for the whole context even when
  canonical acquisition initializes them. These resources still acquire/release
  normally, so mixed bootstrap and pre-existing locks remain mutually exclusive.
  Initial evidence state is invalidated before unlocking. Legacy acquisition
  performs no added pre-open state read; unreadable initial state fails closed
  before opening a new descriptor.
- `read_byte(resource)` is valid only in the owning process and thread during
  the live context, for an acquired resource whose lock is an ordinary,
  non-linked, single-link file of exactly one byte. No FD is returned.
- Read the actual descriptor with os.read; preserve and restore its seek
  position. Verify descriptor and path identity/size/mtime before and after.
  Reject replaced paths, aliases, unexpected size, short reads or I/O failures
  through a specific `LockInspectionError`, never through a fallback path.
- Return frozen `LockedByteSnapshot` with `lock_path`, `byte`, `device`, `inode`,
  `size`, `mtime_ns`, and `owner_pid`. The byte is actual bytes, not an assumed
  zero or cached hash. A retained snapshot is immutable evidence, not a live
  lease or authorization to use an FD after release.
- Invalidate the inspector before any unlock/close, including exceptional exits.
  Separate recovery tooling must have the source-holding parent and private-
  holding worker produce their own real snapshots and bind identities, held
  scope and copy-ledger SHA. Child handle inheritance does not confer Windows
  byte access. This PR does not implement or authorize that operation protocol.

## Risks / Trade-offs

- Descriptor reuse after release -> invalidate before cleanup and test reuse.
- Path replacement / hardlink / reparse alias -> strict optional inspection
  identity and ordinary-path checks; do not alter default lock admission.
- Concurrent seek changes -> same-owner-thread restriction and pointer restore.
- POSIX permits reads through other handles -> portable synthetic refusal twins
  plus native Windows coverage, with one-byte fixtures created before locking.
- Existing mechanical extraction loses errors/cleanup -> whole-file diff review
  and unchanged CLI/contention/partial-release/cross-process regression coverage.

## Migration Plan

Existing callers need no migration: the signature and None yield stay unchanged.
Land only the new API and tests after serial full tests and local/Codex review.
Recovery consumers migrate in a separately reviewed operation; preserve failed
claims and all original evidence. Production/task switching remains gated on real
data and serving acceptance. Reverting this additive API is safe while unused.

## Open Questions

None for this bounded API. A new recovery execution protocol remains separate.
