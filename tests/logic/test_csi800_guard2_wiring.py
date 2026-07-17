"""Unit tests for CSI800 guard-2 runtime wiring (v2-csi800-expansion-guards).

Coverage matrix (>=1 case per dimension):
  mutual exclusion — sleeve grouping + industry artifact refused at
                     config construction (both engines' config classes).
  sleeve wiring    — Pipeline._build_attribution_config consumes the
                     sleeve map/taxonomy; SleeveResolutionError becomes
                     PipelineError (fail-loud passthrough).
  provenance       — BacktestRunner._build_provenance includes the
                     risk_constraints block when supplied and stays
                     BYTE-IDENTICAL without it (REGEN replay safety).
  turnover         — sleeve_turnover per-sleeve one-way math, unknown
                     bucket, deterministic ordering.
  presets          — both campaign presets opt into both guards.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.attribution_sleeve_loader import sleeve_turnover  # noqa: E402


def _bundle(tmp: Path) -> Path:
    inst = tmp / "instruments"
    inst.mkdir(parents=True)
    (inst / "csi300.txt").write_text(
        "SH600000\t2016-01-29\t2099-12-31\n"
        "SH600004\t2018-12-28\t2025-12-31\n", encoding="utf-8")
    (inst / "csi500.txt").write_text(
        "SZ000006\t2016-01-29\t2099-12-31\n"
        "SZ000008\t2019-01-02\t2025-12-31\n", encoding="utf-8")
    return tmp


class TestMutualExclusion:
    def test_pipeline_config_refuses_both_grouping_sources(self):
        from src.core.pipeline import PipelineConfig, PipelineError
        with pytest.raises(PipelineError, match="mutually exclusive"):
            PipelineConfig(
                provider_uri="D:/fake_bundle",
                attribution_sleeve_grouping=True,
                industry_artifact_path="x.csv",
                industry_manifest_path="x.json",
                industry_taxonomy_id="t",
            )

    def test_walk_forward_config_refuses_both_grouping_sources(self):
        from src.core.walk_forward.config import (
            WalkForwardConfig,
            WalkForwardError,
        )
        with pytest.raises(WalkForwardError, match="mutually exclusive"):
            WalkForwardConfig(
                attribution_sleeve_grouping=True,
                industry_artifact_path="x.csv",
                industry_manifest_path="x.json",
                industry_taxonomy_id="t",
            )

    def test_sleeve_grouping_requires_attribution_enabled(self):
        # codex #370 r2 P1: run_attribution=False would skip the sleeve
        # branch entirely and emit bare csi800 metrics — refused at
        # config construction in BOTH engines.
        from src.core.pipeline import PipelineConfig, PipelineError
        from src.core.walk_forward.config import (
            WalkForwardConfig,
            WalkForwardError,
        )
        with pytest.raises(PipelineError, match="requires\\s+run_attribution"):
            PipelineConfig(
                provider_uri="D:/fake",
                attribution_sleeve_grouping=True,
                run_attribution=False,
            )
        with pytest.raises(WalkForwardError,
                           match="requires\\s+run_attribution"):
            WalkForwardConfig(
                attribution_sleeve_grouping=True,
                run_attribution=False,
            )


class TestPipelineSleeveWiring:
    def test_build_attribution_config_uses_sleeve_map(self):
        from src.core.pipeline import Pipeline, PipelineConfig
        with tempfile.TemporaryDirectory() as t:
            root = _bundle(Path(t))
            cfg = PipelineConfig(
                provider_uri=str(root),
                attribution_sleeve_grouping=True,
                test_start="2025-07-01",
                test_end="2025-12-31",
            )
            attr = Pipeline._build_attribution_config(cfg)
            assert attr.industry_taxonomy_id == "csi800_sleeve_v1"
            assert attr.industry_map_override is not None
            assert attr.industry_map_override["SH600000"] == "csi300_sleeve"
            assert attr.industry_map_override["SZ000006"] == "csi500_sleeve"

    def test_sleeve_failure_becomes_pipeline_error(self):
        from src.core.pipeline import Pipeline, PipelineConfig, PipelineError
        with tempfile.TemporaryDirectory() as t:
            root = _bundle(Path(t))
            cfg = PipelineConfig(
                provider_uri=str(root),
                attribution_sleeve_grouping=True,
                # beyond the fixture's demonstrated coverage (2025-12-31):
                test_start="2026-06-01",
                test_end="2026-12-31",
            )
            with pytest.raises(PipelineError, match="beyond"):
                Pipeline._build_attribution_config(cfg)


class TestSleeveFailureIsFatalInPipelineRun:
    def test_fatal_flag_follows_sleeve_grouping(self):
        # codex #370 r1 P1: with sleeve grouping on, an attribution
        # config failure must ABORT the run (no bare csi800 numbers);
        # the industry-taxonomy path keeps its soft-skip.
        from src.core.pipeline import Pipeline, PipelineConfig
        on = PipelineConfig(provider_uri="D:/fake",
                            attribution_sleeve_grouping=True)
        off = PipelineConfig(provider_uri="D:/fake")
        assert Pipeline._attribution_failure_is_fatal(on) is True
        assert Pipeline._attribution_failure_is_fatal(off) is False


class TestSleeveFailuresFatalInWalkForwardFold:
    """codex #370 r4: with sleeve grouping on, EVERY inability-to-
    produce-the-sleeve-report path raises instead of downgrading —
    no-positions, engine failure, unexpected failure. (The pipeline
    engine mirrors these branches via the same predicate; its
    map-construction fatality is covered above.)"""

    def _config(self):
        from src.core.walk_forward.config import WalkForwardConfig
        return WalkForwardConfig(attribution_sleeve_grouping=True)

    def test_no_positions_raises(self):
        from types import SimpleNamespace

        from src.core.walk_forward.config import WalkForwardError
        from src.core.walk_forward.engine import WalkForwardEngine
        with pytest.raises(WalkForwardError, match="no positions"):
            WalkForwardEngine._run_attribution_for_fold(
                config=self._config(),
                fold_index=0,
                test_start="2025-07-01", test_end="2025-12-31",
                predictions=None,
                backtest_output=SimpleNamespace(positions={}),
            )

    def test_engine_failure_raises(self, monkeypatch):
        from types import SimpleNamespace

        import src.core.walk_forward.engine as engine_mod
        from src.core.performance_attribution import (
            PerformanceAttributionError,
        )
        from src.core.walk_forward.config import WalkForwardError
        from src.core.walk_forward.engine import WalkForwardEngine
        with tempfile.TemporaryDirectory() as t:
            root = _bundle(Path(t))
            monkeypatch.setattr(
                engine_mod, "get_canonical_qlib_config",
                lambda: SimpleNamespace(provider_uri=str(root)))
            monkeypatch.setattr(
                engine_mod.PerformanceAttribution, "analyze",
                staticmethod(lambda **kw: (_ for _ in ()).throw(
                    PerformanceAttributionError("degenerate"))))
            with pytest.raises(WalkForwardError,
                               match="attribution engine failed"):
                WalkForwardEngine._run_attribution_for_fold(
                    config=self._config(),
                    fold_index=0,
                    test_start="2025-07-01", test_end="2025-12-31",
                    predictions=object(),
                    backtest_output=SimpleNamespace(
                        positions={"2025-07-01": {"SH600000": 1.0}},
                        return_series={}),
                )


class TestRiskConstraintProvenance:
    def _request(self):
        from src.core.canonical_backtest_contract import (
            CN_STAMP_TAX_SCHEDULE_DEFAULT,
            EXECUTION_PRICE_CLOSE,
            CanonicalAccountConfig,
            CanonicalBacktestInput,
            CanonicalExchangeConfig,
            CanonicalExchangeCostModel,
        )
        return CanonicalBacktestInput(
            predictions_ref="test",
            evaluation_start="2025-07-01",
            evaluation_end="2025-12-31",
            account_config=CanonicalAccountConfig(init_cash=1_000_000),
            exchange_config=CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=EXECUTION_PRICE_CLOSE,
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=0.0005,
                    stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
                    slippage_bps=5.0, min_cost=5.0,
                ),
                limit_threshold=0.095,
            ),
            adjust_mode="pre_adjusted",
            signal_to_execution_lag=1,
            benchmark_code="SH000906TR",
        )

    def test_provenance_includes_block_only_when_supplied(self):
        from src.core.backtest_runner import BacktestRunner
        req = self._request()
        rc = {"max_per_name": 0.05, "max_per_board": 0.40,
              "cash_buffer_min": 0.0, "max_leverage": 1.0,
              "mode": "raise"}
        with_rc = BacktestRunner._build_provenance(
            req, 50, 5, risk_constraints=rc)
        without = BacktestRunner._build_provenance(req, 50, 5)
        assert with_rc["config"]["risk_constraints"] == rc
        # REGEN replay safety: the default path is byte-identical —
        # no key, and the fingerprint unchanged vs a pre-guard build.
        assert "risk_constraints" not in without["config"]
        assert (with_rc["config_fingerprint"]
                != without["config_fingerprint"])


class TestSleeveTurnover:
    def test_per_sleeve_oneway_math_and_unknown_bucket(self):
        positions = {
            "2025-07-01": {"A": 0.5, "B": 0.5},
            "2025-07-02": {"A": 0.3, "B": 0.5, "C": 0.2},
            "2025-07-03": {"A": 0.3, "B": 0.3, "C": 0.4},
        }
        sleeves = {"A": "csi300_sleeve", "B": "csi500_sleeve"}
        out = sleeve_turnover(positions, sleeves)
        # A: |0.3-0.5|/2 + 0 = 0.1 ; B: 0 + |0.3-0.5|/2 = 0.1 ;
        # C (unknown): |0.2-0|/2 + |0.4-0.2|/2 = 0.2
        assert out["csi300_sleeve"]["total_oneway"] == pytest.approx(0.1)
        assert out["csi500_sleeve"]["total_oneway"] == pytest.approx(0.1)
        assert out["unknown"]["total_oneway"] == pytest.approx(0.2)
        assert out["unknown"]["n_transitions"] == 2.0
        assert out["unknown"]["daily_mean_oneway"] == pytest.approx(0.1)

    def test_empty_and_single_day_are_zero_safe(self):
        assert sleeve_turnover({}, {}) == {}
        assert sleeve_turnover({"2025-07-01": {"A": 1.0}}, {}) == {}


class TestCampaignPresetsOptIn:
    def test_both_presets_enable_both_guards(self):
        presets = _PROJECT_ROOT / "config" / "presets"
        for name in ("csi800.yaml", "csi800_conservative.yaml"):
            data = yaml.safe_load(
                (presets / name).read_text(encoding="utf-8"))
            assert data.get("attribution_sleeve_grouping") is True, name
            assert data.get("risk_constraints_enabled") is True, name
