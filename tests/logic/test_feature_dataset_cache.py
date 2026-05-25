"""Tests for ``src.data._feature_dataset_cache`` + the builder
integration.

The cache module is pure stdlib (pickle + hashlib + pathlib) so most
tests are fast unit tests. The builder integration test uses
``unittest.mock.patch`` to swap out qlib's DatasetH so we don't need
a live qlib bundle.
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data._feature_dataset_cache import (  # noqa: E402
    _LEGACY_BUNDLE_TAG,
    cache_get,
    cache_path_for,
    cache_put,
    compute_cache_key,
    read_bundle_tag,
)
from src.data.feature_dataset_builder import (  # noqa: E402
    FeatureDatasetBuilder,
    FeatureDatasetConfig,
    FeatureDatasetResult,
)


def _make_config(**overrides) -> FeatureDatasetConfig:
    base = dict(
        instruments="csi300",
        feature_handler="Alpha158",
        train_start="2022-01-01", train_end="2023-12-31",
        valid_start="2024-01-01", valid_end="2024-03-31",
        test_start="2024-04-01", test_end="2024-06-30",
    )
    base.update(overrides)
    return FeatureDatasetConfig(**base)


class _PicklableDatasetStub:
    """Minimal picklable stand-in for a qlib DatasetH. The cache
    pickles the full FeatureDatasetResult tree; using a real picklable
    placeholder (instead of MagicMock or a local class) lets the
    roundtrip work even on Windows."""

    def __init__(self, label: str = "stub"):
        self.label = label


class _StubHandler:
    """Module-level so pickle can find it during cache write tests."""


class _StubDataset:
    """Module-level so pickle can find it during cache write tests.

    Class-level counters so the fixture can introspect call counts
    without needing a subclass (subclasses defined inside the fixture
    are local and unpicklable, which breaks cache_put roundtrips).
    """

    init_count = 0
    prepare_count = 0

    @classmethod
    def reset_counters(cls):
        cls.init_count = 0
        cls.prepare_count = 0

    def __init__(self, *_args, **_kwargs):
        type(self).init_count += 1

    def prepare(self, _segment, col_set="feature"):  # noqa: ARG002
        type(self).prepare_count += 1
        return pd.DataFrame(
            {"f1": [1.0, 2.0], "f2": [3.0, 4.0]},
            index=pd.MultiIndex.from_product(
                [pd.date_range("2024-04-01", periods=2), ["A"]],
                names=["datetime", "instrument"],
            ),
        )


def _make_result(dataset_obj=None) -> FeatureDatasetResult:
    return FeatureDatasetResult(
        dataset=dataset_obj if dataset_obj is not None else _PicklableDatasetStub(),
        train_shape=(100, 158),
        valid_shape=(20, 158),
        test_shape=(20, 158),
        feature_columns=tuple(f"feat_{i}" for i in range(158)),
    )


# ---------------------------------------------------------------------------
# compute_cache_key
# ---------------------------------------------------------------------------


def test_cache_key_identical_configs_match():
    a = _make_config()
    b = _make_config()
    assert compute_cache_key(a) == compute_cache_key(b)


def test_cache_key_different_instruments_differ():
    a = _make_config(instruments="csi300")
    b = _make_config(instruments="csi500")
    assert compute_cache_key(a) != compute_cache_key(b)


def test_cache_key_different_dates_differ():
    a = _make_config(train_start="2022-01-01")
    b = _make_config(train_start="2022-02-01")
    assert compute_cache_key(a) != compute_cache_key(b)


def test_cache_key_different_handlers_differ():
    a = _make_config(feature_handler="Alpha158")
    b = _make_config(feature_handler="MinedFactor")
    assert compute_cache_key(a) != compute_cache_key(b)


def test_cache_key_different_bundle_tags_differ():
    cfg = _make_config()
    a = compute_cache_key(cfg, bundle_tag="2026-03-06")
    b = compute_cache_key(cfg, bundle_tag="2026-04-01")
    assert a != b


def test_cache_key_is_filesystem_safe():
    """The hex digest must not contain path separators or other
    characters that would corrupt the cache path."""
    key = compute_cache_key(_make_config())
    assert key.replace("_", "").isalnum()
    assert "/" not in key
    assert "\\" not in key
    # Truncated to 32 chars (sha256 prefix)
    assert len(key) == 32


# ---------------------------------------------------------------------------
# read_bundle_tag
# ---------------------------------------------------------------------------


def test_read_bundle_tag_no_provider_uri():
    assert read_bundle_tag(None) == _LEGACY_BUNDLE_TAG
    assert read_bundle_tag("") == _LEGACY_BUNDLE_TAG


def test_read_bundle_tag_missing_manifest(tmp_path):
    # tmp_path exists but has no bundle_manifest.json.
    assert read_bundle_tag(tmp_path) == _LEGACY_BUNDLE_TAG


def test_read_bundle_tag_well_formed_manifest(tmp_path):
    (tmp_path / "bundle_manifest.json").write_text(
        json.dumps({"tail_date": "2026-03-06", "instrument_count": 500}),
        encoding="utf-8",
    )
    assert read_bundle_tag(tmp_path) == "2026-03-06"


def test_read_bundle_tag_malformed_manifest(tmp_path):
    (tmp_path / "bundle_manifest.json").write_text("{not valid json")
    # Best-effort: fall back to the legacy tag without raising.
    assert read_bundle_tag(tmp_path) == _LEGACY_BUNDLE_TAG


def test_read_bundle_tag_missing_tail_date_field(tmp_path):
    (tmp_path / "bundle_manifest.json").write_text(
        json.dumps({"instrument_count": 500}),  # no tail_date
    )
    assert read_bundle_tag(tmp_path) == _LEGACY_BUNDLE_TAG


# ---------------------------------------------------------------------------
# cache_get / cache_put roundtrip
# ---------------------------------------------------------------------------


def test_cache_get_returns_none_for_missing_cache_dir():
    assert cache_get(None, "anykey") is None


def test_cache_get_returns_none_for_missing_file(tmp_path):
    assert cache_get(tmp_path, "nonexistent") is None


def test_cache_put_then_get_roundtrip(tmp_path):
    result = _make_result()
    written = cache_put(tmp_path, "abc123", result)
    assert written is not None
    assert written.exists()
    loaded = cache_get(tmp_path, "abc123")
    assert loaded is not None
    assert loaded.train_shape == result.train_shape
    assert loaded.feature_columns == result.feature_columns


def test_cache_put_is_atomic_no_tmp_left(tmp_path):
    result = _make_result()
    cache_put(tmp_path, "abc", result)
    tmps = list(tmp_path.glob("*.tmp"))
    assert tmps == []


def test_cache_get_corrupt_blob_returns_none(tmp_path):
    """A corrupt cache file must be treated as a miss with a WARNING,
    not raised."""
    target = cache_path_for(tmp_path, "corrupt")
    target.write_bytes(b"this is not a valid pickle")
    assert cache_get(tmp_path, "corrupt") is None


def test_cache_get_wrong_object_type_returns_none(tmp_path):
    """A cache file that unpickles into the wrong type must be a miss."""
    target = cache_path_for(tmp_path, "wrong_type")
    with target.open("wb") as f:
        pickle.dump({"not": "a result"}, f)
    assert cache_get(tmp_path, "wrong_type") is None


def test_cache_put_none_dir_is_noop(tmp_path):
    """``cache_put(None, ...)`` must NOT raise and must NOT write."""
    assert cache_put(None, "any", _make_result()) is None


def test_cache_put_unwritable_returns_none_without_raising(monkeypatch, tmp_path):
    """A pickling failure mid-write must not propagate."""
    # Construct a result with an unpicklable attribute to force a
    # pickle error.

    class _Unpicklable:
        def __reduce__(self):
            raise pickle.PicklingError("intentionally unpicklable")

    bad_result = FeatureDatasetResult(
        dataset=_Unpicklable(),
        train_shape=(0, 0), valid_shape=(0, 0), test_shape=(0, 0),
        feature_columns=(),
    )
    # Must NOT raise.
    written = cache_put(tmp_path, "bad", bad_result)
    assert written is None
    # And no .tmp left behind.
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# FeatureDatasetBuilder.build integration with cache
# ---------------------------------------------------------------------------


class _StubFactory:
    """Module-level callable factory so pickle can find it (vs. a
    local def stored in registry). Tracks call count on the class."""

    call_count = 0

    @classmethod
    def reset(cls):
        cls.call_count = 0

    def __call__(self, _config):
        type(self).call_count += 1
        return _StubHandler()


@pytest.fixture
def stub_qlib(monkeypatch):
    """Patch qlib-touching code paths so build() doesn't need a real
    bundle. Returns a counter object exposing ``handler_factory``,
    ``DatasetH``, and ``prepare`` counts.

    Classes/factories are module-level so pickle can find them during
    cache_put roundtrips (local classes can't be pickled).
    """
    _StubFactory.reset()
    _StubDataset.reset_counters()

    # qlib's DatasetH is imported lazily inside build(); pre-import
    # the module so the patch target exists.
    import qlib.data.dataset as _ds_mod  # noqa: F401

    monkeypatch.setattr(
        "src.data.feature_dataset_builder._FEATURE_HANDLER_REGISTRY",
        {"Alpha158": _StubFactory()},
    )
    monkeypatch.setattr("qlib.data.dataset.DatasetH", _StubDataset)
    monkeypatch.setattr(
        "src.data.feature_dataset_builder.is_canonical_qlib_initialized",
        lambda: True,
    )

    class _CounterView:
        @property
        def handler_factory(self):
            return _StubFactory.call_count

        @property
        def DatasetH(self):
            return _StubDataset.init_count

        @property
        def prepare(self):
            return _StubDataset.prepare_count

    return _CounterView()


def test_build_without_cache_dir_does_not_touch_cache(stub_qlib, tmp_path):
    config = _make_config()
    result = FeatureDatasetBuilder.build(config)
    assert result.train_shape == (2, 2)
    # Cache dir should be empty (no cache_dir arg supplied)
    assert list(tmp_path.iterdir()) == []
    # Build actually ran
    assert stub_qlib.handler_factory == 1


def test_build_first_call_writes_cache(stub_qlib, tmp_path):
    config = _make_config()
    cache_dir = tmp_path / "cache"
    result = FeatureDatasetBuilder.build(config, cache_dir=cache_dir)
    assert result is not None
    # Cache file written
    files = list(cache_dir.glob("dataset_*.pkl"))
    assert len(files) == 1
    # Builder ran (cache was empty)
    assert stub_qlib.handler_factory == 1


def test_build_second_call_reads_cache(stub_qlib, tmp_path):
    config = _make_config()
    cache_dir = tmp_path / "cache"
    # First call: build + write
    FeatureDatasetBuilder.build(config, cache_dir=cache_dir)
    initial = {
        "handler_factory": stub_qlib.handler_factory,
        "DatasetH": stub_qlib.DatasetH,
        "prepare": stub_qlib.prepare,
    }
    # Second call: must hit the cache, NOT call the factory.
    result = FeatureDatasetBuilder.build(config, cache_dir=cache_dir)
    assert result is not None
    assert stub_qlib.handler_factory == initial["handler_factory"]
    assert stub_qlib.DatasetH == initial["DatasetH"]
    assert stub_qlib.prepare == initial["prepare"]


def test_build_different_config_misses_cache(stub_qlib, tmp_path):
    cache_dir = tmp_path / "cache"
    config_a = _make_config(instruments="csi300")
    config_b = _make_config(instruments="csi500")
    FeatureDatasetBuilder.build(config_a, cache_dir=cache_dir)
    initial = stub_qlib.handler_factory
    FeatureDatasetBuilder.build(config_b, cache_dir=cache_dir)
    # Two different configs → two builds, two cache files.
    assert stub_qlib.handler_factory == initial + 1
    assert len(list(cache_dir.glob("dataset_*.pkl"))) == 2


def test_build_with_pit_provider_bypasses_cache(stub_qlib, tmp_path, monkeypatch):
    """``pit_provider`` is incompatible with cache (live state); the
    builder must bypass cache lookup AND skip cache write even when
    cache_dir is set."""
    config = _make_config()
    cache_dir = tmp_path / "cache"

    # Stub out the PIT-validation path so the test doesn't need a
    # real PIT provider.
    monkeypatch.setattr(
        FeatureDatasetBuilder,
        "_validate_pit_provider_alignment",
        classmethod(lambda cls, _p: None),
    )

    fake_pit = object()
    FeatureDatasetBuilder.build(
        config, cache_dir=cache_dir, pit_provider=fake_pit,
    )
    # No cache written under any key.
    assert not cache_dir.exists() or not list(cache_dir.glob("*.pkl"))


def test_build_corrupt_cache_falls_back_to_rebuild(stub_qlib, tmp_path):
    """If a cache file exists but is corrupt, the build must fall
    through to a fresh build (not raise)."""
    config = _make_config()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    # Compute key, write corrupt file at that path
    key = compute_cache_key(config)
    (cache_dir / f"dataset_{key}.pkl").write_bytes(b"garbage")
    # Must not raise
    result = FeatureDatasetBuilder.build(config, cache_dir=cache_dir)
    assert result is not None
    # Build was forced to run despite the cache file existing
    assert stub_qlib.handler_factory == 1
    # Fresh write should have replaced the corrupt blob
    with (cache_dir / f"dataset_{key}.pkl").open("rb") as f:
        # If this raises, the corrupt blob wasn't replaced.
        loaded = pickle.load(f)
    assert isinstance(loaded, FeatureDatasetResult)
