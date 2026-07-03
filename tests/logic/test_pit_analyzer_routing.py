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
            result, reason = WalkForwardEngine._run_attribution_for_fold(
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
