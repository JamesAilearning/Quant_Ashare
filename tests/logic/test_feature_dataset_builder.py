"""Unit tests for FeatureDatasetBuilder."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.feature_dataset_builder import (
    FeatureDatasetBuilder,
    FeatureDatasetBuilderError,
    FeatureDatasetConfig,
)


class FeatureDatasetBuilderStructuralTests(unittest.TestCase):
    """Structural validation — no qlib needed."""

    def test_empty_instruments_rejected(self) -> None:
        with patch("src.data.feature_dataset_builder.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(FeatureDatasetBuilderError, "instruments"):
                FeatureDatasetBuilder.build(FeatureDatasetConfig(
                    instruments="",
                    feature_handler="Alpha158",
                    train_start="2024-01-01", train_end="2024-12-31",
                    valid_start="2025-01-01", valid_end="2025-06-30",
                    test_start="2025-07-01", test_end="2025-12-31",
                ))

    def test_unsupported_handler_rejected(self) -> None:
        with patch("src.data.feature_dataset_builder.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(FeatureDatasetBuilderError, "feature_handler"):
                FeatureDatasetBuilder.build(FeatureDatasetConfig(
                    instruments="csi300",
                    feature_handler="CustomHandler",
                    train_start="2024-01-01", train_end="2024-12-31",
                    valid_start="2025-01-01", valid_end="2025-06-30",
                    test_start="2025-07-01", test_end="2025-12-31",
                ))

    def test_bad_iso_date_rejected(self) -> None:
        with patch("src.data.feature_dataset_builder.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(FeatureDatasetBuilderError, "Invalid ISO"):
                FeatureDatasetBuilder.build(FeatureDatasetConfig(
                    instruments="csi300",
                    feature_handler="Alpha158",
                    train_start="not-a-date", train_end="2024-12-31",
                    valid_start="2025-01-01", valid_end="2025-06-30",
                    test_start="2025-07-01", test_end="2025-12-31",
                ))

    def test_train_start_after_train_end_rejected(self) -> None:
        with patch("src.data.feature_dataset_builder.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(FeatureDatasetBuilderError, "train_start"):
                FeatureDatasetBuilder.build(FeatureDatasetConfig(
                    instruments="csi300",
                    feature_handler="Alpha158",
                    train_start="2025-12-31", train_end="2024-01-01",
                    valid_start="2025-01-01", valid_end="2025-06-30",
                    test_start="2025-07-01", test_end="2025-12-31",
                ))

    def test_overlapping_train_valid_rejected(self) -> None:
        with patch("src.data.feature_dataset_builder.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(FeatureDatasetBuilderError, "data leakage"):
                FeatureDatasetBuilder.build(FeatureDatasetConfig(
                    instruments="csi300",
                    feature_handler="Alpha158",
                    train_start="2024-01-01", train_end="2024-12-31",
                    valid_start="2024-06-01", valid_end="2025-06-30",
                    test_start="2025-07-01", test_end="2025-12-31",
                ))

    def test_overlapping_valid_test_rejected(self) -> None:
        with patch("src.data.feature_dataset_builder.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(FeatureDatasetBuilderError, "data leakage"):
                FeatureDatasetBuilder.build(FeatureDatasetConfig(
                    instruments="csi300",
                    feature_handler="Alpha158",
                    train_start="2024-01-01", train_end="2024-06-30",
                    valid_start="2024-07-01", valid_end="2025-06-30",
                    test_start="2025-01-01", test_end="2025-12-31",
                ))

    def test_qlib_not_initialized_rejected(self) -> None:
        with patch("src.data.feature_dataset_builder.is_canonical_qlib_initialized", return_value=False):
            with self.assertRaisesRegex(FeatureDatasetBuilderError, "not initialized"):
                FeatureDatasetBuilder.build(FeatureDatasetConfig(
                    instruments="csi300",
                    feature_handler="Alpha158",
                    train_start="2024-01-01", train_end="2024-12-31",
                    valid_start="2025-01-01", valid_end="2025-06-30",
                    test_start="2025-07-01", test_end="2025-12-31",
                ))

    def test_empty_date_field_rejected(self) -> None:
        with patch("src.data.feature_dataset_builder.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(FeatureDatasetBuilderError, "test_end"):
                FeatureDatasetBuilder.build(FeatureDatasetConfig(
                    instruments="csi300",
                    feature_handler="Alpha158",
                    train_start="2024-01-01", train_end="2024-12-31",
                    valid_start="2025-01-01", valid_end="2025-06-30",
                    test_start="2025-07-01", test_end="",
                ))


_QLIB_DATA_DIR = Path(r"D:/qlib_data/my_cn_data")
_HAS_QLIB_DATA = _QLIB_DATA_DIR.exists() and (_QLIB_DATA_DIR / "calendars").exists()


from tests.e2e_guard import skip_unless_e2e

@skip_unless_e2e
@unittest.skipUnless(_HAS_QLIB_DATA, "qlib data bundle not available")
class FeatureDatasetBuilderE2ETests(unittest.TestCase):
    """E2E tests that require real qlib data."""

    @classmethod
    def setUpClass(cls) -> None:
        from src.core.qlib_runtime import (
            QlibRuntimeConfig,
            init_qlib_canonical,
            is_canonical_qlib_initialized,
        )
        if not is_canonical_qlib_initialized():
            init_qlib_canonical(QlibRuntimeConfig(
                provider_uri=str(_QLIB_DATA_DIR),
                region="cn",
                data_adjust_mode="pre_adjusted",
            ))

    def test_csi300_alpha158_builds_successfully(self) -> None:
        result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments="csi300",
            feature_handler="Alpha158",
            train_start="2024-01-01", train_end="2025-06-30",
            valid_start="2025-07-01", valid_end="2025-09-30",
            test_start="2025-10-01", test_end="2025-12-31",
        ))
        self.assertGreater(result.train_shape[0], 0)
        self.assertGreater(result.valid_shape[0], 0)
        self.assertGreater(result.test_shape[0], 0)
        self.assertEqual(result.train_shape[1], 158)
        self.assertIn("KMID", result.feature_columns)


if __name__ == "__main__":
    unittest.main()
