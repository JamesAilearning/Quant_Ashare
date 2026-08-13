"""Governance pins for PV-DP-7 steps 2-4 (promotion paired comparison).

The signed change is
``openspec/changes/2026-08-12-pv-promotion-paired-run``. These pins make
any drift in the promotion criterion, the paired geometry, or the
adjudication plan a red test instead of a silently different experiment.
"""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_PRESET_DIR = _PROJECT_ROOT / "config" / "presets"
_BASELINE_PRESET = _PRESET_DIR / "pv_promo_paired_baseline.yaml"
_TREATMENT_PRESET = _PRESET_DIR / "pv_promo_paired_treatment.yaml"
_PLAN = _PROJECT_ROOT / "docs" / "prereg" / "pv_promotion_paired.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class PairedPresetPins(unittest.TestCase):
    def test_arms_differ_only_in_feature_handler(self) -> None:
        # The whole point of a paired comparison: one difference. A
        # second drifted key (a different universe, benchmark, slippage,
        # window) would make the verdict measure something other than
        # the registered hypothesis.
        base, treat = _load(_BASELINE_PRESET), _load(_TREATMENT_PRESET)
        differing = {
            k for k in set(base) | set(treat)
            if base.get(k) != treat.get(k)
        }
        self.assertEqual({"feature_handler", "output_dir"}, differing)
        self.assertEqual("Alpha158", base["feature_handler"])
        self.assertEqual("Alpha158PlusMined", treat["feature_handler"])

    def test_decision_window_is_oos_dev_only(self) -> None:
        # overall_start + train + valid MUST land the first test window
        # exactly on the OOS dev start: the GP saw the IS window, so an
        # IS test fold would score in-sample leakage as improvement.
        # Derived from the parent config's own window months, not
        # restated — a parent change that moves the geometry turns this
        # red instead of silently re-admitting IS folds.
        parent = _load(_PROJECT_ROOT / "config_walk.yaml")
        campaign_plan = _load(
            _PROJECT_ROOT / "docs" / "prereg" / "pv_incremental.yaml")
        oos_start = campaign_plan["windows"]["oos_start"]
        oos_end = campaign_plan["windows"]["oos_end"]
        for preset_path in (_BASELINE_PRESET, _TREATMENT_PRESET):
            preset = _load(preset_path)
            with self.subTest(preset=preset_path.name):
                months = (int(parent["train_months"])
                          + int(parent["valid_months"]))
                start = preset["overall_start"]
                y, m, d = (int(x) for x in start.split("-"))
                total = (y * 12 + (m - 1)) + months
                first_test = f"{total // 12:04d}-{total % 12 + 1:02d}-{d:02d}"
                self.assertEqual(oos_start, first_test)
                # Sacred rule: the holdout year is never trained into.
                self.assertEqual(oos_end, preset["overall_end"])

    def test_arms_are_canonical_metric_grade(self) -> None:
        # Net-basis improvement is the criterion here, so both arms must
        # produce RAISE-validated official returns. Declaring either
        # escape hatch would make the compared numbers non-canonical —
        # the exact defect E003/E004 disclosed on the baseline run.
        for preset_path in (_BASELINE_PRESET, _TREATMENT_PRESET):
            preset = _load(preset_path)
            with self.subTest(preset=preset_path.name):
                self.assertNotIn("risk_constraints_mode", preset)
                self.assertNotIn("metrics_purpose", preset)

    def test_prereg_plan_registers_exactly_one_variant(self) -> None:
        plan = _load(_PLAN)
        self.assertEqual(["alpha158-plus-pv001"], plan["treatments"])
        self.assertEqual("treatment_better", plan["expected_direction"])
        self.assertTrue(plan["hypothesis"].strip())
        self.assertTrue(plan["baseline"].strip())

    def test_prereg_plan_satisfies_the_rulers_schema(self) -> None:
        # A plan the ruler rejects would only surface at adjudication
        # time, after both arms have been paid for. Drive the ruler's
        # OWN loader so the schema cannot drift apart from it.
        #
        # The one error this tolerates is the uncommitted-plan refusal:
        # that gate is about WHEN the plan was committed relative to the
        # runs (enforced for real at adjudication), and treating it as a
        # test failure would make this red on any working tree that has
        # the plan staged — a test that is red locally and green in CI
        # teaches people to ignore it. Every OTHER refusal, including
        # every schema refusal, still fails here.
        from src.core.preregistration import (
            EXPECTED_DIRECTIONS,
            PreregistrationError,
            load_plan,
        )

        self.assertIn(_load(_PLAN)["expected_direction"], EXPECTED_DIRECTIONS)
        try:
            loaded = load_plan(_PLAN)
        except PreregistrationError as exc:
            self.assertIn("UNCOMMITTED", str(exc),
                          f"plan rejected by the ruler: {exc}")
        else:
            self.assertIn("alpha158-plus-pv001", loaded.treatments)


class RepresentativePromotionPins(unittest.TestCase):
    def test_promotion_tool_does_not_use_the_v1_gate(self) -> None:
        # src/factor_mining/promote.py carries the v1/D4 ValidationCriteria
        # (min_oos_ir 0.3 etc.). Running the FWER survivor through it
        # would put a second, unsigned judge in the promotion path — and
        # this family's OOS IR would fail it.
        #
        # Walk the AST rather than grep the text: the module DOCUMENTS
        # why it avoids that flow, and a substring pin would fire on its
        # own prose while a real `import ... as p` could still slip past
        # a differently-spelled search.
        import ast

        import scripts.research.pv_incremental_promote_representative as mod

        tree = ast.parse(inspect.getsource(mod))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        offenders = sorted(
            m for m in imported
            if m == "src.factor_mining.promote"
            or m.startswith("src.factor_mining.promote.")
        )
        self.assertEqual([], offenders)
        self.assertNotIn("ValidationCriteria",
                         {m.rsplit(".", 1)[-1] for m in imported})

    def test_survivorship_is_the_only_criterion(self) -> None:
        # The gate reads the verdict's survivor list — no pool-metric
        # threshold may appear alongside it.
        import scripts.research.pv_incremental_promote_representative as mod

        src = inspect.getsource(mod.select_registered_candidate)
        self.assertIn("survivors", src)
        for forbidden in ("min_oos_ir", "min_rank_ic", "fitness >"):
            self.assertNotIn(forbidden, src)


class CombinedHandlerPins(unittest.TestCase):
    def test_combined_handler_is_registered_under_its_frozen_name(self) -> None:
        from src.data.mined_factor_handler import (
            ALPHA158_PLUS_MINED_HANDLER_NAME,
        )

        self.assertEqual("Alpha158PlusMined",
                         ALPHA158_PLUS_MINED_HANDLER_NAME)
        treat = _load(_TREATMENT_PRESET)
        self.assertEqual(ALPHA158_PLUS_MINED_HANDLER_NAME,
                         treat["feature_handler"])

    def test_combined_handler_reuses_alpha158_label_and_processors(self) -> None:
        # Both arms MUST carry the same label. The combined handler
        # therefore constructs a real Alpha158 (its processors and label
        # come from qlib itself) and only composes the LOADER — it must
        # never rebuild the label or the handler class.
        from src.data import mined_factor_handler as mod

        src = inspect.getsource(mod._make_alpha158_plus_mined_qlib_handler)
        self.assertIn("Alpha158(", src)
        self.assertIn("alpha158_label_expression", src)
        self.assertIn("NestedDataLoader", src)
        # join="left" keeps the BASELINE arm's row set: a mined-side
        # instrument-date the baseline lacks must not enter.
        self.assertIn('join="left"', src)

    def test_combined_handler_cache_identity_composes_both_sides(self) -> None:
        from src.data import mined_factor_handler as mod

        src = inspect.getsource(mod.register_alpha158_plus_mined_handler)
        self.assertIn("alpha158_default", src)
        self.assertIn("_compute_bundle_cache_identity", src)


if __name__ == "__main__":
    unittest.main()
