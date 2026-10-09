"""Owner-descriptor inspection with real temporary OS locks; no market data."""

import os
import stat
import sys
import threading
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data_pipeline import single_flight as sf


def test_real_locked_coordination_byte_is_read_by_actual_owner(tmp_path: Path) -> None:
    resource = tmp_path / "delisted_registry.parquet"
    lock = sf.lock_path_for(resource)
    lock.write_bytes(b"\xa5")  # Prove actual reading, not an assumed zero byte.
    before = lock.stat()
    with sf.single_flight_with_inspection(resource) as inspector:
        if sys.platform == "win32":
            with pytest.raises(PermissionError):
                lock.read_bytes()
        snapshot = inspector.read_byte(resource)
        assert snapshot.byte == b"\xa5" and snapshot.size == 1
        assert (snapshot.device, snapshot.inode, snapshot.mtime_ns) == (
            before.st_dev, before.st_ino, before.st_mtime_ns,
        )
        with pytest.raises(sf.AlreadyRunningError):
            with sf.single_flight(resource):
                pytest.fail("Inspection must not release its lock to read")
    after = lock.stat()
    assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
    )
    assert lock.read_bytes() == b"\xa5"


@pytest.fixture
def owned_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep real acquisition/OS locks; observe only the actual owning descriptor."""
    resource = tmp_path / "registry.parquet"
    lock = sf.lock_path_for(resource)
    lock.write_bytes(b"\xa5")
    opened: list[int] = []
    original_open = os.open

    def capture_open(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if Path(path) == lock:
            opened.append(fd)
        return fd

    monkeypatch.setattr(sf.os, "open", capture_open)
    with sf.single_flight_with_inspection(resource) as inspector:
        assert len(opened) == 1
        yield SimpleNamespace(resource=resource, lock=lock, inspector=inspector, fd=opened[0])


def altered_stat(info, **changes):
    """Strict metadata twin for changes Windows forbids on a held lock path."""
    values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
    values.update(changes)
    return SimpleNamespace(**values)


def test_snapshot_has_exact_frozen_schema_and_actual_owner_pid(owned_lock) -> None:
    snapshot = owned_lock.inspector.read_byte(owned_lock.resource)
    assert isinstance(snapshot, sf.LockedByteSnapshot)
    assert tuple(field.name for field in fields(snapshot)) == (
        "lock_path", "byte", "device", "inode", "size", "mtime_ns", "owner_pid",
    )
    assert snapshot.lock_path == owned_lock.lock
    assert snapshot.byte == b"\xa5" and snapshot.owner_pid == os.getpid()
    assert sf.LockedByteSnapshot.__dataclass_params__.frozen is True
    with pytest.raises(FrozenInstanceError):
        snapshot.byte = b"\0"
    assert not hasattr(snapshot, "fd")


def test_owner_read_preserves_cursor_and_never_reopens_lock(owned_lock, monkeypatch) -> None:
    os.lseek(owned_lock.fd, 13, os.SEEK_SET)

    def no_reopen(*args, **kwargs):
        pytest.fail("Inspection reopened a coordination file instead of using its owner")

    with monkeypatch.context() as scoped:
        scoped.setattr(sf.os, "open", no_reopen)
        snapshot = owned_lock.inspector.read_byte(owned_lock.resource)
    assert snapshot.byte == b"\xa5"
    assert os.lseek(owned_lock.fd, 0, os.SEEK_CUR) == 13
    with pytest.raises(sf.AlreadyRunningError):
        with sf.single_flight(owned_lock.resource):
            pytest.fail("Reading changed the real held lock")


def test_unknown_resource_refuses_before_any_descriptor_read(owned_lock, monkeypatch) -> None:
    def no_read(*args):
        pytest.fail("Unknown resource reached an owner descriptor")

    monkeypatch.setattr(sf.os, "read", no_read)
    with pytest.raises(sf.LockInspectionError):
        owned_lock.inspector.read_byte(owned_lock.resource.with_name("unowned.parquet"))


def test_wrong_process_identity_refuses_before_descriptor_read(owned_lock, monkeypatch) -> None:
    actual_pid = os.getpid()
    monkeypatch.setattr(sf.os, "getpid", lambda: actual_pid + 1)
    monkeypatch.setattr(sf.os, "read", lambda *args: pytest.fail("Foreign PID read a held descriptor"))
    with pytest.raises(sf.LockInspectionError):
        owned_lock.inspector.read_byte(owned_lock.resource)


def test_actual_other_thread_cannot_read_owner_descriptor(owned_lock) -> None:
    errors: list[BaseException] = []

    def foreign_thread():
        try:
            owned_lock.inspector.read_byte(owned_lock.resource)
        except BaseException as exc:
            errors.append(exc)

    other = threading.Thread(target=foreign_thread, daemon=True)
    other.start()
    other.join(timeout=2)
    assert not other.is_alive(), "Wrong-thread refusal unexpectedly blocked"
    assert len(errors) == 1 and isinstance(errors[0], sf.LockInspectionError)
    assert owned_lock.inspector.read_byte(owned_lock.resource).byte == b"\xa5"


def test_expired_inspector_refuses_even_if_descriptor_number_would_now_read_another_file(tmp_path, monkeypatch) -> None:
    resource, replacement = tmp_path / "resource", tmp_path / "replacement"
    sf.lock_path_for(resource).write_bytes(b"\xa5")
    replacement.write_bytes(b"Z")
    with sf.single_flight_with_inspection(resource) as inspector:
        assert inspector.read_byte(resource).byte == b"\xa5"
    fd = os.open(replacement, os.O_RDONLY)
    try:
        def no_read(*args):
            pytest.fail("Expired inspector used a potentially reused descriptor")
        with monkeypatch.context() as scoped:
            scoped.setattr(sf.os, "read", no_read)
            with pytest.raises(sf.LockInspectionError):
                inspector.read_byte(resource)
        assert os.read(fd, 1) == b"Z"
    finally:
        os.close(fd)


@pytest.mark.parametrize("holder_name", ["single_flight", "single_flight_with_inspection"])
@pytest.mark.parametrize("contender_name", ["single_flight", "single_flight_with_inspection"])
def test_both_apis_share_actual_os_exclusion_and_release(tmp_path, holder_name, contender_name) -> None:
    resource = tmp_path / "resource"
    sf.lock_path_for(resource).write_bytes(b"\xa5")
    holder, contender = getattr(sf, holder_name), getattr(sf, contender_name)
    with holder(resource) as value:
        if holder_name == "single_flight":
            assert value is None
        else:
            assert value.read_byte(resource).byte == b"\xa5"
        with pytest.raises(sf.AlreadyRunningError):
            with contender(resource):
                pytest.fail("Both APIs must share the same real OS lock")
    with contender(resource) as value:
        if contender_name == "single_flight":
            assert value is None
        else:
            assert value.read_byte(resource).byte == b"\xa5"


@pytest.mark.parametrize("api_name", ["single_flight", "single_flight_with_inspection"])
def test_partial_contention_releases_earlier_actual_lock(tmp_path, api_name) -> None:
    first, held = tmp_path / "a-first", tmp_path / "z-held"
    for resource in (first, held):
        sf.lock_path_for(resource).write_bytes(b"\xa5")
    with sf.single_flight(held):
        with pytest.raises(sf.AlreadyRunningError):
            with getattr(sf, api_name)(first, held):
                pytest.fail("Partial acquisition must not yield")
        with sf.single_flight(first) as legacy:
            assert legacy is None  # First lock is already released, not deferred.
    with sf.single_flight_with_inspection(first, held) as inspector:
        assert inspector.read_byte(first).byte == inspector.read_byte(held).byte == b"\xa5"


@pytest.mark.parametrize("api_name", ["single_flight", "single_flight_with_inspection"])
def test_partial_open_failure_preserves_setup_error_and_releases_first(tmp_path, monkeypatch, api_name) -> None:
    first, failed = tmp_path / "a-first", tmp_path / "z-failed"
    first_lock, failed_lock = sf.lock_path_for(first), sf.lock_path_for(failed)
    first_lock.write_bytes(b"\xa5")
    original_open = os.open

    def fail_second(path, *args, **kwargs):
        if Path(path) == failed_lock:
            raise PermissionError("synthetic setup failure")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(sf.os, "open", fail_second)
        with pytest.raises(sf.SingleFlightSetupError):
            with getattr(sf, api_name)(first, failed):
                pytest.fail("Partial setup failure must not yield")
    with sf.single_flight(first) as legacy:
        assert legacy is None
    assert first_lock.read_bytes() == b"\xa5"


@pytest.mark.parametrize("body_raises", [False, True])
def test_inspector_is_invalidated_before_unlock_even_when_body_raises(tmp_path, monkeypatch, body_raises) -> None:
    resource = tmp_path / "resource"
    sf.lock_path_for(resource).write_bytes(b"\xa5")
    original_unlock = sf._unlock
    observed: list[int] = []
    inspector = None
    error = ValueError("original body failure")

    def check_before_unlock(fd):
        assert inspector is not None
        with pytest.raises(sf.LockInspectionError):
            inspector.read_byte(resource)
        observed.append(fd)
        original_unlock(fd)

    monkeypatch.setattr(sf, "_unlock", check_before_unlock)
    try:
        with sf.single_flight_with_inspection(resource) as inspector:
            assert inspector.read_byte(resource).byte == b"\xa5"
            if body_raises:
                raise error
    except ValueError as exc:
        assert body_raises and exc is error
    else:
        assert not body_raises
    assert len(observed) == 1
    with pytest.raises(sf.LockInspectionError):
        inspector.read_byte(resource)
    monkeypatch.setattr(sf, "_unlock", original_unlock)
    with sf.single_flight(resource) as legacy:
        assert legacy is None


def test_non_one_byte_real_file_is_not_inspection_evidence(tmp_path) -> None:
    resource = tmp_path / "resource"
    lock = sf.lock_path_for(resource)
    lock.write_bytes(b"AB")  # Created before locking; no Windows sharing bypass.
    with sf.single_flight_with_inspection(resource) as inspector:
        with pytest.raises(sf.LockInspectionError):
            inspector.read_byte(resource)
    assert lock.read_bytes() == b"AB"


def test_real_preexisting_hardlink_is_rejected_without_replacing_held_path(tmp_path) -> None:
    resource = tmp_path / "resource"
    lock, alias = sf.lock_path_for(resource), tmp_path / "alias"
    lock.write_bytes(b"\xa5")
    os.link(lock, alias)  # Ordinary local FS setup before acquiring the lock.
    with sf.single_flight_with_inspection(resource) as inspector:
        with pytest.raises(sf.LockInspectionError):
            inspector.read_byte(resource)
    assert lock.stat().st_ino == alias.stat().st_ino and lock.read_bytes() == alias.read_bytes() == b"\xa5"


@pytest.mark.parametrize("mutation", ["replaced_inode", "zero_size", "hardlink", "symlink", "reparse", "ancestor_reparse"])
def test_held_path_identity_and_alias_failures_refuse_strict_metadata_twins(owned_lock, monkeypatch, mutation) -> None:
    original_lstat = Path.lstat

    def changed_path(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == owned_lock.lock:
            if mutation == "replaced_inode":
                return altered_stat(info, st_ino=info.st_ino + 1)
            if mutation == "zero_size":
                return altered_stat(info, st_size=0)
            if mutation == "hardlink":
                return altered_stat(info, st_nlink=2)
            if mutation == "symlink":
                return altered_stat(info, st_mode=stat.S_IFLNK | 0o600)
            if mutation == "reparse":
                return altered_stat(info, st_file_attributes=0x400)
        if mutation == "ancestor_reparse" and path == owned_lock.lock.parent:
            return altered_stat(info, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", changed_path)
    monkeypatch.setattr(sf.os, "read", lambda *args: pytest.fail("Invalid path reached a descriptor read"))
    with pytest.raises(sf.LockInspectionError):
        owned_lock.inspector.read_byte(owned_lock.resource)


@pytest.mark.parametrize("which", ["descriptor", "path"])
def test_identity_change_after_byte_read_returns_no_snapshot_and_restores_cursor(owned_lock, monkeypatch, which) -> None:
    os.lseek(owned_lock.fd, 13, os.SEEK_SET)
    original_fstat, original_lstat = os.fstat, Path.lstat
    calls = 0

    def changed_fstat(fd):
        nonlocal calls
        info = original_fstat(fd)
        if fd == owned_lock.fd:
            calls += 1
            if calls > 1:
                return altered_stat(info, st_mtime_ns=info.st_mtime_ns + 1)
        return info

    def changed_lstat(path, *args, **kwargs):
        nonlocal calls
        info = original_lstat(path, *args, **kwargs)
        if path == owned_lock.lock:
            calls += 1
            if calls > 1:
                return altered_stat(info, st_mtime_ns=info.st_mtime_ns + 1)
        return info

    with monkeypatch.context() as scoped:
        if which == "descriptor":
            scoped.setattr(sf.os, "fstat", changed_fstat)
        else:
            scoped.setattr(Path, "lstat", changed_lstat)
        with pytest.raises(sf.LockInspectionError):
            owned_lock.inspector.read_byte(owned_lock.resource)
    assert calls >= 2 and os.lseek(owned_lock.fd, 0, os.SEEK_CUR) == 13


@pytest.mark.parametrize("returned", [b"", b"AB"])
def test_short_or_nonbounded_read_refuses_without_zero_fallback(owned_lock, monkeypatch, returned) -> None:
    os.lseek(owned_lock.fd, 13, os.SEEK_SET)
    calls = []

    def bad_read(fd, count):
        calls.append((fd, count))
        return returned

    monkeypatch.setattr(sf.os, "read", bad_read)
    with pytest.raises(sf.LockInspectionError):
        owned_lock.inspector.read_byte(owned_lock.resource)
    assert calls == [(owned_lock.fd, 1)] and os.lseek(owned_lock.fd, 0, os.SEEK_CUR) == 13


@pytest.mark.parametrize("operation", ["fstat", "lstat", "tell", "rewind", "read", "restore"])
def test_inspection_io_and_pointer_restore_errors_are_typed_not_evidence(owned_lock, monkeypatch, operation) -> None:
    os.lseek(owned_lock.fd, 13, os.SEEK_SET)
    original_fstat, original_lstat, original_seek = os.fstat, Path.lstat, os.lseek

    def failing_fstat(fd):
        if fd == owned_lock.fd:
            raise OSError("synthetic fstat failure")
        return original_fstat(fd)

    def failing_lstat(path, *args, **kwargs):
        if path == owned_lock.lock:
            raise OSError("synthetic path failure")
        return original_lstat(path, *args, **kwargs)

    def failing_seek(fd, offset, whence):
        fails = ((operation == "tell" and whence == os.SEEK_CUR)
                 or (operation == "rewind" and whence == os.SEEK_SET and offset == 0)
                 or (operation == "restore" and whence == os.SEEK_SET and offset == 13))
        if fd == owned_lock.fd and fails:
            raise OSError("synthetic cursor failure")
        return original_seek(fd, offset, whence)

    def failing_read(fd, count):
        assert fd == owned_lock.fd and count == 1
        raise OSError("synthetic owner read failure")

    with monkeypatch.context() as scoped:
        if operation == "fstat":
            scoped.setattr(sf.os, "fstat", failing_fstat)
        elif operation == "lstat":
            scoped.setattr(Path, "lstat", failing_lstat)
        elif operation == "read":
            scoped.setattr(sf.os, "read", failing_read)
        else:
            scoped.setattr(sf.os, "lseek", failing_seek)
        with pytest.raises(sf.LockInspectionError):
            owned_lock.inspector.read_byte(owned_lock.resource)
    if operation != "restore":
        assert original_seek(owned_lock.fd, 0, os.SEEK_CUR) == 13
    assert owned_lock.inspector.read_byte(owned_lock.resource).byte == b"\xa5"
