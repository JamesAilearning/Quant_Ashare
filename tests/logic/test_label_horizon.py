"""Tests for the configurable label horizon (add-label-horizon-config).

Pure synthetic / mock — no qlib, no bundle (dev-batch red line). Covers the
spec's scenarios: expression identity (H=1 byte-identical, H=5 documented),
invalid-horizon refusal, cache-key separation + default key stability, the
horizon-driven embargo on all three consumers (builder helper via
validate_segment_embargo, engine gap arithmetic via label_lookahead_days, the
operator-UI guard), resume-fingerprint fold-in + fail-loud cause naming, and
the SignalAnalyzer label-independence pin.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.pipeline import PipelineConfig, PipelineError  # noqa: E402
from src.core.walk_forward._resume import (  # noqa: E402
    FoldManifest,
    ResumeMode,
    compute_config_fingerprint,
    decide_fold,
)
from src.core.walk_forward.config import WalkForwardConfig, WalkForwardError  # noqa: E402
from src.data._feature_dataset_cache import compute_cache_key  # noqa: E402
from src.data._segment_embargo import (  # noqa: E402
    LABEL_LOOKAHEAD_DAYS,
    label_lookahead_days,
    validate_segment_embargo,
)
from src.data.feature_dataset_builder import (  # noqa: E402
    FeatureDatasetConfig,
    alpha158_label_expression,
)


def _cal(n: int, start: str = "2025-01-01") -> list[date]:
    d0 = date.fromisoformat(start)
    return [d0 + timedelta(days=i) for i in range(n)]


def _fdc(h: int = 1) -> FeatureDatasetConfig:
    return FeatureDatasetConfig(
        instruments="csi300", feature_handler="Alpha158",
        train_start="2022-01-01", train_end="2024-12-31",
        valid_start="2025-01-01", valid_end="2025-06-30",
        test_start="2025-07-01", test_end="2025-12-31",
        label_horizon_days=h,
    )


class LabelExpressionTests(unittest.TestCase):
    def test_default_reproduces_qlib_hardcoded_label_exactly(self) -> None:
        # THE byte-identity pin: H=1 must equal qlib Alpha158.get_label_config()'s
        # hard-coded expression character-for-character (REGEN-2 anchor depends on it).
        self.assertEqual(
            alpha158_label_expression(1), "Ref($close, -2)/Ref($close, -1) - 1",
        )

    def test_h5_produces_documented_expression(self) -> None:
        self.assertEqual(
            alpha158_label_expression(5), "Ref($close, -6)/Ref($close, -1) - 1",
        )


class LookaheadHelperTests(unittest.TestCase):
    def test_default_reads_module_constant(self) -> None:
        self.assertEqual(label_lookahead_days(1), LABEL_LOOKAHEAD_DAYS)
        self.assertEqual(label_lookahead_days(1), 2)  # today's value

    def test_h5_is_h_plus_one(self) -> None:
        self.assertEqual(label_lookahead_days(5), 6)

    def test_h1_respects_patched_constant(self) -> None:
        # the engine's embargo-gap test patches the constant; the helper must
        # read it at call time for H=1 (documented compatibility)
        from unittest.mock import patch

        import src.data._segment_embargo as emb
        with patch.object(emb, "LABEL_LOOKAHEAD_DAYS", 7):
            self.assertEqual(emb.label_lookahead_days(1), 7)

    def test_invalid_horizons_refused(self) -> None:
        for bad in (0, -1, 1.5, True, "5", None):
            with self.assertRaises(ValueError, msg=f"bad={bad!r}"):
                label_lookahead_days(bad)  # type: ignore[arg-type]


class ConfigValidationTests(unittest.TestCase):
    def test_walk_forward_config_rejects_invalid_horizon(self) -> None:
        for bad in (0, -3, 1.5, True):
            with self.assertRaises(WalkForwardError, msg=f"bad={bad!r}"):
                WalkForwardConfig(label_horizon_days=bad)  # type: ignore[arg-type]

    def test_pipeline_config_rejects_invalid_horizon(self) -> None:
        for bad in (0, -3, 1.5, True):
            with self.assertRaises(PipelineError, msg=f"bad={bad!r}"):
                PipelineConfig(provider_uri="/tmp/fake", label_horizon_days=bad)  # type: ignore[arg-type]

    def test_default_configs_accept(self) -> None:
        self.assertEqual(WalkForwardConfig().label_horizon_days, 1)
        self.assertEqual(PipelineConfig(provider_uri="/tmp/fake").label_horizon_days, 1)

    def test_non_alpha_handler_with_horizon_refused_at_config_load(self) -> None:
        # codex P2 on #318: the walk-forward engine's per-fold error isolation
        # would convert FeatureDatasetBuilder's later rejection into all-NaN
        # placeholder folds — an unsupported config must refuse at CONFIG
        # construction, before any fold starts. Both engines, same rule.
        with self.assertRaises(WalkForwardError) as cm:
            WalkForwardConfig(feature_handler="MinedFactor", label_horizon_days=5)
        self.assertIn("silently ignore", str(cm.exception))
        with self.assertRaises(PipelineError):
            PipelineConfig(provider_uri="/tmp/fake",
                           feature_handler="MinedFactor", label_horizon_days=5)

    def test_non_alpha_handler_with_default_horizon_accepts(self) -> None:
        # H=1 stays universally valid — the refusal is only for a horizon the
        # handler would ignore, not for using another handler at all.
        # (adjust_mode: MinedFactor's own pre-existing requirement, unrelated.)
        cfg = WalkForwardConfig(
            feature_handler="MinedFactor", adjust_mode="post_adjusted",
        )
        self.assertEqual(cfg.label_horizon_days, 1)


class CacheKeyTests(unittest.TestCase):
    def test_horizons_never_share_cache_keys(self) -> None:
        k1 = compute_cache_key(_fdc(1), bundle_tag="t", handler_identity="alpha158_default")
        k5 = compute_cache_key(_fdc(5), bundle_tag="t", handler_identity="alpha158_default")
        self.assertNotEqual(k1, k5)

    def test_default_key_is_byte_identical_to_pre_change_schema(self) -> None:
        # the pre-change payload schema, replicated literally: H=1 must hash to
        # EXACTLY this (existing cache entries stay valid).
        cfg = _fdc(1)
        pre_change_payload = {
            "instruments": cfg.instruments,
            "feature_handler": cfg.feature_handler,
            "train_start": cfg.train_start,
            "train_end": cfg.train_end,
            "valid_start": cfg.valid_start,
            "valid_end": cfg.valid_end,
            "test_start": cfg.test_start,
            "test_end": cfg.test_end,
            "bundle_tag": "t",
            "handler_identity": "alpha158_default",
        }
        expected = hashlib.sha256(
            json.dumps(pre_change_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:32]
        self.assertEqual(
            compute_cache_key(cfg, bundle_tag="t", handler_identity="alpha158_default"),
            expected,
        )


class EmbargoTests(unittest.TestCase):
    def test_h5_refuses_two_day_gap(self) -> None:
        cal = _cal(60)
        # boundaries 3 calendar days apart -> 2 trading days strictly between
        errors = validate_segment_embargo(
            train_end=cal[10], valid_start=cal[13],
            valid_end=cal[30], test_start=cal[33],
            calendar=cal,
            lookahead_days=label_lookahead_days(5),
        )
        self.assertEqual(len(errors), 2)  # both boundaries violate 6-day embargo

    def test_h1_two_day_gap_unchanged(self) -> None:
        cal = _cal(60)
        errors = validate_segment_embargo(
            train_end=cal[10], valid_start=cal[13],
            valid_end=cal[30], test_start=cal[33],
            calendar=cal,
            lookahead_days=label_lookahead_days(1),
        )
        self.assertEqual(errors, [])  # 2 trading days between = today's requirement

    def test_ui_guard_is_horizon_aware(self) -> None:
        # the operator-UI guard (3rd consumer) shares the same helper: a gap
        # legal at H=1 is refused at H=5, through the UI code path itself.
        from web.operator_ui.training_guards import _validate_segment_embargo

        cal = tuple(_cal(60))
        parsed: dict[str, date | None] = {
            "train_end": cal[10], "valid_start": cal[13],
            "valid_end": cal[30], "test_start": cal[33],
        }
        metadata = SimpleNamespace(calendar_dates=cal)
        errors_h1: list[str] = []
        _validate_segment_embargo(parsed, metadata, errors_h1)  # type: ignore[arg-type]
        self.assertEqual(errors_h1, [])
        errors_h5: list[str] = []
        _validate_segment_embargo(parsed, metadata, errors_h5,  # type: ignore[arg-type]
                                  label_horizon_days=5)
        self.assertEqual(len(errors_h5), 2)
        self.assertIn("持有期 5", errors_h5[0])


class ResumeTests(unittest.TestCase):
    def test_fingerprints_differ_across_horizons(self) -> None:
        f1 = compute_config_fingerprint(WalkForwardConfig(label_horizon_days=1))
        f5 = compute_config_fingerprint(WalkForwardConfig(label_horizon_days=5))
        self.assertNotEqual(f1, f5)

    def _manifest(self, horizon: int | None) -> FoldManifest:
        from src.core.walk_forward._types import WalkForwardFold

        cfg = WalkForwardConfig(label_horizon_days=horizon or 1)
        fold = WalkForwardFold(
            fold_index=0, train_period="a ~ b", valid_period="c ~ d",
            test_period="e ~ f", ic_1d=0.01, ic_5d=0.02,
            annualized_return=0.1, max_drawdown=-0.05,
            information_ratio=0.5, prediction_shape=(10, 5),
        )
        m = FoldManifest.from_fold(
            fold=fold, config=cfg,
            model_path="m", report_path="r", predictions_path="p",
            positions_path=None,
        )
        if horizon is None:  # simulate a pre-upgrade manifest
            import dataclasses
            m = dataclasses.replace(m, label_horizon_days=None)
        return m

    def test_manifest_roundtrips_horizon(self) -> None:
        m = self._manifest(5)
        reborn = FoldManifest.from_dict(json.loads(json.dumps(m.to_dict())))
        self.assertEqual(reborn.label_horizon_days, 5)

    def test_legacy_manifest_loads_horizon_as_none(self) -> None:
        payload = self._manifest(1).to_dict()
        del payload["label_horizon_days"]
        self.assertIsNone(FoldManifest.from_dict(payload).label_horizon_days)

    def test_horizon_change_rerun_names_both_values(self) -> None:
        m = self._manifest(1)
        decision = decide_fold(
            fold_index=0, train_period="a ~ b", test_period="e ~ f",
            valid_period="c ~ d",
            config_fingerprint="DIFFERENT",
            discovered={0: m},
            resume_mode=ResumeMode.AUTO,
            label_horizon_days=5,
        )
        self.assertFalse(decision.skip)
        self.assertIn("label_horizon_days changed: manifest=1, config=5",
                      decision.reason)
        self.assertIn("expected", decision.reason)

    def test_pre_upgrade_manifest_rerun_names_the_upgrade(self) -> None:
        m = self._manifest(None)
        decision = decide_fold(
            fold_index=0, train_period="a ~ b", test_period="e ~ f",
            valid_period="c ~ d",
            config_fingerprint="DIFFERENT",
            discovered={0: m},
            resume_mode=ResumeMode.AUTO,
            label_horizon_days=1,
        )
        self.assertIn("predates label-horizon stamping", decision.reason)

    def test_same_horizon_mismatch_stays_generic(self) -> None:
        # a mismatch NOT caused by the horizon must not blame the horizon
        m = self._manifest(5)
        decision = decide_fold(
            fold_index=0, train_period="a ~ b", test_period="e ~ f",
            valid_period="c ~ d",
            config_fingerprint="DIFFERENT",
            discovered={0: m},
            resume_mode=ResumeMode.AUTO,
            label_horizon_days=5,
        )
        self.assertIn("fingerprint_mismatch", decision.reason)
        self.assertNotIn("label_horizon_days changed", decision.reason)


class SignalAnalyzerIndependenceTests(unittest.TestCase):
    def test_ic_measurement_has_no_label_input(self) -> None:
        # The label-independence pin: the analyzer's measurement API surface has
        # NO label/horizon parameter anywhere — IC is computed from REALIZED
        # prices at fixed forward periods (the T+1→T+1+period arithmetic itself
        # is pinned by test_compute_daily_ic_entry_offset_arithmetic). Changing
        # the training label horizon therefore cannot change what the IC
        # diagnostics measure.
        import inspect

        from src.core.signal_analyzer import SignalAnalysisConfig, SignalAnalyzer

        cfg_fields = set(inspect.signature(SignalAnalysisConfig).parameters)
        self.assertFalse(
            {f for f in cfg_fields if "label" in f or "horizon" in f},
            f"SignalAnalysisConfig grew a label/horizon knob: {cfg_fields} — "
            "update the label-independence contract before threading it.",
        )
        ic_params = set(
            inspect.signature(SignalAnalyzer._compute_daily_ic).parameters
        )
        self.assertFalse(
            {f for f in ic_params if "label" in f or "horizon" in f},
            f"_compute_daily_ic grew a label/horizon parameter: {ic_params}.",
        )


if __name__ == "__main__":
    unittest.main()
