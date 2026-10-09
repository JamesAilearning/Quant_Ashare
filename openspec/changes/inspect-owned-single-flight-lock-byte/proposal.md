## Why

A complete recovery ledger includes the one-byte registry coordination file.
Windows rejects reads through a second handle while its byte is locked, including
reads from the same process. Recovery must read through the actual lock owner
without unlocking, omitting ledger entries, or weakening market-data validation.

## What Changes

- Add an opt-in, lifetime-bound inspector for the existing single-flight locks.
- Read exactly one byte through the descriptor that owns the lock and return an
  immutable snapshot of that byte, its ordinary-file identity, and the owner PID.
- Keep one lock-acquisition implementation and preserve `single_flight`'s
  signature, `None` yield, lock ordering, contention/setup errors and cleanup.
- Cover real Windows locking plus synthetic identity/lifetime/refusal boundaries.

## Capabilities

### New Capabilities

- `single-flight-lock-inspection`: Fail-closed inspection of a one-byte coordination
  file through its currently owning lock descriptor.

### Modified Capabilities

None. Existing daily-update exclusivity and stage behavior are unchanged.

## Impact

`src/data_pipeline/single_flight.py` and focused tests. No new dependency, provider
or raw-data mutation, metric, model, universe, trading, UI, quarantine or scheduling
change. This API does not authorize another recovery attempt or accept any data;
separate operator evidence consumers must bind and validate its real snapshots.
