"""V2 Quantitative Trading Pipeline — end-to-end entry point.

Usage:
    python main.py
"""

from __future__ import annotations

from src.core.pipeline import Pipeline, PipelineConfig


def main() -> None:
    config = PipelineConfig(
        # qlib runtime
        provider_uri=r"D:/qlib_data/my_cn_data",
        region="cn",

        # features
        instruments="csi300",
        feature_handler="Alpha158",
        train_start="2022-01-01",
        train_end="2024-12-31",
        valid_start="2025-01-01",
        valid_end="2025-06-30",
        test_start="2025-07-01",
        test_end="2025-12-31",

        # model
        model_type="LGBModel",
        num_boost_round=1000,
        learning_rate=0.0421,
        max_depth=8,
        num_leaves=210,

        # backtest
        benchmark_code="SH600000",  # Use a stock if index data unavailable
        init_cash=100_000_000,
        commission_rate=0.0005,
        stamp_tax_bps=10.0,
        slippage_bps=5.0,
        min_cost=5.0,
        execution_price_kind="close",
        topk=50,
        n_drop=5,

        # output
        output_dir="output",
    )

    Pipeline.run(config)


if __name__ == "__main__":
    main()
