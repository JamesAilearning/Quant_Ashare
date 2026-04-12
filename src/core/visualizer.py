"""Result visualization — equity curve, drawdown, and monthly heatmap.

Generates publication-quality charts from backtest results using matplotlib.
Outputs PNG files to the specified directory.

Boundaries
----------
- Operates on backtest return_series dict (from CanonicalBacktestOutput).
- No qlib dependency; purely post-processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.core.logger import get_logger

_logger = get_logger(__name__)


class VisualizerError(RuntimeError):
    """Raised on visualization failures."""


@dataclass(frozen=True)
class VisualizerConfig:
    """Configuration for result visualization."""

    output_dir: str = "output/charts"
    figsize: tuple[int, int] = (12, 6)
    dpi: int = 150
    style: str = "seaborn-v0_8-darkgrid"


@dataclass(frozen=True)
class VisualizerResult:
    """Paths to generated chart files."""

    equity_curve_path: str
    drawdown_path: str
    monthly_heatmap_path: str


class ResultVisualizer:
    """Generates portfolio performance charts."""

    @classmethod
    def generate(
        cls,
        return_series: Mapping[str, Any],
        config: VisualizerConfig | None = None,
    ) -> VisualizerResult:
        """Generate all charts from backtest return_series.

        Parameters
        ----------
        return_series : dict
            Must contain "return" and "bench" keys, each mapping
            date strings to float values.
        config : VisualizerConfig, optional

        Returns
        -------
        VisualizerResult with paths to generated PNG files.
        """
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
        import pandas as pd
        import numpy as np

        if config is None:
            config = VisualizerConfig()

        if "return" not in return_series:
            raise VisualizerError(
                "return_series must contain 'return' key with daily returns."
            )

        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Parse return series
        ret_dict = return_series["return"]
        bench_dict = return_series.get("bench", {})

        returns = pd.Series(
            {pd.Timestamp(k): float(v) for k, v in ret_dict.items()}
        ).sort_index()

        if returns.empty:
            raise VisualizerError(
                "return_series['return'] is empty after parsing. "
                "Cannot generate charts with no data."
            )

        benchmark = pd.Series(
            {pd.Timestamp(k): float(v) for k, v in bench_dict.items()}
        ).sort_index() if bench_dict else None

        try:
            plt.style.use(config.style)
        except OSError:
            pass  # fallback to default style

        # Generate charts
        eq_path = str(output_dir / "equity_curve.png")
        cls._plot_equity_curve(returns, benchmark, eq_path, config)

        dd_path = str(output_dir / "drawdown.png")
        cls._plot_drawdown(returns, benchmark, dd_path, config)

        hm_path = str(output_dir / "monthly_heatmap.png")
        cls._plot_monthly_heatmap(returns, hm_path, config)

        plt.close("all")

        _logger.info("Charts saved to %s/", output_dir)
        _logger.info("  - equity_curve.png")
        _logger.info("  - drawdown.png")
        _logger.info("  - monthly_heatmap.png")

        return VisualizerResult(
            equity_curve_path=eq_path,
            drawdown_path=dd_path,
            monthly_heatmap_path=hm_path,
        )

    @classmethod
    def _plot_equity_curve(
        cls,
        returns: Any,
        benchmark: Any,
        path: str,
        config: VisualizerConfig,
    ) -> None:
        """Plot cumulative return (equity curve)."""
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=config.figsize)

        cum_ret = (1 + returns).cumprod()
        ax.plot(cum_ret.index, cum_ret.values, label="Strategy", linewidth=1.5, color="#2196F3")

        if benchmark is not None and not benchmark.empty:
            cum_bench = (1 + benchmark).cumprod()
            ax.plot(cum_bench.index, cum_bench.values, label="Benchmark",
                    linewidth=1.2, color="#9E9E9E", linestyle="--")

        ax.set_title("Equity Curve", fontsize=14, fontweight="bold")
        ax.set_xlabel("Date")
        ax.set_ylabel("Cumulative Return")
        ax.legend(loc="upper left")
        ax.grid(True, alpha=0.3)

        # Annotate final values
        final_ret = cum_ret.iloc[-1] - 1
        ax.annotate(
            f"Total: {final_ret:.1%}",
            xy=(cum_ret.index[-1], cum_ret.iloc[-1]),
            fontsize=10,
            ha="right",
        )

        fig.tight_layout()
        fig.savefig(path, dpi=config.dpi, bbox_inches="tight")
        plt.close(fig)

    @classmethod
    def _plot_drawdown(
        cls,
        returns: Any,
        benchmark: Any,
        path: str,
        config: VisualizerConfig,
    ) -> None:
        """Plot drawdown chart."""
        import matplotlib.pyplot as plt
        import numpy as np

        fig, ax = plt.subplots(figsize=config.figsize)

        cum_ret = (1 + returns).cumprod()
        running_max = cum_ret.cummax()
        drawdown = (cum_ret - running_max) / running_max

        ax.fill_between(drawdown.index, drawdown.values, 0,
                        alpha=0.4, color="#F44336", label="Strategy Drawdown")
        ax.plot(drawdown.index, drawdown.values, color="#D32F2F", linewidth=0.8)

        if benchmark is not None and not benchmark.empty:
            cum_bench = (1 + benchmark).cumprod()
            bench_max = cum_bench.cummax()
            bench_dd = (cum_bench - bench_max) / bench_max
            ax.plot(bench_dd.index, bench_dd.values, color="#9E9E9E",
                    linewidth=0.8, linestyle="--", label="Benchmark Drawdown")

        ax.set_title("Drawdown", fontsize=14, fontweight="bold")
        ax.set_xlabel("Date")
        ax.set_ylabel("Drawdown")
        ax.legend(loc="lower left")
        ax.grid(True, alpha=0.3)

        # Annotate max drawdown
        max_dd = drawdown.min()
        max_dd_date = drawdown.idxmin()
        ax.annotate(
            f"Max DD: {max_dd:.1%}",
            xy=(max_dd_date, max_dd),
            xytext=(max_dd_date, max_dd * 0.7),
            fontsize=10,
            arrowprops=dict(arrowstyle="->", color="black"),
        )

        fig.tight_layout()
        fig.savefig(path, dpi=config.dpi, bbox_inches="tight")
        plt.close(fig)

    @classmethod
    def _plot_monthly_heatmap(
        cls,
        returns: Any,
        path: str,
        config: VisualizerConfig,
    ) -> None:
        """Plot monthly returns heatmap."""
        import matplotlib.pyplot as plt
        import numpy as np
        import pandas as pd

        # Aggregate daily returns to monthly
        monthly = returns.resample("ME").apply(lambda x: (1 + x).prod() - 1)

        # Create year x month matrix
        monthly_df = pd.DataFrame({
            "year": monthly.index.year,
            "month": monthly.index.month,
            "return": monthly.values,
        })
        pivot = monthly_df.pivot(index="year", columns="month", values="return")
        pivot.columns = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][:len(pivot.columns)]

        fig, ax = plt.subplots(figsize=(config.figsize[0], max(3, len(pivot) * 0.8)))

        # Color scale centered at 0
        vmax = max(abs(pivot.min().min()), abs(pivot.max().max()), 0.05)
        im = ax.imshow(
            pivot.values, cmap="RdYlGn", aspect="auto",
            vmin=-vmax, vmax=vmax,
        )

        # Labels
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels(pivot.columns, fontsize=9)
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index, fontsize=9)

        # Annotate cells with return values
        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                val = pivot.iloc[i, j]
                if not np.isnan(val):
                    color = "white" if abs(val) > vmax * 0.6 else "black"
                    ax.text(j, i, f"{val:.1%}", ha="center", va="center",
                            fontsize=8, color=color)

        ax.set_title("Monthly Returns Heatmap", fontsize=14, fontweight="bold")
        fig.colorbar(im, ax=ax, shrink=0.8, label="Return")

        fig.tight_layout()
        fig.savefig(path, dpi=config.dpi, bbox_inches="tight")
        plt.close(fig)
