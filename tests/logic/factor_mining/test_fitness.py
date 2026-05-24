"""Tests for the composite fitness function and validity filters."""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from src.factor_mining.evaluator import EvaluationResult
from src.factor_mining.expression import OperatorCall, Terminal
from src.factor_mining.fitness import (
    ANNUALISATION_DAYS,
    FitnessConfig,
    compute_fitness,
    expression_size,
    passes_validity,
)


def _make_result(
    *,
    factor_values=None,
    ic_mean=0.05,
    ic_std=0.10,
    ir=0.5,
    rank_ic_mean=0.04,
    rank_ic_std=0.08,
    rank_ir=0.5,
    turnover_daily=0.10,
    coverage=0.95,
    n_obs_per_day_min=20,
):
    if factor_values is None:
        # 100 dates × 50 tickers, well-varying — passes default validity
        rng = np.random.default_rng(1)
        factor_values = pd.DataFrame(
            rng.normal(0, 1, size=(100, 50)),
            index=pd.date_range("2024-01-01", periods=100),
            columns=[f"T{i}" for i in range(50)],
        )
    return EvaluationResult(
        factor_values=factor_values,
        ic_mean=ic_mean,
        ic_std=ic_std,
        ir=ir,
        rank_ic_mean=rank_ic_mean,
        rank_ic_std=rank_ic_std,
        rank_ir=rank_ir,
        turnover_daily=turnover_daily,
        coverage=coverage,
        n_obs_per_day_min=n_obs_per_day_min,
    )


# ---------------------------------------------------------------------------
# FitnessConfig defaults
# ---------------------------------------------------------------------------


def test_fitness_config_default_cost_rate_matches_d1():
    cfg = FitnessConfig()
    assert cfg.cost_rate == 0.003


def test_fitness_config_default_weights_match_v1_5_1():
    cfg = FitnessConfig()
    assert cfg.w_ic == 1.0
    assert cfg.w_ir == 0.5
    assert cfg.w_rankic == 0.5
    assert cfg.w_turnover == 0.2
    assert cfg.w_corr == 0.8
    assert cfg.w_complexity == 0.01


def test_fitness_config_validity_defaults():
    cfg = FitnessConfig()
    assert cfg.coverage_min == 0.8
    assert cfg.variance_days_frac_min == 0.7
    assert cfg.variance_min == 1e-6
    assert cfg.extreme_outlier_frac_max == 0.05


# ---------------------------------------------------------------------------
# passes_validity
# ---------------------------------------------------------------------------


def test_passes_validity_normal_factor():
    assert passes_validity(_make_result(), FitnessConfig())


def test_invalid_coverage_fails():
    r = _make_result(coverage=0.5)
    assert not passes_validity(r, FitnessConfig())


def test_invalid_variance_fails():
    # Constant factor across all dates → 0 variance days
    constant = pd.DataFrame(
        np.full((100, 50), 0.5),
        index=pd.date_range("2024-01-01", periods=100),
        columns=[f"T{i}" for i in range(50)],
    )
    r = _make_result(factor_values=constant)
    assert not passes_validity(r, FitnessConfig())


def test_invalid_sanity_fails():
    rng = np.random.default_rng(3)
    extreme = pd.DataFrame(
        rng.normal(0, 1, size=(100, 50)),
        index=pd.date_range("2024-01-01", periods=100),
        columns=[f"T{i}" for i in range(50)],
    )
    # Inject >5% extreme outliers (10% here)
    flat_idx = np.unravel_index(np.arange(500), extreme.shape)
    extreme.values[flat_idx] = 1e10
    r = _make_result(factor_values=extreme)
    assert not passes_validity(r, FitnessConfig())


def test_sanity_does_not_count_nan_as_outlier():
    """A factor with 30 % NaN cells but no magnitude outliers should
    pass the sanity check — the coverage check (separate, also 0.80)
    is what enforces the NaN budget. The earlier implementation
    double-counted NaN into ``_extreme_outlier_frac`` and made the
    default ``extreme_outlier_frac_max=0.05`` effectively require
    coverage ≥ 0.95, which is a stricter constraint than
    ``coverage_min=0.80``."""
    rng = np.random.default_rng(11)
    arr = rng.normal(0, 1, size=(100, 50))
    # Mask ~30% of cells as NaN — finite values are all bounded.
    nan_mask = rng.random(arr.shape) < 0.30
    arr[nan_mask] = np.nan
    df = pd.DataFrame(
        arr,
        index=pd.date_range("2024-01-01", periods=100),
        columns=[f"T{i}" for i in range(50)],
    )
    # Coverage ≈ 0.70 — fails the default coverage_min=0.80, but the
    # sanity check should NOT also reject.
    r = _make_result(factor_values=df, coverage=0.70)
    # Sanity (outlier check) alone must NOT reject this factor.
    cfg_no_coverage_check = FitnessConfig(
        coverage_min=0.0,
        variance_days_frac_min=0.0,
    )
    assert passes_validity(r, cfg_no_coverage_check), (
        "30% NaN + 70% in-range finite must pass when sanity is the only check"
    )


def test_sanity_extreme_outliers_in_finite_cells_still_fail():
    """The finite-cells-only denominator must still flag magnitude
    outliers when they cluster among the finite fraction."""
    rng = np.random.default_rng(12)
    arr = rng.normal(0, 1, size=(100, 50))
    # 30% NaN
    nan_mask = rng.random(arr.shape) < 0.30
    arr[nan_mask] = np.nan
    # Of the remaining ~70% finite cells, set ~10% to 1e10 — that's
    # ~7% of all cells but ~10% of FINITE cells, which exceeds
    # extreme_outlier_frac_max=0.05.
    finite_idx = np.argwhere(~nan_mask)
    n_outliers = int(0.10 * len(finite_idx))
    rng.shuffle(finite_idx)
    for i, j in finite_idx[:n_outliers]:
        arr[i, j] = 1e10
    df = pd.DataFrame(
        arr,
        index=pd.date_range("2024-01-01", periods=100),
        columns=[f"T{i}" for i in range(50)],
    )
    r = _make_result(factor_values=df, coverage=0.70)
    cfg_no_coverage_check = FitnessConfig(
        coverage_min=0.0,
        variance_days_frac_min=0.0,
        extreme_outlier_frac_max=0.05,
    )
    assert not passes_validity(r, cfg_no_coverage_check)


def test_sanity_all_nan_factor_returns_zero_outlier_frac():
    """An all-NaN factor must not crash the sanity check. Coverage is 0
    so the coverage check rejects; sanity should report 0% outliers
    (the metric is undefined; we report 0 so coverage is binding)."""
    all_nan = pd.DataFrame(
        np.full((100, 50), np.nan),
        index=pd.date_range("2024-01-01", periods=100),
        columns=[f"T{i}" for i in range(50)],
    )
    r = _make_result(factor_values=all_nan, coverage=0.0)
    cfg_no_coverage_check = FitnessConfig(
        coverage_min=0.0,
        variance_days_frac_min=0.0,
    )
    # Sanity alone does not reject — coverage_min would (here we
    # disabled coverage so we can isolate the sanity outcome).
    assert passes_validity(r, cfg_no_coverage_check)


# ---------------------------------------------------------------------------
# compute_fitness — formula correctness
# ---------------------------------------------------------------------------


def test_fitness_invalid_factor_is_neg_inf():
    r = _make_result(coverage=0.1)
    assert compute_fitness(r, expr_size=5, novelty_penalty=0.0) == float("-inf")


def test_fitness_nan_ic_is_neg_inf():
    r = _make_result(ic_mean=float("nan"))
    assert compute_fitness(r, expr_size=5, novelty_penalty=0.0) == float("-inf")


def test_fitness_passing_factor_is_finite():
    r = _make_result()
    score = compute_fitness(r, expr_size=5, novelty_penalty=0.0)
    assert np.isfinite(score)


def test_fitness_cost_term_uses_annualised_rate():
    """The turnover penalty must be exactly
    w_turnover × turnover_daily × 252 × cost_rate."""
    cfg = FitnessConfig()
    r = _make_result(turnover_daily=0.10)
    score_with_cost = compute_fitness(r, expr_size=0, novelty_penalty=0.0, config=cfg)
    # Recompute the same fitness with turnover_daily=0 (zero cost term)
    r_no_cost = _make_result(turnover_daily=0.0)
    score_no_cost = compute_fitness(r_no_cost, expr_size=0, novelty_penalty=0.0, config=cfg)
    delta = score_no_cost - score_with_cost
    expected = cfg.w_turnover * 0.10 * ANNUALISATION_DAYS * cfg.cost_rate
    assert delta == pytest.approx(expected, rel=1e-9, abs=1e-12)


def test_fitness_novelty_term_subtracts_w_corr():
    cfg = FitnessConfig()
    r = _make_result()
    score_no_novelty = compute_fitness(r, expr_size=0, novelty_penalty=0.0, config=cfg)
    score_high_novelty = compute_fitness(r, expr_size=0, novelty_penalty=1.0, config=cfg)
    delta = score_no_novelty - score_high_novelty
    assert delta == pytest.approx(cfg.w_corr * 1.0, rel=1e-9)


def test_fitness_complexity_subtracts_w_complexity_times_size():
    cfg = FitnessConfig()
    r = _make_result()
    a = compute_fitness(r, expr_size=0, novelty_penalty=0.0, config=cfg)
    b = compute_fitness(r, expr_size=10, novelty_penalty=0.0, config=cfg)
    delta = a - b
    assert delta == pytest.approx(cfg.w_complexity * 10, rel=1e-9)


def test_fitness_ic_term_uses_abs():
    """w_ic * |ic_mean| — a negative IC mean still contributes positively
    to the IC term (sign-agnostic signal strength). The IR carries the
    sign separately."""
    cfg = FitnessConfig(w_ir=0.0, w_rankic=0.0, w_turnover=0.0, w_corr=0.0, w_complexity=0.0)
    r_pos = _make_result(ic_mean=0.05, rank_ic_mean=0.04)
    r_neg = _make_result(ic_mean=-0.05, rank_ic_mean=-0.04)
    score_pos = compute_fitness(r_pos, expr_size=0, novelty_penalty=0.0, config=cfg)
    score_neg = compute_fitness(r_neg, expr_size=0, novelty_penalty=0.0, config=cfg)
    assert score_pos == pytest.approx(score_neg, abs=1e-9)


def test_fitness_with_nan_ir_does_not_explode():
    """IR=NaN must contribute 0 to the IR term, not propagate NaN."""
    cfg = FitnessConfig()
    r = _make_result(ir=float("nan"), rank_ir=float("nan"))
    score = compute_fitness(r, expr_size=5, novelty_penalty=0.0, config=cfg)
    assert np.isfinite(score)


# ---------------------------------------------------------------------------
# expression_size
# ---------------------------------------------------------------------------


def test_expression_size_terminal():
    assert expression_size(Terminal("$close")) == 1


def test_expression_size_single_op():
    e = OperatorCall("cs_rank", (Terminal("$volume"),))
    assert expression_size(e) == 2  # cs_rank + $volume


def test_expression_size_nested():
    e = OperatorCall(
        "cs_rank",
        (
            OperatorCall(
                "div_safe",
                (
                    OperatorCall("ts_delta", (Terminal("$close"), Terminal("20"))),
                    Terminal("$close"),
                ),
            ),
        ),
    )
    # cs_rank + div_safe + ts_delta + $close + 20 + $close = 6
    assert expression_size(e) == 6


# ---------------------------------------------------------------------------
# D5 strict gate
# ---------------------------------------------------------------------------


def test_fitness_does_not_import_qlib_or_pit_directly():
    import src.factor_mining.fitness as mod

    src = inspect.getsource(mod)
    assert "from qlib" not in src
    assert "qlib.data" not in src
    assert "qlib.init" not in src
    assert "from src.pit" not in src
    assert "import src.pit" not in src


import pytest  # noqa: E402
