"""Tests for audit P2 — cache key incorporates Tushare manifest +
handler-bound state (MinedFactor pool, PIT provider, registry, pool
parquet contents).

Before this fix:
  - Tushare bundles always returned ``bundle_tag="unknown"`` because
    ``read_bundle_tag`` only knew about ``bundle_manifest.json``,
    so re-ingesting did not invalidate the cache.
  - The cache key used ``feature_handler="MinedFactor"`` (the name)
    but ignored the bound pool, so switching pools served stale
    features under the new pool's name.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data._feature_dataset_cache import (  # noqa: E402
    _LEGACY_BUNDLE_TAG,
    compute_cache_key,
    read_bundle_tag,
)
from src.data.feature_dataset_builder import (  # noqa: E402
    FeatureDatasetConfig,
    _reset_feature_handler_registry_to_defaults,
    get_feature_handler_cache_identity,
    register_feature_handler,
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


# ---------------------------------------------------------------------------
# read_bundle_tag — Tushare fallback
# ---------------------------------------------------------------------------


class ReadBundleTagTushareFallbackTests(unittest.TestCase):
    def test_canonical_manifest_wins_when_both_present(self):
        """If both manifests exist, the canonical PR149 file (with
        ``tail_date``) takes precedence over Tushare's variant."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "bundle_manifest.json").write_text(
                json.dumps({"tail_date": "2026-05-01"})
            )
            (p / "tushare_provider_manifest.json").write_text(
                json.dumps({
                    "coverage_end_date": "2026-03-06",
                    "snapshot_at": "2026-04-01T00:00:00Z",
                })
            )
            self.assertEqual(read_bundle_tag(td), "2026-05-01")

    def test_bundle_manifest_with_content_hash_returns_composite(self):
        """When PR #175's ``content_hash`` is present, the bundle tag
        is ``"<tail>@<hash>"`` so two bundles that happen to share the
        same tail_date but differ in calendar bytes get distinct cache
        keys. Codex P1 on PR #175.
        """
        import tempfile

        good_hash = "sha256:" + ("a" * 64)
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "bundle_manifest.json").write_text(
                json.dumps({
                    "tail_date": "2026-05-01",
                    "content_hash": good_hash,
                })
            )
            self.assertEqual(
                read_bundle_tag(td),
                f"2026-05-01@{good_hash}",
            )

    def test_same_tail_different_content_hash_produces_different_tags(self):
        """Regression for the cross-feature integration hole: a re-
        ingest that lands on the same tail_date with a different
        calendar produces a different content_hash, which MUST flow
        through into the cache tag — otherwise feature dataset cache
        would silently serve stale data across that re-ingest. Codex
        P1 on PR #175.
        """
        import tempfile

        hash_a = "sha256:" + ("a" * 64)
        hash_b = "sha256:" + ("b" * 64)
        with tempfile.TemporaryDirectory() as td_a, tempfile.TemporaryDirectory() as td_b:
            (Path(td_a) / "bundle_manifest.json").write_text(
                json.dumps({
                    "tail_date": "2026-05-01",  # same tail
                    "content_hash": hash_a,
                })
            )
            (Path(td_b) / "bundle_manifest.json").write_text(
                json.dumps({
                    "tail_date": "2026-05-01",  # same tail
                    "content_hash": hash_b,     # different bytes
                })
            )
            tag_a = read_bundle_tag(td_a)
            tag_b = read_bundle_tag(td_b)
            self.assertNotEqual(
                tag_a, tag_b,
                "Same-tail / different-content_hash bundles MUST get "
                "different cache tags or the cache silently serves "
                "stale features after a bundle correction.",
            )
            # And both tags individually carry the hash, not just the
            # tail — so a future refactor that drops one half won't
            # silently re-introduce the bug.
            self.assertIn(hash_a, tag_a)
            self.assertIn(hash_b, tag_b)

    def test_legacy_bundle_manifest_without_content_hash_still_returns_tail(self):
        """Backwards compat: a manifest emitted before PR #175 has no
        ``content_hash`` field. The tag stays exactly the same as
        before (bare ``tail_date``) so existing cache entries are not
        invalidated by adopting this PR.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "bundle_manifest.json").write_text(
                json.dumps({"tail_date": "2026-05-01"})
            )
            self.assertEqual(read_bundle_tag(td), "2026-05-01")

    def test_explicit_null_content_hash_falls_back_to_tail_only(self):
        """``"content_hash": null`` on disk is rejected by
        ``load_manifest`` itself (separate Codex P2 in this PR), but
        ``read_bundle_tag`` is best-effort and runs even on manifests
        that haven't been validated yet. Treat ``null`` here as "no
        hash available" — same as a missing field — so cache
        invalidation gracefully falls back instead of crashing.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "bundle_manifest.json").write_text(
                json.dumps({
                    "tail_date": "2026-05-01",
                    "content_hash": None,
                })
            )
            self.assertEqual(read_bundle_tag(td), "2026-05-01")

    def test_tushare_manifest_composite_tag(self):
        """The Tushare fallback returns ``tushare:<coverage>@<snapshot>``
        — both fields combined so a re-ingest of the same window still
        produces a distinct tag."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "tushare_provider_manifest.json").write_text(
                json.dumps({
                    "coverage_end_date": "2026-03-06",
                    "snapshot_at": "2026-04-25T12:00:00Z",
                })
            )
            self.assertEqual(
                read_bundle_tag(td),
                "tushare:2026-03-06@2026-04-25T12:00:00Z",
            )

    def test_tushare_snapshot_change_invalidates_cache(self):
        """Two publishes of the same coverage window with different
        snapshot_at produce different tags — proving cache
        invalidation works for Tushare bundles. Regression for the
        audit P2 issue where Tushare always returned ``"unknown"``."""
        import tempfile

        with tempfile.TemporaryDirectory() as td_a, tempfile.TemporaryDirectory() as td_b:
            (Path(td_a) / "tushare_provider_manifest.json").write_text(
                json.dumps({
                    "coverage_end_date": "2026-03-06",
                    "snapshot_at": "2026-04-01T00:00:00Z",
                })
            )
            (Path(td_b) / "tushare_provider_manifest.json").write_text(
                json.dumps({
                    "coverage_end_date": "2026-03-06",  # same coverage
                    "snapshot_at": "2026-04-25T00:00:00Z",  # diff snapshot
                })
            )
            self.assertNotEqual(read_bundle_tag(td_a), read_bundle_tag(td_b))

    def test_tushare_only_coverage_falls_back_to_coverage_only(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "tushare_provider_manifest.json").write_text(
                json.dumps({
                    "coverage_end_date": "2026-03-06",
                    "snapshot_at": "",
                })
            )
            self.assertEqual(read_bundle_tag(td), "tushare:2026-03-06")

    def test_tushare_malformed_falls_back_to_unknown(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "tushare_provider_manifest.json").write_text(
                "{not valid json"
            )
            self.assertEqual(read_bundle_tag(td), _LEGACY_BUNDLE_TAG)

    def test_no_manifest_unknown(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(read_bundle_tag(td), _LEGACY_BUNDLE_TAG)


# ---------------------------------------------------------------------------
# compute_cache_key — handler_identity
# ---------------------------------------------------------------------------


class ComputeCacheKeyHandlerIdentityTests(unittest.TestCase):
    def test_same_identity_same_key(self):
        cfg = _make_config()
        a = compute_cache_key(cfg, bundle_tag="b1", handler_identity="X")
        b = compute_cache_key(cfg, bundle_tag="b1", handler_identity="X")
        self.assertEqual(a, b)

    def test_different_identity_different_key(self):
        cfg = _make_config()
        a = compute_cache_key(cfg, bundle_tag="b1", handler_identity="pool_v1")
        b = compute_cache_key(cfg, bundle_tag="b1", handler_identity="pool_v2")
        self.assertNotEqual(a, b)

    def test_none_identity_distinct_from_explicit_sentinel(self):
        cfg = _make_config()
        none_key = compute_cache_key(cfg, bundle_tag="b1", handler_identity=None)
        same_key = compute_cache_key(cfg, bundle_tag="b1", handler_identity=None)
        explicit_key = compute_cache_key(
            cfg, bundle_tag="b1", handler_identity="_no_handler_identity_",
        )
        # Two None-identity calls produce the same key (stable
        # fallback), but the literal sentinel string is the same as
        # the None fallback (documented contract).
        self.assertEqual(none_key, same_key)
        self.assertEqual(none_key, explicit_key)


# ---------------------------------------------------------------------------
# Handler registry cache_identity
# ---------------------------------------------------------------------------


class HandlerCacheIdentityRegistryTests(unittest.TestCase):
    def setUp(self):
        _reset_feature_handler_registry_to_defaults()

    def tearDown(self):
        _reset_feature_handler_registry_to_defaults()

    def test_alpha158_default_has_identity(self):
        """Default Alpha158 registration ships with a constant
        identity so it's cacheable out of the box."""
        self.assertEqual(
            get_feature_handler_cache_identity("Alpha158"),
            "alpha158_default",
        )

    def test_unregistered_handler_identity_is_none(self):
        self.assertIsNone(get_feature_handler_cache_identity("DoesNotExist"))

    def test_handler_registered_without_identity_returns_none(self):
        register_feature_handler(
            "NoIdHandler", lambda _cfg: None, replace=True,
        )
        self.assertIsNone(get_feature_handler_cache_identity("NoIdHandler"))

    def test_callable_identity_invoked_each_time(self):
        calls = {"n": 0}

        def _identity():
            calls["n"] += 1
            return f"call_{calls['n']}"

        register_feature_handler(
            "CallableHandler", lambda _cfg: None,
            cache_identity=_identity, replace=True,
        )
        a = get_feature_handler_cache_identity("CallableHandler")
        b = get_feature_handler_cache_identity("CallableHandler")
        self.assertNotEqual(a, b)
        self.assertEqual(calls["n"], 2)

    def test_callable_identity_exception_returns_none(self):
        def _bad():
            raise RuntimeError("identity computation failed")

        register_feature_handler(
            "BadHandler", lambda _cfg: None,
            cache_identity=_bad, replace=True,
        )
        self.assertIsNone(get_feature_handler_cache_identity("BadHandler"))

    def test_invalid_cache_identity_type_rejected(self):
        from src.data.feature_dataset_builder import FeatureDatasetBuilderError

        with self.assertRaisesRegex(
            FeatureDatasetBuilderError, "cache_identity"
        ):
            register_feature_handler(
                "BadType", lambda _cfg: None,
                cache_identity=42,  # type: ignore[arg-type]
                replace=True,
            )

    def test_empty_string_cache_identity_rejected_at_registration(self):
        """Codex P2 on PR #158: empty-string identities silently
        collided with the ``_no_handler_identity_`` sentinel in
        compute_cache_key, so a mutable handler with an
        env/config-sourced empty identity would reuse cache entries
        from any OTHER empty-identity handler instead of being opted
        out. Fail loud at registration time."""
        from src.data.feature_dataset_builder import FeatureDatasetBuilderError

        for bad in ("", "   ", "\t\n"):
            with self.assertRaisesRegex(
                FeatureDatasetBuilderError,
                "empty/whitespace-only|empty string",
            ):
                register_feature_handler(
                    "EmptyIdentity", lambda _cfg: None,
                    cache_identity=bad, replace=True,
                )

    def test_getter_normalizes_empty_string_from_in_place_mutation(self):
        """Defence-in-depth: even if some test or downstream code
        bypasses ``register_feature_handler`` and mutates the
        registry dict directly with an empty-string descriptor,
        ``get_feature_handler_cache_identity`` returns ``None`` so
        the cache stays safely disabled."""
        from src.data.feature_dataset_builder import (
            _FEATURE_HANDLER_CACHE_IDENTITY,
            _FEATURE_HANDLER_REGISTRY,
        )

        _FEATURE_HANDLER_REGISTRY["DirectMutation"] = lambda _cfg: None
        _FEATURE_HANDLER_CACHE_IDENTITY["DirectMutation"] = ""
        self.assertIsNone(
            get_feature_handler_cache_identity("DirectMutation"),
        )

    def test_getter_treats_callable_returning_empty_as_none(self):
        """Same safety rule applies to callable identities: a
        callable returning ``""`` is treated as "no identity" so the
        cache doesn't silently bucket the handler with other empty-
        identity handlers."""

        register_feature_handler(
            "CallableEmpty", lambda _cfg: None,
            cache_identity=lambda: "", replace=True,
        )
        self.assertIsNone(get_feature_handler_cache_identity("CallableEmpty"))

    def test_getter_treats_callable_returning_whitespace_as_none(self):
        register_feature_handler(
            "CallableWS", lambda _cfg: None,
            cache_identity=lambda: "  \t  ", replace=True,
        )
        self.assertIsNone(get_feature_handler_cache_identity("CallableWS"))

    def test_replace_overwrites_identity(self):
        register_feature_handler(
            "X", lambda _cfg: None, cache_identity="v1", replace=True,
        )
        self.assertEqual(get_feature_handler_cache_identity("X"), "v1")
        register_feature_handler(
            "X", lambda _cfg: None, cache_identity="v2", replace=True,
        )
        self.assertEqual(get_feature_handler_cache_identity("X"), "v2")


# ---------------------------------------------------------------------------
# MinedFactor: bundle identity changes with pool / provider / registry
# ---------------------------------------------------------------------------


class MinedFactorBundleIdentityTests(unittest.TestCase):
    def _make_pool_dir(self, base: Path, payload: bytes = b"pool_a") -> Path:
        from src.factor_mining.factor_pool import (
            POOL_EXPR_JSON_FILENAME,
            POOL_PARQUET_FILENAME,
        )
        base.mkdir(parents=True, exist_ok=True)
        (base / POOL_PARQUET_FILENAME).write_bytes(payload)
        (base / POOL_EXPR_JSON_FILENAME).write_text("{}")
        return base

    def test_different_pool_dir_yields_different_identity(self):
        import tempfile

        from src.data.mined_factor_handler import (
            MinedFactorBundle,
            _compute_bundle_cache_identity,
        )

        with tempfile.TemporaryDirectory() as td:
            p1 = self._make_pool_dir(Path(td) / "pool_a", payload=b"AAA")
            p2 = self._make_pool_dir(Path(td) / "pool_b", payload=b"BBB")
            b1 = MinedFactorBundle(
                pool_dir=p1, pit_provider_uri="",
                delisted_registry_path="",
            )
            b2 = MinedFactorBundle(
                pool_dir=p2, pit_provider_uri="",
                delisted_registry_path="",
            )
            self.assertNotEqual(
                _compute_bundle_cache_identity(b1),
                _compute_bundle_cache_identity(b2),
            )

    def test_same_pool_dir_different_parquet_bytes_yields_different_identity(self):
        """Re-mining the SAME pool_dir with a different seed produces
        new parquet bytes. The identity must change so the cache
        doesn't serve features built on the old pool's expressions."""
        import tempfile

        from src.data.mined_factor_handler import (
            MinedFactorBundle,
            _compute_bundle_cache_identity,
        )

        with tempfile.TemporaryDirectory() as td:
            p = self._make_pool_dir(Path(td) / "pool", payload=b"first")
            bundle = MinedFactorBundle(
                pool_dir=p, pit_provider_uri="",
                delisted_registry_path="",
            )
            ident_a = _compute_bundle_cache_identity(bundle)
            # Re-mining: overwrite the parquet
            (p / "factor_pool.parquet").write_bytes(b"second")
            ident_b = _compute_bundle_cache_identity(bundle)
            self.assertNotEqual(ident_a, ident_b)

    def test_different_pit_provider_yields_different_identity(self):
        import tempfile

        from src.data.mined_factor_handler import (
            MinedFactorBundle,
            _compute_bundle_cache_identity,
        )

        with tempfile.TemporaryDirectory() as td:
            p = self._make_pool_dir(Path(td) / "pool")
            b1 = MinedFactorBundle(
                pool_dir=p, pit_provider_uri="/pit/a",
                delisted_registry_path="",
            )
            b2 = MinedFactorBundle(
                pool_dir=p, pit_provider_uri="/pit/b",
                delisted_registry_path="",
            )
            self.assertNotEqual(
                _compute_bundle_cache_identity(b1),
                _compute_bundle_cache_identity(b2),
            )

    def test_different_registry_yields_different_identity(self):
        import tempfile

        from src.data.mined_factor_handler import (
            MinedFactorBundle,
            _compute_bundle_cache_identity,
        )

        with tempfile.TemporaryDirectory() as td:
            p = self._make_pool_dir(Path(td) / "pool")
            b1 = MinedFactorBundle(
                pool_dir=p, pit_provider_uri="",
                delisted_registry_path="/reg/v1.parquet",
            )
            b2 = MinedFactorBundle(
                pool_dir=p, pit_provider_uri="",
                delisted_registry_path="/reg/v2.parquet",
            )
            self.assertNotEqual(
                _compute_bundle_cache_identity(b1),
                _compute_bundle_cache_identity(b2),
            )

    def test_register_mined_factor_handler_installs_callable_identity(self):
        """End-to-end: registering a MinedFactor bundle installs a
        callable identity that ``get_feature_handler_cache_identity``
        resolves to a bundle-derived string."""
        import tempfile

        from src.data.mined_factor_handler import (
            MinedFactorBundle,
            register_mined_factor_handler,
        )

        with tempfile.TemporaryDirectory() as td:
            p = self._make_pool_dir(Path(td) / "pool")
            bundle = MinedFactorBundle(
                pool_dir=p, pit_provider_uri="",
                delisted_registry_path="",
            )
            register_mined_factor_handler(
                bundle, name="MinedFactor", replace=True,
            )
            try:
                ident = get_feature_handler_cache_identity("MinedFactor")
                self.assertIsNotNone(ident)
                assert ident is not None
                self.assertTrue(ident.startswith("mined_factor:"))
            finally:
                _reset_feature_handler_registry_to_defaults()


# ---------------------------------------------------------------------------
# Builder integration: cache disabled when handler lacks identity
# ---------------------------------------------------------------------------


class BuilderSkipsCacheWithoutHandlerIdentityTests(unittest.TestCase):
    """When a handler is registered without ``cache_identity``, the
    builder must NOT consult or populate the cache — otherwise the
    cache silently serves stale features under the handler's name."""

    def setUp(self):
        _reset_feature_handler_registry_to_defaults()

    def tearDown(self):
        _reset_feature_handler_registry_to_defaults()

    def test_handler_without_identity_bypasses_cache(self):
        import tempfile

        from src.data.feature_dataset_builder import FeatureDatasetBuilder

        # Register a handler with NO cache_identity.
        class _StubHandler:
            pass

        class _StubDataset:
            def __init__(self, *_, **__):
                pass

            def prepare(self, _seg, col_set="feature"):
                import pandas as pd
                return pd.DataFrame(
                    {"f": [1.0, 2.0]},
                    index=pd.MultiIndex.from_product(
                        [pd.date_range("2024-04-01", periods=2), ["A"]],
                        names=["datetime", "instrument"],
                    ),
                )

        factory_calls = {"n": 0}

        def _factory(_cfg):
            factory_calls["n"] += 1
            return _StubHandler()

        register_feature_handler(
            "NoIdHandler", _factory, replace=True,  # NO cache_identity
        )

        config = FeatureDatasetConfig(
            instruments="csi300",
            feature_handler="NoIdHandler",
            train_start="2022-01-01", train_end="2023-12-15",
            valid_start="2024-01-01", valid_end="2024-03-15",
            test_start="2024-04-01", test_end="2024-06-30",
        )

        import qlib.data.dataset as _qd  # noqa: F401

        with tempfile.TemporaryDirectory() as td:
            with patch(
                "src.data.feature_dataset_builder.is_canonical_qlib_initialized",
                return_value=True,
            ), patch(
                "qlib.data.dataset.DatasetH", _StubDataset,
            ):
                # First call.
                FeatureDatasetBuilder.build(config, cache_dir=td)
                # Second call — cache should NOT be consulted because
                # the handler has no identity → factory MUST be called
                # a second time.
                FeatureDatasetBuilder.build(config, cache_dir=td)

            # Cache files written? Must be NO — the cache is bypassed.
            cache_files = list(Path(td).glob("dataset_*.pkl"))
            self.assertEqual(cache_files, [], "no cache writes expected")
            self.assertEqual(
                factory_calls["n"], 2,
                "handler factory must be invoked on every build when "
                "cache_identity is missing",
            )


if __name__ == "__main__":
    unittest.main()
