"""Streamlit configuration forms and validation."""

from __future__ import annotations

from typing import Any


def validate_provider_uri(uri: str) -> None:
    """Raise ValueError if provider_uri is empty or whitespace-only."""
    if not str(uri or "").strip():
        raise ValueError("provider_uri is required for canonical qlib init.")


def validate_config_keys(config: dict[str, Any], known_keys: set[str]) -> None:
    """Reject unknown config keys — no silent fallback."""
    unknown = set(config) - known_keys
    if unknown:
        raise ValueError(
            f"Unknown config keys: {sorted(unknown)}. "
            f"Allowed: {sorted(known_keys)}."
        )


PIPELINE_KEYS = {
    "provider_uri", "region",
    "instruments", "feature_handler",
    "train_start", "train_end", "valid_start", "valid_end", "test_start", "test_end",
    "model_type", "num_boost_round", "early_stopping_rounds", "learning_rate",
    "max_depth", "num_leaves", "lambda_l1", "lambda_l2", "min_data_in_leaf",
    "feature_fraction", "bagging_fraction", "bagging_freq", "seed",
    "benchmark_code", "init_cash", "commission_rate", "stamp_tax_bps",
    "slippage_bps", "min_cost", "execution_price_kind", "adjust_mode",
    "signal_to_execution_lag", "topk", "n_drop", "limit_threshold",
    "run_factor_analysis", "factor_forward_period", "factor_top_n", "factor_max_decay_lag",
    "run_attribution", "industry_artifact_path", "industry_manifest_path",
    "industry_taxonomy_id", "industry_temporal_mode",
    "output_dir",
}

WALK_FORWARD_KEYS = {
    "provider_uri", "region",
    "instruments", "feature_handler",
    "overall_start", "overall_end",
    "train_months", "valid_months", "test_months", "step_months",
    "model_type", "num_boost_round", "early_stopping_rounds", "learning_rate",
    "max_depth", "num_leaves", "lambda_l1", "lambda_l2", "min_data_in_leaf",
    "feature_fraction", "bagging_fraction", "bagging_freq", "seed",
    "ensemble_window",
    "benchmark_code", "init_cash", "topk", "n_drop",
    "commission_rate", "stamp_tax_bps", "slippage_bps", "min_cost",
    "execution_price_kind", "adjust_mode", "signal_to_execution_lag", "limit_threshold",
    "run_attribution", "industry_artifact_path", "industry_manifest_path",
    "industry_taxonomy_id", "industry_temporal_mode",
    "output_dir",
}
