"""Tests for src.core.visualizer — result visualization."""

import unittest
import tempfile
from pathlib import Path

import pandas as pd
import numpy as np

from src.core.visualizer import (
    ResultVisualizer,
    VisualizerConfig,
    VisualizerError,
    VisualizerResult,
)


def _make_return_series(n_days=60):
    """Create synthetic return series dict."""
    dates = pd.date_range("2024-10-01", periods=n_days, freq="B")
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, n_days)
    bench = np.random.normal(0.0005, 0.015, n_days)
    return {
        "return": {str(d.date()): float(r) for d, r in zip(dates, returns)},
        "bench": {str(d.date()): float(b) for d, b in zip(dates, bench)},
    }


class VisualizerTests(unittest.TestCase):
    """Tests for chart generation."""

    def test_generates_all_charts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            return_series = _make_return_series()
            result = ResultVisualizer.generate(
                return_series=return_series,
                config=VisualizerConfig(output_dir=tmpdir, dpi=72),
            )
            self.assertIsInstance(result, VisualizerResult)
            self.assertTrue(Path(result.equity_curve_path).exists())
            self.assertTrue(Path(result.drawdown_path).exists())
            self.assertTrue(Path(result.monthly_heatmap_path).exists())

    def test_works_without_benchmark(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            return_series = _make_return_series()
            del return_series["bench"]
            result = ResultVisualizer.generate(
                return_series=return_series,
                config=VisualizerConfig(output_dir=tmpdir, dpi=72),
            )
            self.assertTrue(Path(result.equity_curve_path).exists())

    def test_rejects_missing_return_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(VisualizerError):
                ResultVisualizer.generate(
                    return_series={"bench": {}},
                    config=VisualizerConfig(output_dir=tmpdir),
                )

    def test_file_sizes_reasonable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            return_series = _make_return_series(n_days=120)
            result = ResultVisualizer.generate(
                return_series=return_series,
                config=VisualizerConfig(output_dir=tmpdir, dpi=100),
            )
            # Charts should be non-trivial size (>1KB)
            for path in [result.equity_curve_path, result.drawdown_path, result.monthly_heatmap_path]:
                size = Path(path).stat().st_size
                self.assertGreater(size, 1000, f"{path} too small: {size} bytes")


if __name__ == "__main__":
    unittest.main()
