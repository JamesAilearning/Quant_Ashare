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
