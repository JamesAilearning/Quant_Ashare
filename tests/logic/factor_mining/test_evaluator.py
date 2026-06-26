"""Tests for the factor evaluator (walker + IC/IR/RankIC/turnover/coverage)."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from src.factor_mining.evaluator import (
    EvaluationResult,
    evaluate_expression,
    evaluate_factor,
    max_abs_corr,
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


def test_evaluate_factor_coverage_uses_universe_mask_denominator():
    # Members-only coverage: a union ticker that is never a member (its
    # panel cells all NaN) must NOT drag coverage down. Union coverage =
    # 30/40 = 0.75 (< 0.8 gate); members-only (A,B,C) = 30/30 = 1.0.
    tickers = list("ABCD")
    dates = pd.date_range("2024-01-01", periods=10)
    panel = _make_panel(tickers, dates)
    panel["$volume"].loc[:, "D"] = np.nan
    fwd = pd.DataFrame(
        np.random.default_rng(7).normal(0, 0.02, size=(10, 4)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    r_union = evaluate_factor(expr, panel, fwd, method="rank")
    assert r_union.coverage == pytest.approx(30 / 40, abs=1e-9)
    mask = pd.DataFrame(True, index=dates, columns=tickers)
    mask.loc[:, "D"] = False  # D is never a universe member
    r_mask = evaluate_factor(expr, panel, fwd, method="rank", universe_mask=mask)
    assert r_mask.coverage == pytest.approx(1.0, abs=1e-9)


def test_evaluate_factor_coverage_mask_penalizes_missing_member_cell():
    # A is a member every day but missing data on 2 days → those NaN
    # member cells still count against coverage (28/30), so the validity
    # gate keeps biting genuinely-undefined factors.
    tickers = list("ABC")
    dates = pd.date_range("2024-01-01", periods=10)
    panel = _make_panel(tickers, dates)
    panel["$volume"].iloc[0, 0] = np.nan
    panel["$volume"].iloc[1, 0] = np.nan
    fwd = pd.DataFrame(
        np.random.default_rng(9).normal(0, 0.02, size=(10, 3)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    mask = pd.DataFrame(True, index=dates, columns=tickers)
    r = evaluate_factor(expr, panel, fwd, method="rank", universe_mask=mask)
    assert r.coverage == pytest.approx(28 / 30, abs=1e-9)


def test_evaluate_factor_coverage_mask_none_equals_all_member_mask():
    # universe_mask=None reproduces the legacy all-cells fraction, which
    # equals the members-only result when every ticker is a member.
    tickers = list("ABC")
    dates = pd.date_range("2024-01-01", periods=10)
    panel = _make_panel(tickers, dates)
    panel["$volume"].iloc[0, 0] = np.nan
    fwd = pd.DataFrame(
        np.random.default_rng(11).normal(0, 0.02, size=(10, 3)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    r_none = evaluate_factor(expr, panel, fwd, method="rank")
    full_mask = pd.DataFrame(True, index=dates, columns=tickers)
    r_full = evaluate_factor(expr, panel, fwd, method="rank", universe_mask=full_mask)
    assert r_none.coverage == pytest.approx(29 / 30, abs=1e-9)
    assert r_full.coverage == pytest.approx(r_none.coverage, abs=1e-9)


def test_evaluate_factor_coverage_counts_mask_only_member_cells_as_uncovered():
    """A member the factor panel omits entirely (mask True, absent from
    factor_values) must count as UNCOVERED — kept in the denominator, not
    dropped — so coverage is not inflated (Codex P2 on #217)."""
    tickers = list("ABC")
    dates = pd.date_range("2024-01-01", periods=10)
    panel = _make_panel(tickers, dates)
    fwd = pd.DataFrame(
        np.random.default_rng(31).normal(0, 0.02, size=(10, 3)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    # The mask declares a 4th member "D" that the panel/factor never produces.
    mask = pd.DataFrame(
        True, index=dates, columns=pd.Index(list("ABCD"), name="instrument"),
    )
    result = evaluate_factor(expr, panel, fwd, method="rank", universe_mask=mask)
    # 4 members × 10 = 40 member cells; A,B,C finite (30), D absent (10
    # uncovered) → 30/40, NOT 1.0 (which dropping D from the denom would give).
    assert result.coverage == pytest.approx(30 / 40, abs=1e-9)


def test_evaluate_factor_method_normal_separates_pearson_from_rank():
    """When ``method='normal'`` and the factor↔return relationship is
    monotone but non-linear, ``ic_mean`` (Pearson) and ``rank_ic_mean``
    (Spearman) must be materially different values — proving fitness
    has two independent IC terms.

    Regression for the rank-IC double-counting bug: before the fix the
    miner called ``evaluate_factor(method="rank")``, which set
    ``ic_mean == rank_ic_mean`` (both Spearman) so the fitness formula
    ``w_ic·|ic_mean| + w_rankic·|rank_ic_mean|`` collapsed to
    ``(w_ic + w_rankic)·|rank IC|`` — double-counting the same signal.
    """
    tickers = [f"T{i:02d}" for i in range(16)]
    dates = pd.date_range("2024-01-01", periods=30)
    rng = np.random.default_rng(2026)

    # Per date: assign tickers values 1..16 in random order; fwd is a
    # cubic transform of those values. The factor cs_rank($volume)
    # preserves the order (Spearman IC ≈ 1.0), but Pearson IC between
    # ranks and cubes is dampened by the non-linearity.
    base = np.empty((30, 16))
    fwd_arr = np.empty((30, 16))
    for d in range(30):
        perm = rng.permutation(16) + 1  # 1..16
        base[d] = perm
        fwd_arr[d] = (perm - 8.5) ** 3 + rng.normal(0, 5, 16)

    panel = _make_panel(tickers, dates)
    panel["$volume"] = pd.DataFrame(
        base,
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    fwd = pd.DataFrame(
        fwd_arr,
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )

    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    result = evaluate_factor(expr, panel, fwd, method="normal")

    # cs_rank(volume) preserves ordering ⇒ Spearman IC ≈ 1.0.
    assert result.rank_ic_mean == pytest.approx(1.0, abs=0.05), (
        f"expected rank_ic_mean ≈ 1.0 for monotone signal, got "
        f"{result.rank_ic_mean!r}"
    )
    # Pearson IC of (uniform ranks) vs (cubic values) is materially below 1.0.
    assert result.ic_mean < 0.95, (
        f"expected ic_mean (Pearson) < 0.95 for cubic relationship, got "
        f"{result.ic_mean!r}"
    )
    # The two must NOT collapse — that is the bug the fix prevents.
    assert abs(result.ic_mean - result.rank_ic_mean) > 0.01, (
        f"Pearson and Spearman collapsed to identical values "
        f"({result.ic_mean!r} vs {result.rank_ic_mean!r}); fitness would "
        f"be double-counting rank IC."
    )


def test_evaluate_factor_method_rank_collapses_headline_and_rank():
    """Sanity check of the docstring contract: with ``method='rank'``
    both ``ic_mean`` and ``rank_ic_mean`` are Spearman by construction.
    Locks in the behavior so callers passing ``method='rank'`` don't
    get surprised by silent semantics drift."""
    tickers = [f"T{i:02d}" for i in range(8)]
    dates = pd.date_range("2024-01-01", periods=20)
    panel = _make_panel(tickers, dates, seed=99)
    rng = np.random.default_rng(99)
    fwd = pd.DataFrame(
        rng.normal(0, 0.02, size=(20, 8)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))
    result = evaluate_factor(expr, panel, fwd, method="rank")
    assert result.ic_mean == pytest.approx(result.rank_ic_mean, abs=1e-12), (
        "method='rank' must place Spearman in both ic_mean and rank_ic_mean"
    )


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
# max_abs_corr — shared pairwise correlation (T2-3)
# ---------------------------------------------------------------------------


def _stack(series_by_idx: dict) -> pd.Series:
    return pd.Series(series_by_idx, dtype=float)


def test_max_abs_corr_perfect_and_anti_correlation():
    base = _stack({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})
    # perfect positive (affine) and perfect negative both → |corr| == 1.
    assert max_abs_corr(base, [base * 2 + 1]) == 1.0
    assert max_abs_corr(base, [-base]) == 1.0


def test_max_abs_corr_takes_the_maximum_across_others():
    base = _stack({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})
    weak = _stack({"a": 1.0, "b": 1.0, "c": 2.0, "d": 1.5})  # weaker corr
    out = max_abs_corr(base, [weak, -base])  # -base is the strongest (|1.0|)
    assert out == 1.0


def test_max_abs_corr_empty_and_below_min_overlap():
    base = _stack({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})
    assert max_abs_corr(base, []) == 0.0  # no others
    # only 2 jointly-non-NaN cells (< min_overlap=3) → skipped → 0.0
    short = _stack({"a": 1.0, "b": 2.0})
    assert max_abs_corr(base, [short]) == 0.0


def test_max_abs_corr_zero_variance_corr_is_nan_skipped():
    base = _stack({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})
    const = _stack({"a": 5.0, "b": 5.0, "c": 5.0, "d": 5.0})  # corr → NaN
    assert max_abs_corr(base, [const]) == 0.0


def test_max_abs_corr_never_returns_non_finite_with_inf_input():
    # T2-3 guard fix: np.isfinite (not pd.notna) admits the correlation, so a
    # degenerate / non-finite pairing can never poison the maximum. Even with an
    # inf-valued partner the result is a finite 0.0, never inf/NaN.
    base = _stack({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})
    inf_partner = _stack({"a": 1.0, "b": 2.0, "c": np.inf, "d": 4.0})
    out = max_abs_corr(base, [inf_partner])
    assert np.isfinite(out) and out == 0.0


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


def test_evaluate_factor_rank_method_skips_pearson_ic(monkeypatch):
    """method='rank' (the validator's per-entry hot path) must NOT compute
    the Pearson IC — it is the headline only when method != 'rank' and is
    discarded on the rank path. Guards the dead-work elimination."""
    import src.factor_mining.evaluator as _ev

    calls: list[str] = []
    real = _ev._ic_per_day

    def _spy(*args, **kwargs):
        calls.append(kwargs.get("method"))
        return real(*args, **kwargs)

    monkeypatch.setattr(_ev, "_ic_per_day", _spy)

    tickers = list("ABCDE")
    dates = pd.date_range("2024-01-01", periods=12)
    rng = np.random.default_rng(3)
    fwd = pd.DataFrame(
        rng.normal(0, 0.02, size=(12, 5)),
        index=pd.Index(dates, name="datetime"),
        columns=pd.Index(tickers, name="instrument"),
    )
    panel = _make_panel(tickers, dates)
    panel["$volume"] = fwd
    expr = OperatorCall("cs_rank", (Terminal("$volume"),))

    calls.clear()
    evaluate_factor(expr, panel, fwd, method="rank")
    assert calls == ["rank"]  # Pearson IC skipped on the rank path

    calls.clear()
    evaluate_factor(expr, panel, fwd, method="normal")
    assert "normal" in calls and "rank" in calls  # both computed off the rank path
