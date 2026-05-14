"""Unit tests for configuration validation — provider_uri and key checking."""

from __future__ import annotations

import sys as _sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))


class ConfigValidationTests(unittest.TestCase):
    def test_empty_provider_uri_rejected(self) -> None:
        from web.operator_ui.config_forms import validate_provider_uri
        with self.assertRaises(ValueError):
            validate_provider_uri("")
        with self.assertRaises(ValueError):
            validate_provider_uri("   ")

    def test_valid_provider_uri_passes(self) -> None:
        from web.operator_ui.config_forms import validate_provider_uri
        validate_provider_uri("D:/qlib_data/my_cn_data")
        validate_provider_uri("/data/qlib_bundle")

    def test_unknown_config_keys_hard_fail(self) -> None:
        from web.operator_ui.config_forms import PIPELINE_KEYS, validate_config_keys
        config = {"provider_uri": "/data", "typo_key": 123, "another_typo": True}
        with self.assertRaises(ValueError):
            validate_config_keys(config, PIPELINE_KEYS)

    def test_valid_config_keys_pass(self) -> None:
        from web.operator_ui.config_forms import PIPELINE_KEYS, validate_config_keys
        config = {"provider_uri": "/data", "instruments": "csi300", "train_start": "2022-01-01",
                   "train_end": "2024-12-31", "valid_start": "2025-01-01", "valid_end": "2025-06-30",
                   "test_start": "2025-07-01", "test_end": "2025-12-31"}
        validate_config_keys(config, PIPELINE_KEYS)

    def test_pipeline_keys_match_pipeline_config_fields(self) -> None:
        from src.core.pipeline import PipelineConfig
        from web.operator_ui.config_forms import PIPELINE_KEYS

        expected = {field.name for field in PipelineConfig.__dataclass_fields__.values()}
        self.assertEqual(PIPELINE_KEYS, expected)

    def test_walk_forward_keys_match_config_fields_plus_runtime_keys(self) -> None:
        from src.core.walk_forward import WalkForwardConfig
        from web.operator_ui.config_forms import WALK_FORWARD_KEYS

        expected = {
            field.name for field in WalkForwardConfig.__dataclass_fields__.values()
        } | {"provider_uri", "region"}
        self.assertEqual(WALK_FORWARD_KEYS, expected)

    def test_tushare_provider_keys_match_config_fields(self) -> None:
        from src.data.tushare.provider_bundle import TushareQlibProviderBundleConfig
        from web.operator_ui.config_forms import TUSHARE_PROVIDER_KEYS

        expected = {
            field.name
            for field in TushareQlibProviderBundleConfig.__dataclass_fields__.values()
        }
        self.assertEqual(TUSHARE_PROVIDER_KEYS, expected)


if __name__ == "__main__":
    unittest.main()
