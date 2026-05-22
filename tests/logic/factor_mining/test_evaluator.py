"""Tests for the factor evaluator (walker + IC/IR/RankIC/turnover/coverage)."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from src.factor_mining.evaluator import (
    EvaluationResult,
    evaluate_expression,
    evaluate_factor,
)
from src.factor_mining.expression import OperatorCall, Terminal, parse_expression


def _make_panel(tickers, dates, seed=0):
    rng = np.random.default_rng(seed)
    fields = ["$open", "$high", "$low", "$close", "$volume", "$money"]
    out = {}
    for f in fields:
        data = rng.normal(100, 5, size=(len(dates), len(tickers)))
        df = pd.DataFrame(data, index=pd.Index(dates, name="datetime"), columns=pd.Index(tickers, name="instrument"))
        out[f] = df
    return out


# ---------------------------------------------------------------------------
# evaluate_expression — recursive walker
# ---------------------------------------------------------------------------


def test_evaluate_expression_terminal_window_returns_int():
    panel = _make_panel(["A", "B"], pd.date_range("2024-01-01", periods=3))
    result = evaluate_expression(Terminal("20"), panel)
    assert result == 20
    assert isinstance(result, int)


def test_evaluate_expression_terminal_feature_returns_dataframe():
    panel = _make_panel(["A", "B"], pd.date_range("2024-01-01", periods=3))
    result = evaluate_expression(Terminal("$close"), panel)
    assert isinstance(result, pd.DataFrame)
    assert result.equals(panel["$close"])


def test_evaluate_expression_missing_feature_raises():
    import pytest

    panel = _make_panel(["A"], pd.date_range("2024-01-01", periods=3))
    with pytest.raises(KeyError, match="not present"):
        evaluate_expression(Terminal("$open"), {k: v for k, v in panel.items() if k != "$open"})


def test_evaluate_expression_arithmetic():
    tickers = ["A", "B"]
    dates = pd.date_range("2024-01-01", periods=4)
    panel = _make_panel(tickers, dates)
    expr = OperatorCall("add", (Terminal("$volume"), Terminal("$money")))
    result = evaluate_expression(expr, panel)
    expected = panel["$volume"] + panel["$money"]
    pd.testing.assert_frame_equal(result, expected)


def test_evaluate_expression_ts_mean():
    tickers = ["A", "B"]
    dates = pd.date_range("2024-01-01", periods=10)
    panel = _make_panel(tickers, dates)
    expr = OperatorCall("ts_mean", (Terminal("$close"), Terminal("5")))
    result = evaluate_expression(expr, panel)
    expected = panel["$close"].rolling(5, min_periods=5).mean()
    pd.testing.assert_frame_equal(result, expected)


def test_evaluate_expression_cs_rank():
    tickers = ["A", "B", "C", "D"]
    dates = pd.date_range("2024-01-01", periods=3)
    panel = _make_panel(tickers, dates)
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    result = evaluate_expression(expr, panel)
    # Each row's ranks fall in [-0.5, 0.5]
    assert ((result >= -0.5) & (result <= 0.5)).values.all()


def test_evaluate_expression_nested_smoke_factor():
    tickers = ["A", "B", "C", "D"]
    dates = pd.date_range("2024-01-01", periods=30)
    panel = _make_panel(tickers, dates)
    expr = parse_expression("cs_rank(div_safe(ts_delta($close, 20), $close))")
    result = evaluate_expression(expr, panel)
    assert isinstance(result, pd.DataFrame)
    assert result.shape == (30, 4)


# ---------------------------------------------------------------------------
# evaluate_factor — produces EvaluationResult with metric bundle
# ---------------------------------------------------------------------------


def test_evaluate_factor_perfect_correlation_yields_ic_near_one():
    tickers = list("ABCDE")
    dates = pd.date_range("2024-01-01", periods=20)
    # Construct a forward-return panel with monotone structure per day,
    # then construct a "factor" that is exactly the same panel — perfect
    # rank correlation per day.
    rng = np.random.default_rng(11)
    fwd = pd.DataFrame(
        rng.normal(0, 0.02, size=(20, 5)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    # Build a panel where $volume == fwd; then expr = $volume gives factor = fwd.
    panel = _make_panel(tickers, dates)
    panel["$volume"] = fwd
    # cs_rank($volume) is PURE-typed and routes through the gate
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    result = evaluate_factor(expr, panel, forward_return=fwd, method="rank")
    # cs_rank produces a monotone transform of fwd, so Spearman corr per day = 1
    assert result.rank_ic_mean == pytest.approx(1.0, abs=1e-9)


def test_evaluate_factor_ir_nan_on_zero_std_ic():
    """When the per-day IC is identically constant, IR must be NaN
    (per inventory.md §B.4 convention)."""
    tickers = list("ABCD")
    dates = pd.date_range("2024-01-01", periods=10)
    # Construct a factor and fwd such that the IC per day is identical
    # across dates. Easiest: factor=fwd → IC=1.0 every day → std=0.
    rng = np.random.default_rng(7)
    fwd = pd.DataFrame(
        rng.normal(0, 0.02, size=(10, 4)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    panel = _make_panel(tickers, dates)
    panel["$volume"] = fwd
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    result = evaluate_factor(expr, panel, fwd, method="rank")
    assert np.isnan(result.rank_ir)


def test_evaluate_factor_turnover_static_factor_is_zero():
    tickers = list("ABCD")
    dates = pd.date_range("2024-01-01", periods=10)
    panel = _make_panel(tickers, dates)
    # $volume identical every date → cs_rank also identical → turnover 0
    panel["$volume"] = pd.DataFrame(
        np.tile([1.0, 2.0, 3.0, 4.0], (10, 1)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    rng = np.random.default_rng(13)
    fwd = pd.DataFrame(
        rng.normal(0, 0.02, size=(10, 4)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    result = evaluate_factor(expr, panel, fwd, method="rank")
    assert result.turnover_daily == pytest.approx(0.0, abs=1e-12)


def test_evaluate_factor_coverage_excludes_nan_cells():
    tickers = list("ABC")
    dates = pd.date_range("2024-01-01", periods=10)
    panel = _make_panel(tickers, dates)
    # Inject NaN into the panel so factor has NaN cells
    panel["$volume"].iloc[0, 0] = np.nan
    panel["$volume"].iloc[1, 1] = np.nan
    rng = np.random.default_rng(21)
    fwd = pd.DataFrame(
        rng.normal(0, 0.02, size=(10, 3)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    result = evaluate_factor(expr, panel, fwd, method="rank")
    # 2 NaN cells out of 30 → coverage = 28/30
    assert result.coverage == pytest.approx(28 / 30, abs=1e-9)


def test_evaluate_factor_handles_empty_panel_gracefully():
    """Empty inputs should return finite-shaped (mostly-NaN) result, not raise."""
    tickers = ["A"]
    dates = pd.date_range("2024-01-01", periods=0)
    panel = _make_panel(tickers, dates)
    fwd = pd.DataFrame(
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    result = evaluate_factor(expr, panel, fwd, method="rank")
    assert isinstance(result, EvaluationResult)
    assert result.coverage == 0.0
    assert result.turnover_daily == 0.0
    assert np.isnan(result.ic_mean) or result.ic_mean == 0.0


# ---------------------------------------------------------------------------
# D5 strict gate
# ---------------------------------------------------------------------------


def test_evaluator_does_not_import_qlib_or_pit_directly():
    import src.factor_mining.evaluator as mod

    src = inspect.getsource(mod)
    assert "from qlib" not in src
    assert "qlib.data" not in src
    assert "qlib.init" not in src
    assert "from src.pit" not in src
    assert "import src.pit" not in src


import pytest  # noqa: E402
