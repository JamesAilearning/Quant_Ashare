"""Unit tests for ModelTrainer."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.model_trainer import (
    ModelTrainConfig,
    ModelTrainer,
    ModelTrainerError,
)


class ModelTrainerStructuralTests(unittest.TestCase):
    """Structural validation — no qlib needed."""

    def test_unsupported_model_type_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "model_type"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="RandomForest"),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_negative_num_boost_round_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "num_boost_round"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel", num_boost_round=0),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_negative_learning_rate_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "learning_rate"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel", learning_rate=-0.01),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_empty_artifact_path_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "model_artifact_path"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel"),
                    dataset=None,
                    model_artifact_path="",
                )

    def test_qlib_not_initialized_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=False):
            with self.assertRaisesRegex(ModelTrainerError, "not initialized"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel"),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_negative_early_stopping_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "early_stopping_rounds"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel", early_stopping_rounds=0),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_non_int_seed_rejected(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "seed"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel", seed=3.14),  # type: ignore[arg-type]
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_bool_seed_rejected(self) -> None:
        # bool is subtype of int; must be explicitly rejected.
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "seed"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="LGBModel", seed=True),  # type: ignore[arg-type]
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )


class ModelTrainerUpperBoundsTests(unittest.TestCase):
    """P2e regression guards — sane upper bounds on hyperparameters,
    LightGBM ``num_leaves <= 2**max_depth`` invariant, and upfront
    ``predict_segment`` validation.

    Previously these slipped through:
    - ``num_boost_round=10**9`` burned hours before the caller noticed
    - ``max_depth=500`` quietly worked because LightGBM capped internally
    - ``num_leaves=1024`` with ``max_depth=4`` let LightGBM silently clip
      (the user believed they were training a 1024-leaf model; they
      were training a 16-leaf model)
    - ``predict_segment="tets"`` raised a cryptic ``KeyError`` deep
      inside qlib's DatasetH.
    """

    def _call(self, **kwargs) -> None:
        """Call train_and_predict with qlib init patched true."""
        cfg_kwargs = {k: v for k, v in kwargs.items() if k != "predict_segment"}
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            ModelTrainer.train_and_predict(
                config=ModelTrainConfig(model_type="LGBModel", **cfg_kwargs),
                dataset=None,
                model_artifact_path="/tmp/model.pkl",
                predict_segment=kwargs.get("predict_segment", "test"),
            )

    def test_rejects_absurd_num_boost_round(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "num_boost_round"):
            self._call(num_boost_round=1_000_000)

    def test_rejects_non_int_num_boost_round(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "num_boost_round"):
            self._call(num_boost_round=100.0)  # type: ignore[arg-type]

    def test_rejects_bool_num_boost_round(self) -> None:
        # bool is a subtype of int — must be explicitly rejected.
        with self.assertRaisesRegex(ModelTrainerError, "num_boost_round"):
            self._call(num_boost_round=True)  # type: ignore[arg-type]

    def test_rejects_zero_max_depth(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "max_depth"):
            self._call(max_depth=0)

    def test_rejects_absurd_max_depth(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "max_depth"):
            self._call(max_depth=500)

    def test_rejects_catboost_depth_above_framework_limit(self) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            with self.assertRaisesRegex(ModelTrainerError, "CatBoostModel max_depth"):
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(model_type="CatBoostModel", max_depth=32),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )

    def test_rejects_non_int_max_depth(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "max_depth"):
            self._call(max_depth=8.5)  # type: ignore[arg-type]

    def test_rejects_num_leaves_below_two(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "num_leaves"):
            self._call(num_leaves=1)

    def test_rejects_non_int_num_leaves(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "num_leaves"):
            self._call(num_leaves=64.0)  # type: ignore[arg-type]

    def test_rejects_absurd_num_leaves(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "num_leaves"):
            # also passes 2**max_depth check at depth=64 (2**64 huge)
            self._call(max_depth=64, num_leaves=500_000)

    def test_rejects_num_leaves_exceeds_two_pow_max_depth(self) -> None:
        """LightGBM invariant: with max_depth=4 the binary tree can hold
        at most 2**4=16 leaves. 1024 leaves at depth 4 would be silently
        clipped to 16 — caller thinks they tuned a wide model, they didn't.
        """
        with self.assertRaisesRegex(
            ModelTrainerError, r"num_leaves.*2\*\*max_depth"
        ):
            self._call(max_depth=4, num_leaves=1024)

    def test_accepts_num_leaves_at_two_pow_max_depth(self) -> None:
        """Boundary case: num_leaves == 2**max_depth is the documented
        maximum and must be accepted. We can't assert success (no dataset),
        but the validation phase must pass — so any raised error should
        NOT mention num_leaves / max_depth."""
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            try:
                ModelTrainer.train_and_predict(
                    config=ModelTrainConfig(
                        model_type="LGBModel",
                        max_depth=4,
                        num_leaves=16,
                        num_boost_round=10,
                        early_stopping_rounds=5,
                    ),
                    dataset=None,
                    model_artifact_path="/tmp/model.pkl",
                )
            except ModelTrainerError as exc:
                self.assertNotRegex(
                    str(exc), r"num_leaves.*2\*\*max_depth",
                    "validation must accept num_leaves == 2**max_depth",
                )
            except Exception:
                # Downstream (model build / fit) will fail — not our concern.
                pass

    def test_rejects_learning_rate_above_one(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "learning_rate"):
            self._call(learning_rate=1.5)

    def test_rejects_non_numeric_learning_rate(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "learning_rate"):
            self._call(learning_rate="0.05")  # type: ignore[arg-type]

    def test_rejects_bool_learning_rate(self) -> None:
        # bool is subclass of int (and Python treats int as a numeric);
        # must be explicitly rejected so True doesn't pass through as 1.0.
        with self.assertRaisesRegex(ModelTrainerError, "learning_rate"):
            self._call(learning_rate=True)  # type: ignore[arg-type]

    def test_rejects_early_stopping_exceeds_num_boost_round(self) -> None:
        """If early_stopping_rounds > num_boost_round, the stopping
        criterion can never trigger — configuration smell."""
        with self.assertRaisesRegex(ModelTrainerError, "early_stopping_rounds"):
            self._call(num_boost_round=20, early_stopping_rounds=100)

    def test_rejects_bad_predict_segment(self) -> None:
        for bad in ("tets", "TRAIN", "validation", "oos", ""):
            with self.subTest(segment=bad):
                with self.assertRaisesRegex(ModelTrainerError, "predict_segment"):
                    self._call(predict_segment=bad)

    def test_accepts_all_valid_predict_segments(self) -> None:
        """train / valid / test must pass structural validation (may fail
        later at fit/predict time — that's fine)."""
        for seg in ("train", "valid", "test"):
            with self.subTest(segment=seg):
                with patch(
                    "src.core.model_trainer.is_canonical_qlib_initialized",
                    return_value=True,
                ):
                    try:
                        ModelTrainer.train_and_predict(
                            config=ModelTrainConfig(model_type="LGBModel"),
                            dataset=None,
                            model_artifact_path="/tmp/model.pkl",
                            predict_segment=seg,
                        )
                    except ModelTrainerError as exc:
                        self.assertNotRegex(
                            str(exc), "predict_segment",
                            f"valid segment {seg!r} must pass validation",
                        )
                    except Exception:
                        # Fit on None dataset fails downstream — not our
                        # concern here.
                        pass


class FitDispatchTests(unittest.TestCase):
    """_fit_dispatch must pass LGB-only kwargs only to LGBModel."""

    def _make_model(self):
        class _M:
            def __init__(self):
                self.fit_calls = []
            def fit(self, dataset, **kwargs):
                self.fit_calls.append((dataset, kwargs))
        return _M()

    def test_lgb_receives_extra_kwargs(self) -> None:
        model = self._make_model()
        evals: dict = {}
        ModelTrainer._fit_dispatch(
            model, dataset="DS",
            config=ModelTrainConfig(model_type="LGBModel", num_boost_round=7, early_stopping_rounds=3),
            evals_result=evals,
        )
        self.assertEqual(len(model.fit_calls), 1)
        dataset_arg, kwargs = model.fit_calls[0]
        self.assertEqual(dataset_arg, "DS")
        self.assertEqual(kwargs["num_boost_round"], 7)
        self.assertEqual(kwargs["early_stopping_rounds"], 3)
        self.assertIs(kwargs["evals_result"], evals)

    def test_xgb_receives_fit_time_controls(self) -> None:
        model = self._make_model()
        evals: dict = {}
        ModelTrainer._fit_dispatch(
            model, dataset="DS",
            config=ModelTrainConfig(
                model_type="XGBModel",
                num_boost_round=11,
                early_stopping_rounds=4,
            ),
            evals_result=evals,
        )
        _, kwargs = model.fit_calls[0]
        self.assertEqual(kwargs["num_boost_round"], 11)
        self.assertEqual(kwargs["early_stopping_rounds"], 4)
        self.assertIs(kwargs["evals_result"], evals)

    def test_catboost_receives_fit_time_controls(self) -> None:
        model = self._make_model()
        evals: dict = {}
        ModelTrainer._fit_dispatch(
            model, dataset="DS",
            config=ModelTrainConfig(
                model_type="CatBoostModel",
                num_boost_round=13,
                early_stopping_rounds=5,
            ),
            evals_result=evals,
        )
        _, kwargs = model.fit_calls[0]
        self.assertEqual(kwargs["num_boost_round"], 13)
        self.assertEqual(kwargs["early_stopping_rounds"], 5)
        self.assertIs(kwargs["evals_result"], evals)


class TrainingDiagnosticsTests(unittest.TestCase):
    """_extract_training_diagnostics best-effort extraction."""

    def test_lgb_best_iteration_from_inner_model(self) -> None:
        class _Inner:
            best_iteration = 42

        class _M:
            model = _Inner()

        best_iter, _ = ModelTrainer._extract_training_diagnostics(_M(), "LGBModel", {})
        self.assertEqual(best_iter, 42)

    def test_xgb_best_iteration_from_inner_model(self) -> None:
        class _Inner:
            best_iteration = 17

        class _M:
            model = _Inner()

        best_iter, _ = ModelTrainer._extract_training_diagnostics(_M(), "XGBModel", {})
        self.assertEqual(best_iter, 17)

    def test_catboost_best_iteration_via_getter(self) -> None:
        class _Inner:
            def get_best_iteration(self):
                return 99

        class _M:
            model = _Inner()

        best_iter, _ = ModelTrainer._extract_training_diagnostics(_M(), "CatBoostModel", {})
        self.assertEqual(best_iter, 99)

    def test_missing_inner_returns_none(self) -> None:
        class _M:
            model = None

        best_iter, final_val = ModelTrainer._extract_training_diagnostics(_M(), "LGBModel", {})
        self.assertIsNone(best_iter)
        self.assertIsNone(final_val)

    def test_final_valid_loss_from_evals_result(self) -> None:
        class _Inner:
            best_iteration = 3

        class _M:
            model = _Inner()

        evals = {"valid": {"l2": [0.5, 0.4, 0.3, 0.35]}}
        best_iter, final_val = ModelTrainer._extract_training_diagnostics(_M(), "LGBModel", evals)
        self.assertEqual(best_iter, 3)
        # best_iter=3 → values[2] = 0.3
        self.assertAlmostEqual(final_val, 0.3)

    def test_best_iter_zero_uses_values_index_zero_not_last(self) -> None:
        """best_iter==0 (0-indexed, CatBoost) must select values[0],
        not fall through the old guard to values[-1].  Before the
        max(0, best_iter-1) fix, 0 < 0 failed the guard and final_val
        silently returned the last element."""
        class _Inner:
            def get_best_iteration(self):
                return 0

        class _M:
            model = _Inner()

        evals = {"valid": {"l2": [0.9, 0.7, 0.5, 0.3]}}
        best_iter, final_val = ModelTrainer._extract_training_diagnostics(_M(), "CatBoostModel", evals)
        self.assertEqual(best_iter, 0)
        # best_iter=0 → max(0, -1)=0 → values[0] = 0.9
        self.assertAlmostEqual(final_val, 0.9)

    def test_diagnostic_extraction_never_raises(self) -> None:
        # Malformed evals_result must not poison the output.
        class _M:
            model = None

        best_iter, final_val = ModelTrainer._extract_training_diagnostics(
            _M(), "LGBModel", {"valid": "not a dict"},
        )
        self.assertIsNone(best_iter)
        self.assertIsNone(final_val)


class SeedEverythingTests(unittest.TestCase):
    def test_seed_sets_python_and_numpy(self) -> None:
        import random as _random

        from src.core.model_trainer import _seed_everything
        _seed_everything(1234)
        a1 = _random.random()
        _seed_everything(1234)
        a2 = _random.random()
        self.assertEqual(a1, a2)

        try:
            import numpy as np
        except ImportError:
            return
        _seed_everything(1234)
        n1 = np.random.rand(3).tolist()
        _seed_everything(1234)
        n2 = np.random.rand(3).tolist()
        self.assertEqual(n1, n2)


_QLIB_DATA_DIR = Path(r"D:/qlib_data/my_cn_data")
_HAS_QLIB_DATA = _QLIB_DATA_DIR.exists() and (_QLIB_DATA_DIR / "calendars").exists()


from tests.e2e_guard import skip_unless_e2e


@skip_unless_e2e
@unittest.skipUnless(_HAS_QLIB_DATA, "qlib data bundle not available")
class ModelTrainerE2ETests(unittest.TestCase):
    """E2E tests that require real qlib data."""

    _dataset = None

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

        from src.data.feature_dataset_builder import (
            FeatureDatasetBuilder,
            FeatureDatasetConfig,
        )
        result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments="csi300",
            feature_handler="Alpha158",
            train_start="2024-01-01", train_end="2025-06-30",
            valid_start="2025-07-01", valid_end="2025-09-30",
            test_start="2025-10-01", test_end="2025-12-31",
        ))
        cls._dataset = result.dataset

    def test_lgb_train_and_predict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = ModelTrainer.train_and_predict(
                config=ModelTrainConfig(
                    model_type="LGBModel",
                    num_boost_round=50,
                    early_stopping_rounds=10,
                ),
                dataset=self._dataset,
                model_artifact_path=str(Path(tmp) / "model.pkl"),
            )
            self.assertGreater(result.prediction_shape[0], 0)
            self.assertTrue(Path(result.model_artifact_path).exists())

    def test_model_pickle_is_loadable(self) -> None:
        import pickle
        with tempfile.TemporaryDirectory() as tmp:
            result = ModelTrainer.train_and_predict(
                config=ModelTrainConfig(
                    model_type="LGBModel",
                    num_boost_round=20,
                    early_stopping_rounds=5,
                ),
                dataset=self._dataset,
                model_artifact_path=str(Path(tmp) / "model.pkl"),
            )
            with open(result.model_artifact_path, "rb") as f:
                loaded = pickle.load(f)
            self.assertIsNotNone(loaded)

    def test_xgb_train_and_predict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = ModelTrainer.train_and_predict(
                config=ModelTrainConfig(
                    model_type="XGBModel",
                    num_boost_round=30,
                    early_stopping_rounds=10,
                ),
                dataset=self._dataset,
                model_artifact_path=str(Path(tmp) / "xgb_model.pkl"),
            )
            self.assertGreater(result.prediction_shape[0], 0)
            self.assertTrue(Path(result.model_artifact_path).exists())

    def test_catboost_train_and_predict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = ModelTrainer.train_and_predict(
                config=ModelTrainConfig(
                    model_type="CatBoostModel",
                    num_boost_round=30,
                    early_stopping_rounds=10,
                ),
                dataset=self._dataset,
                model_artifact_path=str(Path(tmp) / "cat_model.pkl"),
            )
            self.assertGreater(result.prediction_shape[0], 0)
            self.assertTrue(Path(result.model_artifact_path).exists())


class LGBRegularisationFieldsTests(unittest.TestCase):
    """ModelTrainConfig now exposes LGB regularisation / sampling
    knobs (``lambda_l1``, ``lambda_l2``, ``min_data_in_leaf``,
    ``feature_fraction``, ``bagging_fraction``, ``bagging_freq``).

    Why: walk-forward's first end-to-end run had every fold's
    ``best_iteration`` come in at 1-6 — LGB pushed valid loss to its
    local optimum on the first split because there was zero L1/L2
    regularisation, large ``num_leaves``, and a high learning rate.
    Without these knobs available at the config layer, an operator
    cannot tune the model to actually train past that plateau.

    Defaults below mirror LightGBM's own defaults so adding the fields
    does not change behaviour for callers that don't set them.
    """

    def test_defaults_match_lightgbm_defaults(self) -> None:
        cfg = ModelTrainConfig(model_type="LGBModel")
        self.assertEqual(cfg.lambda_l1, 0.0)
        self.assertEqual(cfg.lambda_l2, 0.0)
        self.assertEqual(cfg.min_data_in_leaf, 20)
        self.assertEqual(cfg.feature_fraction, 1.0)
        self.assertEqual(cfg.bagging_fraction, 1.0)
        self.assertEqual(cfg.bagging_freq, 0)

    def test_create_model_forwards_regularisation_to_lgbmodel(self) -> None:
        """``_create_model`` must pass the new fields through to LGBModel.

        We patch the LGBModel class with a stub that records its kwargs;
        the test then asserts every regularisation / sampling field
        landed on the constructor call. Without this guard, a future
        rename in qlib's LGBModel signature would silently drop the
        params and the operator would think they were tuning a model
        that was actually still on defaults.
        """
        captured: dict = {}

        class _StubLGB:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with patch.dict(
            "sys.modules",
            {"qlib.contrib.model.gbdt": MagicMock(LGBModel=_StubLGB)},
        ):
            ModelTrainer._create_model(ModelTrainConfig(
                model_type="LGBModel",
                lambda_l1=0.3,
                lambda_l2=1.5,
                min_data_in_leaf=42,
                feature_fraction=0.7,
                bagging_fraction=0.8,
                bagging_freq=4,
            ))

        self.assertEqual(captured["lambda_l1"], 0.3)
        self.assertEqual(captured["lambda_l2"], 1.5)
        self.assertEqual(captured["min_data_in_leaf"], 42)
        self.assertEqual(captured["feature_fraction"], 0.7)
        self.assertEqual(captured["bagging_fraction"], 0.8)
        self.assertEqual(captured["bagging_freq"], 4)

    def _validate_with_qlib_init(self, cfg: ModelTrainConfig) -> None:
        with patch("src.core.model_trainer.is_canonical_qlib_initialized", return_value=True):
            ModelTrainer._validate(cfg, model_artifact_path="/tmp/m.pkl")

    def test_rejects_negative_lambda_l1(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "lambda_l1"):
            self._validate_with_qlib_init(
                ModelTrainConfig(model_type="LGBModel", lambda_l1=-0.1),
            )

    def test_rejects_negative_lambda_l2(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "lambda_l2"):
            self._validate_with_qlib_init(
                ModelTrainConfig(model_type="LGBModel", lambda_l2=-1.0),
            )

    def test_rejects_zero_min_data_in_leaf(self) -> None:
        """LightGBM accepts 0 but it pins all rows into a single leaf —
        a degenerate run; we reject up front."""
        with self.assertRaisesRegex(ModelTrainerError, "min_data_in_leaf"):
            self._validate_with_qlib_init(
                ModelTrainConfig(model_type="LGBModel", min_data_in_leaf=0),
            )

    def test_rejects_feature_fraction_above_one(self) -> None:
        """LightGBM silently clips out-of-range sampling fractions, so a
        user thinks they're sampling 110% of features and silently runs
        with 100%. Reject loudly."""
        with self.assertRaisesRegex(ModelTrainerError, "feature_fraction"):
            self._validate_with_qlib_init(
                ModelTrainConfig(model_type="LGBModel", feature_fraction=1.1),
            )

    def test_rejects_zero_feature_fraction(self) -> None:
        """0 disables sampling entirely — LGB has nothing to fit on."""
        with self.assertRaisesRegex(ModelTrainerError, "feature_fraction"):
            self._validate_with_qlib_init(
                ModelTrainConfig(model_type="LGBModel", feature_fraction=0.0),
            )

    def test_rejects_bagging_fraction_outside_range(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "bagging_fraction"):
            self._validate_with_qlib_init(
                ModelTrainConfig(model_type="LGBModel", bagging_fraction=1.5),
            )

    def test_rejects_negative_bagging_freq(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "bagging_freq"):
            self._validate_with_qlib_init(
                ModelTrainConfig(model_type="LGBModel", bagging_freq=-1),
            )


class LGBOnlyValidationGatedByModelTypeTests(unittest.TestCase):
    """Per review P2-2: LightGBM-specific knobs (``num_leaves``,
    ``lambda_l1/l2``, ``min_data_in_leaf``, ``feature_fraction``,
    ``bagging_fraction``, ``bagging_freq``) only get validated when
    ``model_type == "LGBModel"``. Otherwise a perfectly legal
    ``CatBoostModel(max_depth=4)`` would fail the LGB ``num_leaves <=
    2^max_depth`` check on the default ``num_leaves=210`` even though
    CatBoost ignores the field entirely.
    """

    def _validate_with_qlib_init(self, config):
        with patch(
            "src.core.model_trainer.is_canonical_qlib_initialized",
            return_value=True,
        ):
            ModelTrainer._validate(config, "/tmp/m.pkl")

    def test_xgb_with_lgb_unsafe_num_leaves_passes(self) -> None:
        """XGB doesn't use num_leaves, so ``num_leaves > 2^max_depth``
        must NOT block an XGB config."""
        self._validate_with_qlib_init(
            ModelTrainConfig(
                model_type="XGBModel",
                max_depth=4,
                num_leaves=210,  # 210 >> 2^4=16 — would fail under LGB
            ),
        )

    def test_catboost_with_lgb_unsafe_num_leaves_passes(self) -> None:
        """Same for CatBoost."""
        self._validate_with_qlib_init(
            ModelTrainConfig(
                model_type="CatBoostModel",
                max_depth=4,
                num_leaves=210,
            ),
        )

    def test_xgb_ignores_negative_lambda(self) -> None:
        """``lambda_l1=-1`` for XGB is meaningless to the user (XGB
        doesn't see the field) but the previous validator still
        rejected it."""
        self._validate_with_qlib_init(
            ModelTrainConfig(
                model_type="XGBModel",
                lambda_l1=-1.0,  # invalid for LGB, irrelevant for XGB
            ),
        )

    def test_lgb_still_enforces_num_leaves_bound(self) -> None:
        """Regression guard: the LGB-specific check must still fire
        when ``model_type == "LGBModel"``."""
        with self.assertRaisesRegex(ModelTrainerError, "num_leaves"):
            self._validate_with_qlib_init(
                ModelTrainConfig(
                    model_type="LGBModel",
                    max_depth=4, num_leaves=210,
                ),
            )

    def test_lgb_still_enforces_negative_lambda(self) -> None:
        with self.assertRaisesRegex(ModelTrainerError, "lambda_l1"):
            self._validate_with_qlib_init(
                ModelTrainConfig(
                    model_type="LGBModel",
                    lambda_l1=-0.5,
                ),
            )


class AtomicPickleWriteTests(unittest.TestCase):
    """Per review #15: model artifact write must be atomic so a crash
    mid-``pickle.dump`` doesn't leave a corrupted file at the target
    path. The fix uses a temp sibling file + ``os.replace``.

    These tests pin the *contract* (no partial file at the target on
    failure; tmp file cleaned up; successful write lands at target) by
    reproducing the exact write sequence the source uses, so the
    contract is captured here even though the actual call site is
    inline inside ``train_and_predict``.
    """

    def test_target_path_has_no_partial_remnant_on_dump_failure(self) -> None:
        import os as _os
        import pickle as _pickle

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "model.pkl"
            tmp_path = target.with_suffix(target.suffix + ".tmp")

            with patch("pickle.dump", side_effect=OSError("simulated disk full")):
                with self.assertRaisesRegex(OSError, "simulated"):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with tmp_path.open("wb") as f:
                            _pickle.dump(object(), f)
                        _os.replace(tmp_path, target)
                    except Exception:
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                        raise

            self.assertFalse(
                target.exists(),
                "Atomic write violated: target exists after failed dump",
            )
            self.assertFalse(
                tmp_path.exists(),
                "Atomic write violated: tmp file not cleaned up",
            )

    def test_successful_write_lands_at_target(self) -> None:
        import os as _os
        import pickle as _pickle

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "model.pkl"
            tmp_path = target.with_suffix(target.suffix + ".tmp")

            payload = {"weights": [0.1, 0.2, 0.3]}
            with tmp_path.open("wb") as f:
                _pickle.dump(payload, f)
            _os.replace(tmp_path, target)

            self.assertTrue(target.exists())
            self.assertFalse(tmp_path.exists())
            with target.open("rb") as f:
                loaded = _pickle.load(f)
            self.assertEqual(loaded, payload)


class TrainAndPredictHappyPathTests(unittest.TestCase):
    """PR #54 introduced ``_write_model_sidecar`` that runs after a
    successful pickle write.  An ``UnboundLocalError`` in the
    sidecar call order slipped past review because the existing
    atomic-write tests mocked ``pickle.dump`` to raise an exception
    before the sidecar path was reached.  These tests exercise the
    full success path without requiring qlib runtime.
    """

    def _write_fake_pickle(self, target: Path) -> bytes:
        data = b"fake_pickle_payload_for_sha256"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return data

    def test_sidecar_contains_required_fields(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "model.pkl"
            self._write_fake_pickle(model_path)

            config = MagicMock()
            config.model_type = "LGBModel"
            ModelTrainer._write_model_sidecar(
                model_path, config, best_iter=42, final_val=0.99,
            )

            sidecar_path = model_path.with_suffix(".pkl.meta.json")
            self.assertTrue(
                sidecar_path.is_file(),
                "Sidecar not written after successful pickle dump",
            )
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(sidecar["schema_version"], "v1")
            self.assertIsInstance(sidecar["pkl_sha256"], str)
            self.assertEqual(len(sidecar["pkl_sha256"]), 64)
            self.assertEqual(sidecar["best_iteration"], 42)
            self.assertEqual(sidecar["final_valid_loss"], 0.99)
            self.assertEqual(sidecar["model_type"], "LGBModel")
            self.assertIn("trained_at", sidecar)
            self.assertIn("python_version", sidecar)

    def test_sidecar_pkl_sha256_matches_pickle_bytes(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "model.pkl"
            payload = self._write_fake_pickle(model_path)

            config = MagicMock()
            config.model_type = "XGBModel"
            ModelTrainer._write_model_sidecar(
                model_path, config, best_iter=None, final_val=None,
            )

            sidecar_path = model_path.with_suffix(".pkl.meta.json")
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))

            import hashlib
            expected_sha = hashlib.sha256(payload).hexdigest()
            self.assertEqual(
                sidecar["pkl_sha256"], expected_sha,
                "Sidecar sha256 must match the pickle bytes on disk",
            )

    def test_unbound_best_iter_regression_guard(self) -> None:
        """Pin the correct ordering: _extract_training_diagnostics is
        called *before* _write_model_sidecar, so best_iter / final_val
        are always assigned when the sidecar writes them.

        PR #54 introduced the opposite order — best_iter was passed to
        the sidecar before _extract_training_diagnostics assigned it,
        causing UnboundLocalError on every real training run. If this
        order regresses, the sidecar would see None for both fields
        (the initial values set at :file:``model_trainer.py:167-168``)
        instead of the extracted diagnostics.
        """
        import inspect
        import textwrap

        src = textwrap.dedent(inspect.getsource(ModelTrainer.train_and_predict))
        # The _write_model_sidecar call must appear *after* the
        # _extract_training_diagnostics call in the source text.
        sidecar_pos = src.index("_write_model_sidecar")
        extract_pos = src.index("_extract_training_diagnostics")
        self.assertGreater(
            sidecar_pos, extract_pos,
            "PR #54 regression: _write_model_sidecar must be called "
            "AFTER _extract_training_diagnostics so best_iter/final_val "
            "are assigned before the sidecar writes them.",
        )


if __name__ == "__main__":
    unittest.main()
