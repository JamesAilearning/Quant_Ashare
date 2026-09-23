"""Direct historical measurement must not bypass suspension quarantine."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("qlib")

from src.contracts.suspension_quarantine import POLICY_IDS, SuspensionQuarantine, incident_for_policy
from src.core.backtest_runner import BacktestRunner, BacktestRunnerError
from src.core.canonical_backtest_contract import (
    ADJUST_MODE_PRE,
    CN_STAMP_TAX_SCHEDULE_DEFAULT,
    EXECUTION_PRICE_CLOSE,
    CanonicalAccountConfig,
    CanonicalBacktestInput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
)
from src.data.pit.bundle_integrity import write_bundle_integrity
from src.data.tushare.fetch_types import FetchHole


@pytest.fixture(params=POLICY_IDS)
def quarantine_policy(request):
    return request.param


def _quarantined_provider(tmp_path, policy):
    provider = tmp_path / "provider"
    evidence = SuspensionQuarantine(
        policy_id=policy,
        missing_dates=(min(incident_for_policy(policy).dates),),
        reference_sha256="a" * 64,
        retained_sha256="b" * 64,
        candidate_sha256="c" * 64,
        query_start_date="20151001",
        query_end_date="20260922",
    )
    hole = FetchHole(
        endpoint="suspend_d", unit="file", reason_class="quarantined_history",
        attempts=1, last_error="synthetic reviewed incident", quarantine=evidence,
    )
    write_bundle_integrity(provider, built_from_holey_fetch=True, holes=(hole,))
    return provider


def _request():
    return CanonicalBacktestInput(
        predictions_ref="synthetic",
        evaluation_start="2025-10-01",
        evaluation_end="2025-12-31",
        account_config=CanonicalAccountConfig(init_cash=100_000_000),
        exchange_config=CanonicalExchangeConfig(
            freq="day", execution_price_kind=EXECUTION_PRICE_CLOSE,
            cost_model=CanonicalExchangeCostModel(
                commission_rate=0.0005,
                stamp_tax_schedule=CN_STAMP_TAX_SCHEDULE_DEFAULT,
                slippage_bps=5.0, min_cost=5.0,
            ),
        ),
        adjust_mode=ADJUST_MODE_PRE,
        signal_to_execution_lag=1,
        benchmark_code="SH000300TR",
    )


def _gate_args(scope, provider, output):
    args = ["--scope", scope, "--provider", str(provider), "--out", str(output)]
    if scope == "member":
        return args + [
            "--member-pkl", "must-not-load.pkl", "--member-meta", "must-not-load.json",
            "--fit-start", "2018-01-02", "--fit-end", "2024-12-18",
            "--valid-start", "2025-01-02", "--valid-end", "2025-06-26",
        ]
    return args + [
        "--manifest", "must-not-load.json",
        "--window-start", "2025-10-01", "--window-end", "2025-12-31",
    ]


@pytest.mark.parametrize("metrics_purpose", ["official", "predictions_only"])
@pytest.mark.parametrize("padding", ["", "  "])
def test_initialized_backtest_refuses_quarantine_before_qlib_reads(
    tmp_path, monkeypatch, metrics_purpose, padding, quarantine_policy,
):
    from src.core import backtest_runner

    provider = _quarantined_provider(tmp_path, quarantine_policy)
    monkeypatch.setattr(backtest_runner, "is_canonical_qlib_initialized", lambda: True)
    monkeypatch.setattr(
        backtest_runner, "get_canonical_qlib_config",
        lambda: SimpleNamespace(
            provider_uri=f"{padding}{provider}{padding}", data_adjust_mode=ADJUST_MODE_PRE,
        ),
    )
    calendar = Mock(side_effect=AssertionError("must not read the execution calendar"))
    monkeypatch.setattr(BacktestRunner, "_load_execution_calendar", calendar)
    with pytest.raises(BacktestRunnerError, match="quarantin"):
        BacktestRunner.run(
            request=_request(), predictions="synthetic", metrics_purpose=metrics_purpose,
            # Captured identities must not bypass current provider integrity.
            bundle_identity="previous-clean-bundle", bundle_build_identity="old-build",
        )
    calendar.assert_not_called()


@pytest.mark.parametrize("padding", ["", "  "])
def test_frozen_evaluation_refuses_quarantine_before_model_or_output(tmp_path, monkeypatch, padding, quarantine_policy):
    from scripts import eval_frozen_model_oos

    provider = _quarantined_provider(tmp_path, quarantine_policy)
    output = tmp_path / "must-not-create" / "eval.json"
    heavy = Mock(side_effect=AssertionError("must not initialize qlib or load a model"))
    monkeypatch.setattr(eval_frozen_model_oos, "_predictions_over_window", heavy)
    with pytest.raises(SystemExit, match="quarantin"):
        eval_frozen_model_oos.main([
            "--provider", f"{padding}{provider}{padding}",
            "--model", "must-not-load.pkl", "--out", str(output),
        ])
    heavy.assert_not_called()
    assert not output.parent.exists()


@pytest.mark.parametrize("scope", ["member", "ensemble"])
@pytest.mark.parametrize("padding", ["", "  "])
def test_retrain_gate_refuses_quarantine_before_measurement_or_output(
    tmp_path, monkeypatch, scope, padding, quarantine_policy,
):
    from scripts import retrain_gate

    provider = _quarantined_provider(tmp_path, quarantine_policy)
    output = tmp_path / "must-not-create" / "gate.json"
    member = Mock(side_effect=AssertionError("must not load member or measure IC"))
    ensemble = Mock(side_effect=AssertionError("must not load ensemble or backtest"))
    monkeypatch.setattr(retrain_gate, "_member_scope", member)
    monkeypatch.setattr(retrain_gate, "_ensemble_scope", ensemble)
    with pytest.raises(SystemExit, match="quarantin"):
        retrain_gate.main(_gate_args(scope, f"{padding}{provider}{padding}", output))
    member.assert_not_called()
    ensemble.assert_not_called()
    assert not output.parent.exists()


@pytest.mark.parametrize("scope", ["member", "ensemble"])
def test_quarantined_retrain_cli_reports_tool_error_not_gate_failure(
    tmp_path, monkeypatch, capsys, scope, quarantine_policy,
):
    from scripts import retrain_gate

    provider = _quarantined_provider(tmp_path, quarantine_policy)
    output = tmp_path / "must-not-create" / "gate.json"
    heavy = Mock(side_effect=AssertionError("must not measure quarantine"))
    monkeypatch.setattr(retrain_gate, f"_{scope}_scope", heavy)
    monkeypatch.setattr("sys.argv", ["retrain_gate.py", *_gate_args(scope, provider, output)])
    assert retrain_gate._cli() == 2
    assert "quarantin" in capsys.readouterr().err
    heavy.assert_not_called()
    assert not output.parent.exists()


@pytest.mark.parametrize("stamp", ["legacy_missing", "clean", "ordinary_hole"])
@pytest.mark.parametrize("entrypoint", ["frozen", "member", "ensemble"])
def test_historical_cli_keeps_nonquarantine_integrity_behavior(tmp_path, monkeypatch, stamp, entrypoint):
    from scripts import eval_frozen_model_oos, retrain_gate

    provider = tmp_path / "provider"
    if stamp != "legacy_missing":
        holes = () if stamp == "clean" else (
            FetchHole("daily", "000001.SZ/2025", "transport", 1, "synthetic ordinary hole"),
        )
        write_bundle_integrity(provider, built_from_holey_fetch=bool(holes), holes=holes)
    output = tmp_path / "must-not-create" / "report.json"
    heavy = Mock(side_effect=AssertionError("existing validation reached"))
    if entrypoint == "frozen":
        monkeypatch.setattr(eval_frozen_model_oos, "_predictions_over_window", heavy)
        main = eval_frozen_model_oos.main
        args = [
            "--provider", str(provider), "--model", "unused.pkl", "--out", str(output),
        ]
    else:
        monkeypatch.setattr(retrain_gate, f"_{entrypoint}_scope", heavy)
        main = retrain_gate.main
        args = _gate_args(entrypoint, provider, output)
    with pytest.raises(AssertionError, match="existing validation reached"):
        main(args)
    heavy.assert_called_once()
    assert not output.parent.exists()
