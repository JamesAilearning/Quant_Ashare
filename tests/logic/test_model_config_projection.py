"""Tests for shared model-training config projection."""

from __future__ import annotations

import unittest

from src.core.model_config_projection import build_model_train_config
from src.core.model_trainer import ModelTrainConfig
from src.core.pipeline import PipelineConfig
from src.core.walk_forward import WalkForwardConfig


class ModelConfigProjectionTests(unittest.TestCase):
    def test_pipeline_and_walk_forward_projection_share_supported_fields(self) -> None:
        overrides = {
            "model_type": "LGBModel",
            "num_boost_round": 321,
            "early_stopping_rounds": 17,
            "learning_rate": 0.031,
            "max_depth": 5,
            "num_leaves": 29,
            "lambda_l1": 0.7,
            "lambda_l2": 1.3,
            "min_data_in_leaf": 33,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.9,
            "bagging_freq": 2,
            "compute_device": "gpu",
        }
        pipeline_config = PipelineConfig(provider_uri="/tmp/fake", seed=42, **overrides)
        walk_forward_config = WalkForwardConfig(**overrides)

        self.assertEqual(
            build_model_train_config(pipeline_config),
            build_model_train_config(walk_forward_config),
        )

    def test_mapping_projection_fills_model_train_defaults(self) -> None:
        config = build_model_train_config({
            "model_type": "LGBModel",
            "num_boost_round": 123,
        })

        self.assertIsInstance(config, ModelTrainConfig)
        self.assertEqual(config.num_boost_round, 123)
        self.assertEqual(config.early_stopping_rounds, 50)
        # Tuned defaults fill in for everything not supplied (C2-c).
        self.assertEqual(config.lambda_l1, 0.0)
        self.assertEqual(config.lambda_l2, 1.0)
        self.assertEqual(config.min_data_in_leaf, 50)
        self.assertEqual(config.feature_fraction, 0.8)
        self.assertEqual(config.bagging_fraction, 0.8)
        self.assertEqual(config.bagging_freq, 5)
        self.assertEqual(config.compute_device, "cpu")

    def test_missing_required_model_type_is_loud(self) -> None:
        with self.assertRaisesRegex(TypeError, "model_type"):
            build_model_train_config({"num_boost_round": 123})


if __name__ == "__main__":
    unittest.main()
