## ADDED Requirements

### Requirement: Inspection SHALL use the active canonical lock owner

`single_flight_with_inspection` SHALL use the sole existing lock acquisition
implementation, yield an inspector only after all locks are acquired, and retain
canonical ordering, mutual exclusion, setup/contention errors and cleanup.
`single_flight` SHALL retain its existing signature and None yield. Inspection
SHALL NOT unlock, reopen, replace or delete the coordination file or expose its FD.

#### Scenario: Opt-in inspection preserves exclusion
- **WHEN** an inspector's context holds a resource and a second caller attempts the existing API for that resource
- **THEN** the second caller is refused and the inspector does not release its lock to read

#### Scenario: Existing callers preserve the original contract
- **WHEN** a caller uses single_flight without inspection
- **THEN** its context value is None and existing admission, failure and cleanup semantics are unchanged

### Requirement: A byte snapshot SHALL be bounded actual owner-descriptor evidence

`read_byte(resource)` SHALL accept only an acquired resource in the owning process
and thread during the live context. Its lock path SHALL be an ordinary, non-linked,
single-link file of exactly one byte. The inspector SHALL read that byte through
the actual owning descriptor, preserve its seek position, verify path/descriptor
identity and size/mtime before and after, and return a frozen `LockedByteSnapshot`
containing lock_path, byte, device, inode, size, mtime_ns and owner_pid.
On Windows the opt-in descriptor SHALL be opened in binary mode from the first
open to preserve every possible byte without CRT translation or truncation.
The legacy entry's open flags SHALL remain unchanged in the same acquisition body.
Opt-in inspection SHALL bind pre-open path identity/metadata. Missing, empty or
changed pre-open files SHALL NOT become evidence through acquisition-time
initialization; canonical admission and exclusion for these resources remain unchanged.

#### Scenario: Windows second-handle denial does not prevent owner inspection
- **WHEN** a real Windows byte lock denies a second-handle read of an existing one-byte coordination file
- **THEN** the inspector reads the actual byte through its owning descriptor while retaining the lock and inode

#### Scenario: Cursor and bytes are unchanged
- **WHEN** a snapshot is produced successfully
- **THEN** the descriptor's prior seek position and the file's bytes, identity, size and mtime are unchanged

#### Scenario: Translation-sensitive bytes remain actual bytes
- **WHEN** opt-in inspection holds any of the 256 single-byte values including Ctrl-Z
- **THEN** the initial open and repeated reads preserve that exact byte without translation or truncation

#### Scenario: Bootstrap bytes are not pre-existing evidence
- **WHEN** a missing or empty lock is initialized during canonical acquisition, or its pre-open identity is replaced
- **THEN** inspection refuses before descriptor read for the entire context while that resource remains locked
- **AND** pre-existing eligible resources in the same context remain inspectable

### Requirement: Invalid inspection SHALL fail closed before returning evidence

Invalid inspection SHALL raise `LockInspectionError` and return no evidence.
This includes wrong resources, expired contexts, different processes/threads,
linked or replaced paths, unexpected size, short reads and I/O failures.
There SHALL be no assumed zero byte, metadata-only substitute,
catch-and-continue, descriptor reuse, general file reader or weakening of data
validation. Inspectors SHALL be invalidated before descriptor cleanup even when
the body raises or lock acquisition only partially succeeds.

#### Scenario: Inspector cannot use a reused descriptor after exit
- **WHEN** an exited context's descriptor number is later reused and its old inspector is called
- **THEN** inspection is refused without reading the reused descriptor

#### Scenario: A changed path is not accepted as the owned file
- **WHEN** the owned lock path is replaced, linked, resized or changes during inspection
- **THEN** no snapshot is returned and inspection fails loudly

### Requirement: Inspection SHALL NOT imply recovery or production acceptance

Snapshots SHALL only describe a coordination byte observed by its real owner.
No snapshot SHALL authorize a data retry, accept a provider, change an exception
policy, declare official metrics, or activate production or scheduling. Separate
consumers MUST bind real owner identities, scope and ledger evidence before use.

#### Scenario: API availability leaves production unchanged
- **WHEN** the inspection capability is installed or exercised on temporary fixtures
- **THEN** no data stage, provider swap, production switch or task activation occurs
