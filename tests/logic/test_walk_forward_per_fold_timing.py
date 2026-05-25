"""Tests for FU-4 — per-fold timing in walk-forward outputs.

The audit asked: ``8 fold 跑两小时，你现在不知道哪个 fold 拖累``.
This file pins the timing surface dimensionally:

* ``WalkForwardFold`` carries optional duration / started_at / finished_at.
* ``compute_aggregate`` surfaces mean / total / slowest-fold timing.
* ``build_aggregate_report`` exposes per-fold duration in the JSON.
* ``WalkForwardEngine.run`` stamps timing on every fold it runs
  (including failed folds — knowing "fold 5 took 8 min before OOMing"
  is useful diagnostic info).
* ``FoldManifest`` round-trips the timing fields.
* Legacy manifests / folds without timing data continue to work
  (the field defaults to ``None``).
"""

from __future__ import annotations

import math
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.walk_forward._resume import FoldManifest  # noqa: E402
from src.core.walk_forward._types import WalkForwardFold  # noqa: E402
from src.core.walk_forward.aggregate import (  # noqa: E402
    build_aggregate_report,
    compute_aggregate,
)
from src.core.walk_forward.config import WalkForwardConfig  # noqa: E402
from src.core.walk_forward.engine import WalkForwardEngine  # noqa: E402


def _fold(idx: int, *, duration=None, started=None, finished=None,
          ic_1d=0.05, prediction_shape=(100,)) -> WalkForwardFold:
    return WalkForwardFold(
        fold_index=idx,
        train_period="2022-01-01 ~ 2023-12-31",
        valid_period="2024-01-01 ~ 2024-03-31",
        test_period="2024-04-01 ~ 2024-06-30",
        ic_1d=ic_1d, ic_5d=0.04,
        annualized_return=0.10, max_drawdown=-0.05,
        information_ratio=0.5,
        prediction_shape=prediction_shape,
        duration_seconds=duration,
        started_at=started,
        finished_at=finished,
    )


# ---------------------------------------------------------------------------
# WalkForwardFold defaults
# ---------------------------------------------------------------------------


class WalkForwardFoldTimingFieldsTests(unittest.TestCase):
    def test_defaults_are_none(self):
        """Backward compat: existing callers (tests, mock setups) that
        construct a fold without timing data must still work."""
        fold = WalkForwardFold(
            fold_index=0,
            train_period="a", valid_period="b", test_period="c",
            ic_1d=0.0, ic_5d=0.0,
            annualized_return=0.0, max_drawdown=0.0,
            information_ratio=0.0,
            prediction_shape=(1,),
        )
        self.assertIsNone(fold.duration_seconds)
        self.assertIsNone(fold.started_at)
        self.assertIsNone(fold.finished_at)

    def test_replace_updates_timing(self):
        """``dataclasses.replace`` is how the engine stamps timing
        onto the fold after ``_run_single_fold`` returns. Must work."""
        f = _fold(0)
        f2 = replace(f, duration_seconds=12.5, started_at="t0", finished_at="t1")
        self.assertEqual(f2.duration_seconds, 12.5)
        self.assertEqual(f2.started_at, "t0")
        self.assertEqual(f2.finished_at, "t1")


# ---------------------------------------------------------------------------
# compute_aggregate
# ---------------------------------------------------------------------------


class ComputeAggregateTimingTests(unittest.TestCase):
    def test_mean_total_slowest_with_all_durations(self):
        folds = [
            _fold(0, duration=10.0),
            _fold(1, duration=30.0),
            _fold(2, duration=20.0),
        ]
        agg = compute_aggregate(folds)
        self.assertAlmostEqual(agg["mean_fold_duration_seconds"], 20.0)
        self.assertAlmostEqual(agg["total_duration_seconds"], 60.0)
        self.assertEqual(agg["slowest_fold_index"], 1)
        self.assertAlmostEqual(agg["slowest_fold_duration_seconds"], 30.0)
        self.assertEqual(agg["valid_folds_duration"], 3)

    def test_slowest_uses_fold_index_not_list_position(self):
        """Fold indices may not be contiguous (resume + skip etc.).
        The slowest_fold_index must be the FOLD's index, not its
        position in the list."""
        folds = [
            _fold(2, duration=5.0),
            _fold(4, duration=99.0),  # slowest
            _fold(6, duration=8.0),
        ]
        agg = compute_aggregate(folds)
        self.assertEqual(agg["slowest_fold_index"], 4)

    def test_mixed_some_none_durations_uses_available(self):
        """Resumed folds may have no duration (pre-timing manifest).
        Aggregate should use the available ones and skip None."""
        folds = [
            _fold(0, duration=None),  # resumed, no timing
            _fold(1, duration=20.0),
            _fold(2, duration=40.0),
        ]
        agg = compute_aggregate(folds)
        # Mean over (20, 40), not over (NaN, 20, 40).
        self.assertAlmostEqual(agg["mean_fold_duration_seconds"], 30.0)
        self.assertAlmostEqual(agg["total_duration_seconds"], 60.0)
        self.assertEqual(agg["valid_folds_duration"], 2)
        # Slowest: fold 2 (40s).
        self.assertEqual(agg["slowest_fold_index"], 2)

    def test_all_none_durations_returns_sentinel(self):
        """When NO fold has a duration (all resumed from pre-timing
        manifests, or all constructed by tests), mean/total are NaN
        and slowest_fold_index is -1."""
        folds = [_fold(0, duration=None), _fold(1, duration=None)]
        agg = compute_aggregate(folds)
        self.assertTrue(math.isnan(agg["mean_fold_duration_seconds"]))
        self.assertTrue(math.isnan(agg["total_duration_seconds"]))
        self.assertEqual(agg["slowest_fold_index"], -1)
        self.assertTrue(math.isnan(agg["slowest_fold_duration_seconds"]))
        self.assertEqual(agg["valid_folds_duration"], 0)

    def test_empty_folds_returns_empty_dict(self):
        """Empty fold list still returns ``{}`` — existing contract."""
        self.assertEqual(compute_aggregate([]), {})


# ---------------------------------------------------------------------------
# build_aggregate_report
# ---------------------------------------------------------------------------


class BuildAggregateReportTimingTests(unittest.TestCase):
    def test_per_fold_duration_in_report(self):
        config = WalkForwardConfig(output_dir="/tmp/wf_timing_test")
        folds = [_fold(0, duration=12.3, started="t0a", finished="t0b")]
        agg = compute_aggregate(folds)
        report = build_aggregate_report(
            config=config, folds=folds, aggregate_metrics=agg,
        )
        self.assertEqual(report["folds"][0]["duration_seconds"], 12.3)
        self.assertEqual(report["folds"][0]["started_at"], "t0a")
        self.assertEqual(report["folds"][0]["finished_at"], "t0b")

    def test_per_fold_none_duration_preserved_as_null(self):
        """Folds without timing data should serialize as ``None``,
        which downstream JSON consumers see as ``null`` (clear
        "not measured" signal vs zero)."""
        config = WalkForwardConfig(output_dir="/tmp/wf_timing_null")
        folds = [_fold(0, duration=None)]
        report = build_aggregate_report(
            config=config, folds=folds, aggregate_metrics={},
        )
        self.assertIsNone(report["folds"][0]["duration_seconds"])


# ---------------------------------------------------------------------------
# FoldManifest round-trip carries timing fields
# ---------------------------------------------------------------------------


class FoldManifestTimingRoundTripTests(unittest.TestCase):
    def test_to_dict_from_dict_preserves_timing(self):
        cfg = WalkForwardConfig()
        fold = _fold(3, duration=42.5, started="t0", finished="t1")
        m = FoldManifest.from_fold(
            fold=fold, config=cfg,
            model_path="m.pkl", report_path="r.json",
            predictions_path="p.pkl", positions_path=None,
        )
        round_tripped = FoldManifest.from_dict(m.to_dict())
        self.assertEqual(round_tripped.fold.duration_seconds, 42.5)
        self.assertEqual(round_tripped.fold.started_at, "t0")
        self.assertEqual(round_tripped.fold.finished_at, "t1")

    def test_legacy_manifest_without_timing_loads_with_none(self):
        """Manifests written before this PR don't have timing fields.
        ``from_dict`` must tolerate the missing keys (the dataclass
        defaults fill in ``None``)."""
        cfg = WalkForwardConfig()
        fold = _fold(0)  # duration=None by default
        m = FoldManifest.from_fold(
            fold=fold, config=cfg,
            model_path="m.pkl", report_path="r.json",
            predictions_path="p.pkl", positions_path=None,
        )
        payload = m.to_dict()
        # Simulate a legacy payload by stripping the new fields.
        for k in ("duration_seconds", "started_at", "finished_at"):
            payload["fold"].pop(k, None)
        reborn = FoldManifest.from_dict(payload)
        self.assertIsNone(reborn.fold.duration_seconds)
        self.assertIsNone(reborn.fold.started_at)
        self.assertIsNone(reborn.fold.finished_at)


# ---------------------------------------------------------------------------
# Engine stamps timing on every fold it runs
# ---------------------------------------------------------------------------


class EngineStampsTimingTests(unittest.TestCase):
    """The engine wraps ``_run_single_fold`` with a ``time.perf_counter``
    delta and ISO timestamps, then ``dataclasses.replace``s the
    returned fold to attach the timing data. This applies to both
    successful folds AND NaN-placeholder folds (the engine catches
    exceptions and synthesises a placeholder — knowing "fold 5 ran
    for 8 minutes before OOMing" is useful diagnostic info)."""

    def _stub_fold_returning(self, *, raise_exc=False):
        """Build a ``_run_single_fold`` stub that takes ~0 seconds
        (success) or raises (NaN-placeholder path)."""
        from src.core.walk_forward._types import WalkForwardFold as WFF

        def fake(*, config, fold_index, train_start, train_end,
                valid_start, valid_end, test_start, test_end,
                output_dir, prior_model_paths):  # noqa: ARG001
            if raise_exc:
                raise RuntimeError("synthetic fold failure")
            return WFF(
                fold_index=fold_index,
                train_period=f"{train_start} ~ {train_end}",
                valid_period=f"{valid_start} ~ {valid_end}",
                test_period=f"{test_start} ~ {test_end}",
                ic_1d=0.01, ic_5d=0.02,
                annualized_return=0.05, max_drawdown=-0.05,
                information_ratio=0.5,
                prediction_shape=(100,),
            )

        return fake

    def _run_with_stub(self, fake_run_single_fold):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = WalkForwardConfig(
                overall_start="2024-01-01",
                overall_end="2024-09-30",
                train_months=3, valid_months=1, test_months=1,
                step_months=12,
                output_dir=str(tmp),
            )
            with patch(
                "src.core.walk_forward.engine.is_canonical_qlib_initialized",
                return_value=True,
            ), patch.object(
                WalkForwardEngine, "_run_single_fold",
                side_effect=fake_run_single_fold,
            ), patch(
                "src.core.walk_forward.engine.compute_aggregate",
                return_value={},
            ), patch(
                "src.core.walk_forward.engine.write_aggregate_report",
            ):
                return WalkForwardEngine.run(config)

    def test_successful_fold_gets_duration_stamp(self):
        result = self._run_with_stub(self._stub_fold_returning())
        self.assertGreater(len(result.folds), 0)
        for fold in result.folds:
            self.assertIsNotNone(fold.duration_seconds)
            assert fold.duration_seconds is not None  # narrowing
            self.assertGreaterEqual(fold.duration_seconds, 0.0)
            self.assertIsNotNone(fold.started_at)
            self.assertIsNotNone(fold.finished_at)

    def test_failed_fold_still_gets_duration_stamp(self):
        """NaN-placeholder folds (engine caught an exception) MUST
        still carry timing — knowing the fold ran for 8 min before
        failing is critical diagnostic info."""
        result = self._run_with_stub(
            self._stub_fold_returning(raise_exc=True),
        )
        self.assertGreater(len(result.folds), 0)
        for fold in result.folds:
            # NaN placeholder shape
            self.assertEqual(fold.prediction_shape, (0,))
            # But timing is still stamped.
            self.assertIsNotNone(fold.duration_seconds)
            self.assertIsNotNone(fold.started_at)
            self.assertIsNotNone(fold.finished_at)


if __name__ == "__main__":
    unittest.main()
