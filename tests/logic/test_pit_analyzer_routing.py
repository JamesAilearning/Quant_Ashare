"""Tests for the PIT-provider routing into PerformanceAttribution (audit P2,
add-pit-analyzer-routing PR-1).

Pure synthetic / mock — no qlib bundle. Covers: the provider's new
data_adjust_mode/region parameterization (the label-clash wall), the
attribution opt-in path vs the bit-identical WARN fallback, the alignment
guard, the wiring helper's three states, and the ACTIVATION test (operator
review point 1): a configured provider reaches the attribution call site
through the walk-forward engine — no dead code awaiting PR-2.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

import src.core.qlib_runtime as qlib_runtime_mod
from src.core.canonical_backtest_contract import ADJUST_MODE_POST, ADJUST_MODE_PRE
from src.core.performance_attribution import (
    AttributionConfig,
    PerformanceAttribution,
    PerformanceAttributionError,
)
from src.core.pit_wiring import build_pit_provider
from src.pit.query import PITDataProvider, PITDataProviderError


def _write_registry(root: Path) -> Path:
    reg = root / "delisted_registry.parquet"
    pd.DataFrame(
        {"ticker": ["SH600068"], "delist_date": ["2021-09-13"]}
    ).to_parquet(reg)
    return reg


def _fake_qlib_dir(root: Path) -> Path:
    (root / "calendars").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text("2024-01-02\n")
    return root


class ProviderAdjustModeTests(unittest.TestCase):
    """data_adjust_mode/region parameterization — the label-clash fix."""

    def test_invalid_adjust_mode_refused_before_anything_else(self) -> None:
        with self.assertRaises(PITDataProviderError):
            PITDataProvider(
                provider_uri="X:/nonexistent",
                delisted_registry_path="X:/nonexistent.parquet",
                data_adjust_mode="sideways",
            )

    def test_default_stays_post_and_pre_is_passable(self) -> None:
        # default None -> ADJUST_MODE_POST (every existing caller unchanged);
        # a caller in a pre_adjusted runtime passes its own label.
        with TemporaryDirectory() as td:
            uri = _fake_qlib_dir(Path(td) / "bundle")
            reg = _write_registry(Path(td))
            captured: list = []
            with patch.object(
                qlib_runtime_mod, "init_qlib_canonical",
                side_effect=lambda cfg: captured.append(cfg),
            ):
                PITDataProvider(provider_uri=uri, delisted_registry_path=reg)
                PITDataProvider(
                    provider_uri=uri, delisted_registry_path=reg,
                    data_adjust_mode=ADJUST_MODE_PRE, region="cn",
                )
        self.assertEqual(captured[0].data_adjust_mode, ADJUST_MODE_POST)
        self.assertEqual(captured[1].data_adjust_mode, ADJUST_MODE_PRE)

    def test_missing_registry_fails_loud(self) -> None:
        with self.assertRaises(PITDataProviderError) as cm:
            PITDataProvider(
                provider_uri="X:/nonexistent",
                delisted_registry_path="X:/nonexistent.parquet",
            )
        self.assertIn("Missing delisted registry", str(cm.exception))


class WiringHelperTests(unittest.TestCase):
    """build_pit_provider — the ONE shared engine wiring entry (three states)."""

    def test_empty_path_returns_none(self) -> None:
        self.assertIsNone(build_pit_provider(
            delisted_registry_path="", provider_uri="X:/b",
            data_adjust_mode=ADJUST_MODE_PRE,
        ))
        self.assertIsNone(build_pit_provider(
            delisted_registry_path="   ", provider_uri="X:/b",
            data_adjust_mode=ADJUST_MODE_PRE,
        ))

    def test_nonempty_constructs_with_callers_labels(self) -> None:
        import src.pit.query as query_mod

        fake_cls = MagicMock(return_value="THE_PROVIDER")
        with patch.object(query_mod, "PITDataProvider", fake_cls):
            out = build_pit_provider(
                delisted_registry_path="X:/reg.parquet", provider_uri="X:/b",
                data_adjust_mode=ADJUST_MODE_PRE, region="cn",
            )
        self.assertEqual(out, "THE_PROVIDER")
        kwargs = fake_cls.call_args.kwargs
        self.assertEqual(kwargs["data_adjust_mode"], ADJUST_MODE_PRE)
        self.assertEqual(kwargs["delisted_registry_path"], "X:/reg.parquet")

    def test_provider_uri_is_normalized_before_construction(self) -> None:
        # codex P2 #320 round 2: a "~" path that canonical init accepts must
        # not fail the provider's pre-expansion calendars check — the helper
        # normalizes for EVERY caller.
        import src.pit.query as query_mod

        fake_cls = MagicMock(return_value="P")
        with patch.object(query_mod, "PITDataProvider", fake_cls):
            build_pit_provider(
                delisted_registry_path="X:/reg.parquet",
                provider_uri="~/some_bundle",
                data_adjust_mode=ADJUST_MODE_PRE,
            )
        passed_uri = fake_cls.call_args.kwargs["provider_uri"]
        self.assertNotIn("~", str(passed_uri))

    def test_missing_registry_propagates_fail_loud(self) -> None:
        # never a silent fall-through to the WARN path (spec)
        with self.assertRaises(PITDataProviderError):
            build_pit_provider(
                delisted_registry_path="X:/nonexistent.parquet",
                provider_uri="X:/b", data_adjust_mode=ADJUST_MODE_PRE,
            )


def _close_panel() -> pd.DataFrame:
    idx = pd.MultiIndex.from_product(
        [["SH600000", "SH600001"],
         pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])],
        names=["instrument", "datetime"],
    )
    return pd.DataFrame({"$close": [10.0, 10.5, 11.0, 20.0, 19.0, 18.0]}, index=idx)


class InstrumentReturnsRoutingTests(unittest.TestCase):
    def test_provider_path_routes_and_matches_expected_returns(self) -> None:
        provider = MagicMock()
        provider.get_features.return_value = _close_panel()
        cfg = AttributionConfig(start_date="2024-01-02", end_date="2024-01-04")
        fake_qlib_data = MagicMock()
        with patch.dict(sys.modules, {"qlib.data": fake_qlib_data}):
            out = PerformanceAttribution._get_instrument_returns(
                ["SH600000", "SH600001"], cfg, pit_provider=provider,
            )
        provider.get_features.assert_called_once()
        fake_qlib_data.D.features.assert_not_called()
        self.assertAlmostEqual(out["SH600000"], 11.0 / 10.0 - 1)
        self.assertAlmostEqual(out["SH600001"], 18.0 / 20.0 - 1)

    def test_fallback_warns_and_uses_d_features(self) -> None:
        cfg = AttributionConfig(start_date="2024-01-02", end_date="2024-01-04")
        fake_D = MagicMock()
        fake_D.features.return_value = _close_panel()
        with patch.dict(sys.modules, {"qlib.data": MagicMock(D=fake_D)}):
            with self.assertLogs(
                "src.core.performance_attribution", level="WARNING",
            ) as logs:
                out = PerformanceAttribution._get_instrument_returns(
                    ["SH600000", "SH600001"], cfg,
                )
        fake_D.features.assert_called_once()
        self.assertTrue(any("bypasses" in m for m in logs.output))
        self.assertAlmostEqual(out["SH600000"], 11.0 / 10.0 - 1)

    def test_provider_and_fallback_agree_on_same_panel(self) -> None:
        # same input panel -> identical outputs (the opt-in changes the SOURCE,
        # not the arithmetic)
        cfg = AttributionConfig(start_date="2024-01-02", end_date="2024-01-04")
        provider = MagicMock()
        provider.get_features.return_value = _close_panel()
        fake_D = MagicMock()
        fake_D.features.return_value = _close_panel()
        with patch.dict(sys.modules, {"qlib.data": MagicMock(D=fake_D)}):
            via_provider = PerformanceAttribution._get_instrument_returns(
                ["SH600000", "SH600001"], cfg, pit_provider=provider,
            )
            via_fallback = PerformanceAttribution._get_instrument_returns(
                ["SH600000", "SH600001"], cfg,
            )
        self.assertTrue(via_provider.equals(via_fallback))


class AlignmentGuardTests(unittest.TestCase):
    def test_no_canonical_config_refused(self) -> None:
        with patch.object(
            qlib_runtime_mod, "get_canonical_qlib_config", return_value=None,
        ):
            with self.assertRaises(PerformanceAttributionError):
                PerformanceAttribution._validate_pit_provider_alignment(
                    SimpleNamespace(_provider_uri="X:/b"),
                )

    def test_uri_mismatch_refused(self) -> None:
        canonical = SimpleNamespace(
            provider_uri=qlib_runtime_mod._normalize_provider_uri("X:/right"),
        )
        with patch.object(
            qlib_runtime_mod, "get_canonical_qlib_config", return_value=canonical,
        ):
            with self.assertRaises(PerformanceAttributionError) as cm:
                PerformanceAttribution._validate_pit_provider_alignment(
                    SimpleNamespace(_provider_uri="X:/wrong"),
                )
        self.assertIn("mismatch", str(cm.exception))

    def test_matching_uri_passes(self) -> None:
        canonical = SimpleNamespace(
            provider_uri=qlib_runtime_mod._normalize_provider_uri("X:/right"),
        )
        with patch.object(
            qlib_runtime_mod, "get_canonical_qlib_config", return_value=canonical,
        ):
            PerformanceAttribution._validate_pit_provider_alignment(
                SimpleNamespace(_provider_uri="X:/right"),
            )


class RegistryFingerprintTests(unittest.TestCase):
    """codex P2 on #320: the registry CONTENT (not just its path) folds into
    the resume fingerprint — a registry regenerated in place must invalidate
    resume, mirroring the namechange_path handling."""

    def _fp(self, registry_path: str = "") -> str:
        from src.core.walk_forward._resume import compute_config_fingerprint
        from src.core.walk_forward.config import WalkForwardConfig

        return compute_config_fingerprint(
            WalkForwardConfig(delisted_registry_path=registry_path),
        )

    def test_in_place_content_change_changes_fingerprint(self) -> None:
        with TemporaryDirectory() as td:
            reg = Path(td) / "registry.parquet"
            pd.DataFrame(
                {"ticker": ["SH600068"], "delist_date": ["2021-09-13"]}
            ).to_parquet(reg)
            fp_a = self._fp(str(reg))
            fp_a_again = self._fp(str(reg))
            pd.DataFrame(
                {"ticker": ["SH600068", "SZ002411"],
                 "delist_date": ["2021-09-13", "2023-07-12"]}
            ).to_parquet(reg)  # regenerated IN PLACE, same path
            fp_b = self._fp(str(reg))
        self.assertEqual(fp_a, fp_a_again)  # deterministic on same content
        self.assertNotEqual(fp_a, fp_b)    # content change invalidates resume

    def test_empty_path_adds_no_key_and_missing_file_is_deterministic(self) -> None:
        # empty default must NOT change pre-existing fingerprints (adoption is
        # free on opted-out configs); a configured-but-missing file still
        # yields a deterministic fingerprint (MISSING sentinel, same as
        # namechange_path).
        self.assertEqual(self._fp(""), self._fp(""))
        fp_missing = self._fp("X:/nonexistent/registry.parquet")
        self.assertEqual(fp_missing, self._fp("X:/nonexistent/registry.parquet"))
        self.assertNotEqual(self._fp(""), fp_missing)


def _signal_close_panel() -> pd.DataFrame:
    # (instrument, datetime) $close panel — the shape both D.features and
    # PITDataProvider.get_features return before the analyzer's swaplevel.
    idx = pd.MultiIndex.from_product(
        [["SH600000", "SH600001"],
         pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04",
                         "2024-01-05", "2024-01-08"])],
        names=["instrument", "datetime"],
    )
    vals = [10.0, 10.5, 11.0, 11.2, 11.5, 20.0, 19.0, 18.0, 18.5, 18.2]
    return pd.DataFrame({"$close": vals}, index=idx)


class SignalFetchReturnsRoutingTests(unittest.TestCase):
    """PR-2: SignalAnalyzer._fetch_returns routes through the provider; the
    None path stays bit-identical with the WARN."""

    def _preds(self) -> pd.Series:
        idx = pd.MultiIndex.from_product(
            [pd.to_datetime(["2024-01-02", "2024-01-03"]),
             ["SH600000", "SH600001"]],
            names=["datetime", "instrument"],
        )
        return pd.Series([0.1, 0.2, 0.3, 0.4], index=idx, name="score")

    def test_provider_path_routes_and_matches_fallback_shape(self) -> None:
        from src.core.signal_analyzer import SignalAnalyzer

        provider = MagicMock()
        provider.get_features.return_value = _signal_close_panel()
        fake_qlib_data = MagicMock()
        with patch.dict(sys.modules, {"qlib.data": fake_qlib_data}):
            via_provider = SignalAnalyzer._fetch_returns(
                self._preds(), 2, pit_provider=provider,
            )
        provider.get_features.assert_called_once()
        fake_qlib_data.D.features.assert_not_called()

        fake_D = MagicMock()
        fake_D.features.return_value = _signal_close_panel()
        with patch.dict(sys.modules, {"qlib.data": MagicMock(D=fake_D)}):
            via_fallback = SignalAnalyzer._fetch_returns(self._preds(), 2)
        # same input panel -> identical output (the opt-in changes the SOURCE,
        # not the reshaping arithmetic)
        pd.testing.assert_frame_equal(via_provider, via_fallback)
        self.assertEqual(list(via_provider.index.names), ["datetime", "instrument"])
        self.assertEqual(list(via_provider.columns), ["close"])

    def test_fallback_warns(self) -> None:
        from src.core.signal_analyzer import SignalAnalyzer

        fake_D = MagicMock()
        fake_D.features.return_value = _signal_close_panel()
        with patch.dict(sys.modules, {"qlib.data": MagicMock(D=fake_D)}):
            with self.assertLogs("src.core.signal_analyzer", level="WARNING") as logs:
                SignalAnalyzer._fetch_returns(self._preds(), 2)
        self.assertTrue(any("bypasses" in m for m in logs.output))

    def test_provider_path_does_not_warn(self) -> None:
        import logging

        from src.core.signal_analyzer import SignalAnalyzer

        provider = MagicMock()
        provider.get_features.return_value = _signal_close_panel()
        records: list = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        logger = logging.getLogger("src.core.signal_analyzer")
        logger.addHandler(handler)
        try:
            SignalAnalyzer._fetch_returns(self._preds(), 2, pit_provider=provider)
        finally:
            logger.removeHandler(handler)
        self.assertFalse(any("bypasses" in r.getMessage() for r in records))

    def test_analyze_threads_provider_to_fetch(self) -> None:
        # ACTIVATION (same spirit as PR-1's engine test): analyze passes the
        # provider through to _fetch_returns — with alignment satisfied.
        from src.core.signal_analyzer import SignalAnalysisConfig, SignalAnalyzer

        provider = SimpleNamespace(_provider_uri="X:/right")
        canonical = SimpleNamespace(
            provider_uri=qlib_runtime_mod._normalize_provider_uri("X:/right"),
        )
        captured: dict = {}

        def fake_fetch(predictions, max_period, pit_provider=None):  # noqa: ANN001
            captured["provider"] = pit_provider
            panel = _signal_close_panel().rename(columns={"$close": "close"})
            return panel.swaplevel().sort_index()

        with patch.object(
            qlib_runtime_mod, "get_canonical_qlib_config", return_value=canonical,
        ), patch.dict(
            SignalAnalyzer.analyze.__func__.__globals__,
            {"is_canonical_qlib_initialized": lambda: True},
        ), patch.object(
            SignalAnalyzer, "_fetch_returns", staticmethod(fake_fetch),
        ):
            result = SignalAnalyzer.analyze(
                predictions=self._preds(),
                config=SignalAnalysisConfig(
                    forward_periods=(1,), compute_turnover=False,
                ),
                pit_provider=provider,
            )
        self.assertIs(captured["provider"], provider)
        self.assertIn(1, result.ic_summary)


class SignalAlignmentGuardTests(unittest.TestCase):
    def test_no_canonical_config_refused(self) -> None:
        from src.core.signal_analyzer import SignalAnalyzer, SignalAnalyzerError

        with patch.object(
            qlib_runtime_mod, "get_canonical_qlib_config", return_value=None,
        ):
            with self.assertRaises(SignalAnalyzerError):
                SignalAnalyzer._validate_pit_provider_alignment(
                    SimpleNamespace(_provider_uri="X:/b"),
                )

    def test_uri_mismatch_refused_and_match_passes(self) -> None:
        from src.core.signal_analyzer import SignalAnalyzer, SignalAnalyzerError

        canonical = SimpleNamespace(
            provider_uri=qlib_runtime_mod._normalize_provider_uri("X:/right"),
        )
        with patch.object(
            qlib_runtime_mod, "get_canonical_qlib_config", return_value=canonical,
        ):
            with self.assertRaises(SignalAnalyzerError):
                SignalAnalyzer._validate_pit_provider_alignment(
                    SimpleNamespace(_provider_uri="X:/wrong"),
                )
            SignalAnalyzer._validate_pit_provider_alignment(
                SimpleNamespace(_provider_uri="X:/right"),
            )


class ReportRecordsRegistryTests(unittest.TestCase):
    """codex P2 #320 r3: pipeline_report.json must expose the PIT-routing
    status (walk-forward reports carry the full config via asdict) — an
    operator reading the primary report can tell mask vs legacy path."""

    def _report(self, registry: str) -> dict:
        import json
        import tempfile

        from src.core.pipeline import Pipeline
        from src.core.signal_analyzer import SignalAnalysisResult

        config = SimpleNamespace(
            instruments="csi300", feature_handler="Alpha158",
            label_horizon_days=1, delisted_registry_path=registry,
            train_start="2022-01-01", train_end="2022-12-31",
            valid_start="2023-01-01", valid_end="2023-03-31",
            test_start="2023-04-01", test_end="2023-06-30",
            model_type="LGBModel", benchmark_code="SH000300",
            topk=50, n_drop=5, industry_taxonomy_id=None,
        )
        signal_result = SignalAnalysisResult(
            ic_summary={1: {"mean_ic": 0.01, "std_ic": 0.02, "ir": 0.5, "num_days": 5}},
            ic_series={}, ic_decay=[0.01], turnover_stats={"mean_turnover": 0.1},
        )
        backtest_output = SimpleNamespace(
            metric_status="ok", official_backtest_path="official",
            report={}, provenance={}, risk_analysis={},
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pipeline_report.json"
            Pipeline._write_report(
                str(path), config,  # type: ignore[arg-type]
                SimpleNamespace(train_shape=(1, 1), valid_shape=(1, 1),
                                test_shape=(1, 1)),  # type: ignore[arg-type]
                SimpleNamespace(prediction_shape=(1, 1),
                                model_artifact_path="m"),  # type: ignore[arg-type]
                signal_result, backtest_output,  # type: ignore[arg-type]
                factor_skipped_reason="unit-test",
            )
            return dict(json.loads(path.read_text(encoding="utf-8")))

    def test_configured_registry_recorded(self) -> None:
        data = self._report("D:/qlib_data/tushare_raw/delisted_registry.parquet")
        self.assertEqual(
            data["config"]["delisted_registry_path"],
            "D:/qlib_data/tushare_raw/delisted_registry.parquet",
        )

    def test_legacy_path_recorded_as_null(self) -> None:
        data = self._report("")
        self.assertIsNone(data["config"]["delisted_registry_path"])


class EngineActivationTests(unittest.TestCase):
    """Operator review point 1: the wiring is EXERCISED in PR-1 — a configured
    provider reaches the attribution call site through the engine, not dead
    code awaiting PR-2."""

    def test_engine_threads_provider_to_attribution_call(self) -> None:
        from src.core.walk_forward.config import WalkForwardConfig
        from src.core.walk_forward.engine import WalkForwardEngine

        config = WalkForwardConfig(run_attribution=True)
        provider = MagicMock(name="the_pit_provider")
        backtest_output = SimpleNamespace(
            return_series={"return": {"2024-01-02": 0.01},
                           "bench": {"2024-01-02": 0.005},
                           "cost": {"2024-01-02": 0.0}},
            positions={"2024-01-02": {"SH600000": 1.0}},
        )
        sentinel = object()
        with patch.object(
            PerformanceAttribution, "analyze", MagicMock(return_value=sentinel),
        ) as fake_analyze:
            result, reason, _sleeve_to = WalkForwardEngine._run_attribution_for_fold(
                config=config,
                fold_index=0,
                test_start="2024-01-02", test_end="2024-03-29",
                predictions=MagicMock(),
                backtest_output=backtest_output,  # type: ignore[arg-type]
                pit_provider=provider,
            )
        self.assertIs(result, sentinel)
        self.assertIsNone(reason)
        self.assertIs(fake_analyze.call_args.kwargs["pit_provider"], provider)

    def test_engine_none_provider_stays_none(self) -> None:
        from src.core.walk_forward.config import WalkForwardConfig
        from src.core.walk_forward.engine import WalkForwardEngine

        config = WalkForwardConfig(run_attribution=True)
        backtest_output = SimpleNamespace(
            return_series={"return": {"2024-01-02": 0.01},
                           "bench": {"2024-01-02": 0.005},
                           "cost": {"2024-01-02": 0.0}},
            positions={"2024-01-02": {"SH600000": 1.0}},
        )
        with patch.object(
            PerformanceAttribution, "analyze", MagicMock(return_value=object()),
        ) as fake_analyze:
            WalkForwardEngine._run_attribution_for_fold(
                config=config,
                fold_index=0,
                test_start="2024-01-02", test_end="2024-03-29",
                predictions=MagicMock(),
                backtest_output=backtest_output,  # type: ignore[arg-type]
            )
        self.assertIsNone(fake_analyze.call_args.kwargs["pit_provider"])


if __name__ == "__main__":
    unittest.main()
