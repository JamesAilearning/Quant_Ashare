"""Tests for the PR5 follow-up: dataset_cache_dir threaded through
WalkForwardConfig → engine → FeatureDatasetBuilder.

PR5 (#152) shipped the cache module but the walk-forward engine
didn't pass `cache_dir` to `FeatureDatasetBuilder.build()`. This
verifies the wiring + CLI flag + env var fallback.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_walk_forward import _parse_cli  # noqa: E402
from src.core.walk_forward._resume import _ResumeKind  # noqa: E402
from src.core.walk_forward.config import WalkForwardConfig  # noqa: E402


def _synthetic_trading_calendar() -> list[date]:
    """Continuous Mon-Fri calendar injected via _load_trading_calendar so
    run() needs no real qlib bundle (the embargo gap added in
    fix-walk-forward-embargo-gap makes _generate_windows read the calendar)."""
    out, d = [], date(2015, 1, 1)
    while d <= date(2027, 12, 31):
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


_SYNTH_CAL = _synthetic_trading_calendar()


# ---------------------------------------------------------------------------
# WalkForwardConfig has the new field
# ---------------------------------------------------------------------------


class WalkForwardConfigCacheFieldTests(unittest.TestCase):
    def test_default_is_none(self):
        cfg = WalkForwardConfig()
        self.assertIsNone(cfg.dataset_cache_dir)

    def test_explicit_value_is_preserved(self):
        cfg = WalkForwardConfig(dataset_cache_dir="output/.cache")
        self.assertEqual(cfg.dataset_cache_dir, "output/.cache")

    def test_frozen_dataclass_replace_supported(self):
        """dataclasses.replace() must work — that's how the CLI override
        path applies the new value to an existing config."""
        import dataclasses

        cfg = WalkForwardConfig(dataset_cache_dir="a")
        cfg2 = dataclasses.replace(cfg, dataset_cache_dir="b")
        self.assertEqual(cfg2.dataset_cache_dir, "b")
        # Original unchanged (frozen).
        self.assertEqual(cfg.dataset_cache_dir, "a")


# ---------------------------------------------------------------------------
# CLI flag parsing
# ---------------------------------------------------------------------------


class CliDatasetCacheFlagTests(unittest.TestCase):
    def test_default_is_none(self):
        _config, _resume, override = _parse_cli([])
        self.assertIsNone(override)

    def test_explicit_path(self):
        _config, _resume, override = _parse_cli(
            ["walk.yaml", "--dataset-cache-dir", "~/.cache/qlib/datasets"],
        )
        self.assertEqual(override, "~/.cache/qlib/datasets")

    def test_empty_string_passed_through(self):
        """Empty string is the explicit-disable sentinel — must be
        forwarded verbatim to WalkForwardConfig.dataset_cache_dir so
        the engine knows to bypass the QLIB_DATASET_CACHE_DIR fallback.

        Regression for Codex P2 on PR #155: an earlier draft converted
        ``""`` to ``None`` in main(), which let the env var re-enable
        caching behind the operator's back.
        """
        _config, _resume, override = _parse_cli(
            ["walk.yaml", "--dataset-cache-dir", ""],
        )
        self.assertEqual(override, "")

    def test_combinable_with_resume_flags(self):
        config, resume, override = _parse_cli(
            ["walk.yaml", "--no-resume", "--dataset-cache-dir", "/tmp/ds"],
        )
        self.assertEqual(config, "walk.yaml")
        self.assertEqual(resume.kind, _ResumeKind.FORCE_RERUN)
        self.assertEqual(override, "/tmp/ds")


# ---------------------------------------------------------------------------
# Engine threads cache_dir through to FeatureDatasetBuilder.build
# ---------------------------------------------------------------------------


class EnginePassesCacheDirToBuilderTests(unittest.TestCase):
    """We mock FeatureDatasetBuilder.build to capture its kwargs, then
    assert the cache_dir argument matches what the config (or env var)
    said."""

    def _stub_build(self):
        from unittest.mock import MagicMock

        from src.core.walk_forward import _types

        captured = {"calls": []}
        fake_result = MagicMock()
        fake_result.dataset = MagicMock()
        # Shapes the engine reads for logging
        fake_result.train_shape = (10, 5)
        fake_result.valid_shape = (3, 5)
        fake_result.test_shape = (3, 5)
        fake_result.feature_columns = ("f1",)
        # ensure prediction_shape on the returned fold is non-zero so
        # the engine writes a manifest (cache-dir-aware code lives in
        # the same _run_single_fold path as the manifest)
        _ = _types  # silence unused
        return captured, fake_result

    def test_engine_passes_config_cache_dir(self):
        from src.core.walk_forward.engine import WalkForwardEngine

        captured = {"cache_dir": None}

        # Spy on FeatureDatasetBuilder.build to capture the cache_dir
        # kwarg, then raise to abort the rest of _run_single_fold
        # cleanly — engine catches the exception and installs a
        # NaN-placeholder fold, then advances to the next window.
        def spy(config, *, pit_provider=None, cache_dir=None):  # noqa: ARG001
            captured["cache_dir"] = cache_dir
            raise RuntimeError("intentional short-circuit")

        cfg = WalkForwardConfig(
            overall_start="2024-01-01",
            overall_end="2024-12-31",
            train_months=3, valid_months=1, test_months=1, step_months=12,
            output_dir="/tmp/wf_test_irrelevant",
            dataset_cache_dir="/expected/cache/path",
        )

        with patch(
            "src.core.walk_forward.engine.is_canonical_qlib_initialized",
            return_value=True,
        ), patch(
            "src.core.walk_forward.engine.FeatureDatasetBuilder.build",
            side_effect=spy,
        ), patch(
            "src.core.walk_forward.engine.compute_aggregate",
            return_value={},
        ), patch(
            "src.core.walk_forward.engine.write_aggregate_report",
        ), patch.object(
            WalkForwardEngine, "_load_trading_calendar", return_value=_SYNTH_CAL,
        ):
            # Run the engine — _run_single_fold will hit our spy, raise,
            # and the engine will install a NaN-placeholder fold.
            WalkForwardEngine.run(cfg)

        self.assertIsNotNone(captured["cache_dir"])
        self.assertEqual(str(captured["cache_dir"]), str(Path("/expected/cache/path").expanduser()))

    def test_engine_falls_back_to_env_var_when_config_field_is_none(self):
        from src.core.walk_forward.engine import WalkForwardEngine

        captured = {"cache_dir": None}

        def spy(config, *, pit_provider=None, cache_dir=None):  # noqa: ARG001
            captured["cache_dir"] = cache_dir
            raise RuntimeError("intentional short-circuit")

        cfg = WalkForwardConfig(
            overall_start="2024-01-01",
            overall_end="2024-12-31",
            train_months=3, valid_months=1, test_months=1, step_months=12,
            output_dir="/tmp/wf_test_irrelevant_2",
            dataset_cache_dir=None,
        )

        with patch.dict(os.environ, {"QLIB_DATASET_CACHE_DIR": "/env/cache"}, clear=False):
            with patch(
                "src.core.walk_forward.engine.is_canonical_qlib_initialized",
                return_value=True,
            ), patch(
                "src.core.walk_forward.engine.FeatureDatasetBuilder.build",
                side_effect=spy,
            ), patch(
                "src.core.walk_forward.engine.compute_aggregate",
                return_value={},
            ), patch(
                "src.core.walk_forward.engine.write_aggregate_report",
            ), patch.object(
                WalkForwardEngine, "_load_trading_calendar", return_value=_SYNTH_CAL,
            ):
                WalkForwardEngine.run(cfg)

        self.assertIsNotNone(captured["cache_dir"])
        self.assertEqual(str(captured["cache_dir"]), str(Path("/env/cache").expanduser()))

    def test_engine_explicit_disable_bypasses_env_var(self):
        """Regression for Codex P2 review on PR #155:
        ``--dataset-cache-dir ""`` (empty-string sentinel) must
        force cache-off even when ``QLIB_DATASET_CACHE_DIR`` is set
        globally. Otherwise operators in env-var environments cannot
        actually disable the cache from the CLI."""
        from src.core.walk_forward.engine import WalkForwardEngine

        captured = {"cache_dir": "sentinel"}  # not None to detect non-overwrite

        def spy(config, *, pit_provider=None, cache_dir=None):  # noqa: ARG001
            captured["cache_dir"] = cache_dir
            raise RuntimeError("intentional short-circuit")

        cfg = WalkForwardConfig(
            overall_start="2024-01-01",
            overall_end="2024-12-31",
            train_months=3, valid_months=1, test_months=1, step_months=12,
            output_dir="/tmp/wf_test_explicit_disable",
            dataset_cache_dir="",  # explicit-disable sentinel
        )

        # Env var IS set — but the explicit-disable sentinel must win.
        with patch.dict(
            os.environ, {"QLIB_DATASET_CACHE_DIR": "/env/cache"}, clear=False,
        ):
            with patch(
                "src.core.walk_forward.engine.is_canonical_qlib_initialized",
                return_value=True,
            ), patch(
                "src.core.walk_forward.engine.FeatureDatasetBuilder.build",
                side_effect=spy,
            ), patch(
                "src.core.walk_forward.engine.compute_aggregate",
                return_value={},
            ), patch(
                "src.core.walk_forward.engine.write_aggregate_report",
            ), patch.object(
                WalkForwardEngine, "_load_trading_calendar", return_value=_SYNTH_CAL,
            ):
                WalkForwardEngine.run(cfg)

        # Empty-string sentinel → cache disabled; env var ignored.
        self.assertIsNone(captured["cache_dir"])

    def test_engine_passes_none_when_neither_set(self):
        from src.core.walk_forward.engine import WalkForwardEngine

        captured = {"cache_dir": "sentinel"}  # not None to detect non-overwrite

        def spy(config, *, pit_provider=None, cache_dir=None):  # noqa: ARG001
            captured["cache_dir"] = cache_dir
            raise RuntimeError("intentional short-circuit")

        cfg = WalkForwardConfig(
            overall_start="2024-01-01",
            overall_end="2024-12-31",
            train_months=3, valid_months=1, test_months=1, step_months=12,
            output_dir="/tmp/wf_test_irrelevant_3",
            dataset_cache_dir=None,
        )

        # Clear env var so the fallback also resolves to None.
        env_without = {k: v for k, v in os.environ.items()
                       if k != "QLIB_DATASET_CACHE_DIR"}
        with patch.dict(os.environ, env_without, clear=True):
            with patch(
                "src.core.walk_forward.engine.is_canonical_qlib_initialized",
                return_value=True,
            ), patch(
                "src.core.walk_forward.engine.FeatureDatasetBuilder.build",
                side_effect=spy,
            ), patch(
                "src.core.walk_forward.engine.compute_aggregate",
                return_value={},
            ), patch(
                "src.core.walk_forward.engine.write_aggregate_report",
            ), patch.object(
                WalkForwardEngine, "_load_trading_calendar", return_value=_SYNTH_CAL,
            ):
                WalkForwardEngine.run(cfg)

        # Cache disabled → builder called with cache_dir=None.
        self.assertIsNone(captured["cache_dir"])


if __name__ == "__main__":
    unittest.main()
