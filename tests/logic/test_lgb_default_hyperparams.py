"""Guard tests for the tuned LGB hyperparameter defaults (C2-c).

These complement the per-dataclass literal-value checks in
``test_model_trainer`` / ``test_pipeline`` / ``test_model_config_projection``
with two *structural* invariants. They guard DIFFERENT properties — do
not conflate them:

1. Defaults are identical across the three dataclasses
   (``ModelTrainConfig`` / ``PipelineConfig`` / ``WalkForwardConfig``) —
   a future edit can't fix one and forget the others.

2. Division of labor around ``config_walk.yaml`` (the config that drives
   the RUN_E2E walk-forward aggregate baseline, IR ~0.301):

   * NO-DRIFT guard = config_walk overrides *every* model field
     explicitly. THIS is what makes the baseline immune to a default
     change: a field hardcoded in config_walk ignores its default, so
     changing the default cannot move config_walk's resolved value, so
     the baseline cannot drift. ->
     ``test_config_walk_overrides_every_model_field_so_baseline_cannot_drift``.

   * PRESET-FIX check = the new defaults equal config_walk's values.
     This confirms under-specified presets (default / production /
     my_preset1) now inherit the *right* tuned values. It is NOT a
     no-drift proof: for any field where config_walk relies on the
     default, both sides take the same default and the equality is
     trivially true, so it cannot detect drift there. ->
     ``test_default_values_equal_config_walk_values``.

   Consequence: if someone later makes config_walk rely on a (changed)
   default, the PRESET-FIX check stays green; only the NO-DRIFT guard
   catches it. Do NOT weaken the NO-DRIFT guard on the assumption that
   the PRESET-FIX check covers it.

The config_walk comparison is on the model-hyperparameter subset only
(``_MODEL_HYPERPARAM_FIELDS``); ``compute_device`` / ``seed`` are
environment/repro fields config_walk does not override (correct
defaults cpu / 42), and forcing them into the equality would false-fail
and could invite a wrong "set the default to gpu" fix.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from src.core.model_config_projection import build_model_train_config
from src.core.pipeline import PipelineConfig
from src.core.walk_forward import WalkForwardConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_WALK = PROJECT_ROOT / "config_walk.yaml"

# The model knobs ``config_walk.yaml`` sets explicitly. Excludes
# ``compute_device`` / ``seed`` (environment/repro fields, defaulted on
# both sides — see module docstring).
_MODEL_HYPERPARAM_FIELDS = (
    "model_type",
    "num_boost_round",
    "early_stopping_rounds",
    "learning_rate",
    "max_depth",
    "num_leaves",
    "lambda_l1",
    "lambda_l2",
    "min_data_in_leaf",
    "feature_fraction",
    "bagging_fraction",
    "bagging_freq",
)


class DefaultsConsistentAcrossDataclassesTests(unittest.TestCase):
    def test_model_defaults_identical_across_the_three_dataclasses(self) -> None:
        from_model = build_model_train_config({"model_type": "LGBModel"})
        from_pipeline = build_model_train_config(
            PipelineConfig(provider_uri="/tmp/fake")
        )
        from_walk = build_model_train_config(WalkForwardConfig())

        self.assertEqual(from_model, from_pipeline)
        self.assertEqual(from_model, from_walk)


class ConfigWalkNoDriftAndPresetFixTests(unittest.TestCase):
    """Two guards around ``config_walk.yaml`` (the RUN_E2E WF baseline
    driver). They protect DIFFERENT properties — see the module
    docstring for the full division of labor:

    * NO-DRIFT guard: config_walk overrides every model field, so the
      baseline is immune to default changes.
    * PRESET-FIX check: new defaults == config_walk's values, so
      under-specified presets inherit the tuned set.
    """

    def _load_config_walk(self) -> dict:
        if not CONFIG_WALK.is_file():
            self.skipTest(f"{CONFIG_WALK} not found.")
        raw = yaml.safe_load(CONFIG_WALK.read_text(encoding="utf-8"))
        self.assertIsInstance(raw, dict)
        return raw

    def test_config_walk_overrides_every_model_field_so_baseline_cannot_drift(
        self,
    ) -> None:
        """NO-DRIFT guard. config_walk.yaml drives the RUN_E2E WF
        aggregate baseline (IR ~0.301). As long as it overrides every
        model field explicitly, changing a default cannot move its
        resolved value, so the baseline cannot silently drift. If a
        future edit drops one of these keys, config_walk starts relying
        on that default and this guard fails (the PRESET-FIX check would
        NOT — it is trivially green there). Reads config_walk's own keys
        directly: valid because config_walk is a base config with no
        ``extends``.
        """
        raw = self._load_config_walk()
        missing = [f for f in _MODEL_HYPERPARAM_FIELDS if f not in raw]
        self.assertEqual(
            missing,
            [],
            f"config_walk.yaml no longer sets these model fields "
            f"explicitly: {missing}. config_walk would start relying on "
            "their (changeable) defaults, so the RUN_E2E walk-forward "
            "baseline could silently drift on a future default change. "
            "Re-add the explicit overrides, or regenerate the baseline "
            "per tests/regression/fixtures/README.md in the same PR.",
        )

    def test_default_values_equal_config_walk_values(self) -> None:
        """PRESET-FIX check (NOT a no-drift proof). Confirms the new
        defaults equal config_walk's tuned values on the model-knob
        subset, so under-specified presets (default / production /
        my_preset1) inherit the right values. For any field where
        config_walk relies on the default this is trivially true and
        would NOT catch drift — that is the NO-DRIFT guard's job.
        """
        raw = self._load_config_walk()
        from_walk = build_model_train_config(raw)
        from_default = build_model_train_config(WalkForwardConfig())

        mismatches = {
            field: (getattr(from_walk, field), getattr(from_default, field))
            for field in _MODEL_HYPERPARAM_FIELDS
            if getattr(from_walk, field) != getattr(from_default, field)
        }
        self.assertEqual(
            mismatches,
            {},
            "New defaults differ from config_walk.yaml's tuned values "
            "(field: (config_walk, default)). Under-specified presets "
            "would inherit values that disagree with the canonical tuned "
            "set — realign the dataclass defaults to config_walk.",
        )


if __name__ == "__main__":
    unittest.main()
