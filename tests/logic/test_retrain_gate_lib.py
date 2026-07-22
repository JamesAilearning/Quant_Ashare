"""Per-retrain gate acceptance tests (PR-B', codex #389 r10/r11: every
gate needs at least one REFUSAL state pinned, or an implementation
could silently weaken a gate while still satisfying the checklist).

Coverage matrix (>=1 refusal per gate + the all-pass state + artifact
content pins):

  (a) trainer integrity — non-dict sidecar / missing num_boost_round
      (fail-closed, legacy sidecars included) / non-finite valid loss /
      missing best_iteration / EARLY-STOP BOUNDARY
      (best_iteration == num_boost_round, codex #389 r11/r12).
  (d) IC direction     — ic <= 0 / NaN.
  (b) degeneracy       — nonzero degenerate / nonzero straddle /
      corrupted counts.
  (c) constraint       — any veto record fails; only None passes.
  (e) serving vetoes   — veto2 share over / veto5 weight over /
      veto5 unknown over / veto3 ratio over / corrupted anchor /
      NaN measurement / undefined share cannot-trigger note.
  artifact             — all-pass PASS / any-fail FAIL / missing gate
      FAIL / wrong-scope gates refused / schema + verdict pins.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.retrain_gate_lib import (  # noqa: E402
    FAIL,
    GATE_SCHEMA_VERSION,
    PASS,
    SCOPE_ENSEMBLE,
    SCOPE_MEMBER,
    assemble_gate_artifact,
    gate_constraint_dry_run,
    gate_degeneracy,
    gate_ic_direction,
    gate_serving_veto,
    gate_trainer_integrity,
)


def _good_sidecar() -> dict:
    return {"schema_version": "v1", "model_type": "LGBModel",
            "best_iteration": 555, "num_boost_round": 1000,
            "final_valid_loss": 0.1234, "pkl_sha256": "ab" * 32}


class TrainerIntegrityGate(unittest.TestCase):
    def test_good_sidecar_passes(self) -> None:
        block = gate_trainer_integrity(_good_sidecar())
        self.assertEqual(PASS, block["verdict"])
        self.assertEqual([], block["reasons"])
        self.assertEqual(555, block["best_iteration"])
        self.assertEqual(1000, block["num_boost_round"])

    def test_non_dict_sidecar_fails_closed(self) -> None:
        for bad in (None, "corrupt", ["x"]):
            block = gate_trainer_integrity(bad)
            self.assertEqual(FAIL, block["verdict"], bad)

    def test_missing_num_boost_round_fails_closed(self) -> None:
        # codex #389 r18: legacy sidecars without the field fail the
        # SAME way — never a preset-default fallback.
        sidecar = _good_sidecar()
        del sidecar["num_boost_round"]
        block = gate_trainer_integrity(sidecar)
        self.assertEqual(FAIL, block["verdict"])
        self.assertTrue(
            any("num_boost_round" in r for r in block["reasons"]))

    def test_early_stop_boundary_fails(self) -> None:
        # codex #389 r11/r12: best_iteration == num_boost_round means
        # early stopping NEVER fired — training budget exhausted.
        sidecar = _good_sidecar()
        sidecar["best_iteration"] = sidecar["num_boost_round"]
        block = gate_trainer_integrity(sidecar)
        self.assertEqual(FAIL, block["verdict"])
        self.assertTrue(
            any("early" in r and "budget" in r
                for r in block["reasons"]))

    def test_missing_best_iteration_fails(self) -> None:
        sidecar = _good_sidecar()
        sidecar["best_iteration"] = None
        self.assertEqual(
            FAIL, gate_trainer_integrity(sidecar)["verdict"])

    def test_best_iteration_beyond_budget_fails(self) -> None:
        # A best iteration BEYOND num_boost_round cannot come from a
        # real run — internally inconsistent sidecar (adversarial
        # self-review).
        sidecar = _good_sidecar()
        sidecar["best_iteration"] = sidecar["num_boost_round"] + 1
        block = gate_trainer_integrity(sidecar)
        self.assertEqual(FAIL, block["verdict"])
        self.assertTrue(
            any("inconsistent" in r for r in block["reasons"]))

    def test_non_finite_valid_loss_fails(self) -> None:
        for bad in (float("nan"), float("inf"), None, "0.1"):
            sidecar = _good_sidecar()
            sidecar["final_valid_loss"] = bad
            self.assertEqual(
                FAIL, gate_trainer_integrity(sidecar)["verdict"], bad)

    def test_bool_typed_fields_fail(self) -> None:
        sidecar = _good_sidecar()
        sidecar["best_iteration"] = True
        self.assertEqual(
            FAIL, gate_trainer_integrity(sidecar)["verdict"])


class IcDirectionGate(unittest.TestCase):
    def test_positive_ic_passes(self) -> None:
        block = gate_ic_direction(0.021)
        self.assertEqual(PASS, block["verdict"])
        self.assertAlmostEqual(0.021, block["ic_1d"])

    def test_zero_and_negative_fail(self) -> None:
        for bad in (0.0, -0.001):
            self.assertEqual(
                FAIL, gate_ic_direction(bad)["verdict"], bad)

    def test_non_finite_fails_closed(self) -> None:
        for bad in (float("nan"), float("inf"), None, "0.02", True):
            self.assertEqual(
                FAIL, gate_ic_direction(bad)["verdict"], bad)


class DegeneracyGate(unittest.TestCase):
    def test_zero_zero_passes(self) -> None:
        self.assertEqual(PASS, gate_degeneracy(0, 0)["verdict"])

    def test_nonzero_degenerate_fails(self) -> None:
        self.assertEqual(FAIL, gate_degeneracy(1, 0)["verdict"])

    def test_nonzero_straddle_fails(self) -> None:
        self.assertEqual(FAIL, gate_degeneracy(0, 2)["verdict"])

    def test_corrupted_counts_fail_closed(self) -> None:
        for bad in (None, -1, 0.5, "0", True):
            self.assertEqual(
                FAIL, gate_degeneracy(bad, 0)["verdict"], bad)
            self.assertEqual(
                FAIL, gate_degeneracy(0, bad)["verdict"], bad)


class ConstraintDryRunGate(unittest.TestCase):
    def test_none_passes(self) -> None:
        self.assertEqual(
            PASS, gate_constraint_dry_run(None)["verdict"])

    def test_any_veto_record_fails(self) -> None:
        for veto in ("RAISE: st_exposure", {"rule": "x"}, "", 0):
            block = gate_constraint_dry_run(veto)
            self.assertEqual(FAIL, block["verdict"], veto)
            self.assertEqual(veto, block["constraint_veto"])


def _good_veto_inputs() -> dict:
    return {
        "csi500_effect_share": 0.42,
        "csi500_weight": 0.45,
        "unknown_weight": 0.0,
        "dryrun_daily_mean_oneway": 0.030,
        "anchor_daily_mean_oneway": 0.029,
    }


class ServingVetoGate(unittest.TestCase):
    def test_good_numbers_pass(self) -> None:
        block = gate_serving_veto(**_good_veto_inputs())
        self.assertEqual(PASS, block["verdict"])
        self.assertAlmostEqual(0.030 / 0.029, block["turnover_ratio"])

    def test_veto2_share_at_threshold_fails(self) -> None:
        inputs = _good_veto_inputs()
        inputs["csi500_effect_share"] = 0.80   # inclusive >= trigger
        self.assertEqual(
            FAIL, gate_serving_veto(**inputs)["verdict"])

    def test_veto2_undefined_share_cannot_trigger(self) -> None:
        # Campaign semantics: gross effect sum <= 0 -> share undefined,
        # the dependence leg cannot trigger (note recorded).
        inputs = _good_veto_inputs()
        inputs["csi500_effect_share"] = None
        block = gate_serving_veto(**inputs)
        self.assertEqual(PASS, block["verdict"])
        self.assertTrue(any("undefined" in n for n in block["notes"]))

    def test_veto5_weight_over_fails(self) -> None:
        inputs = _good_veto_inputs()
        inputs["csi500_weight"] = 0.7501
        self.assertEqual(
            FAIL, gate_serving_veto(**inputs)["verdict"])

    def test_veto5_unknown_over_fails(self) -> None:
        inputs = _good_veto_inputs()
        inputs["unknown_weight"] = 0.101
        self.assertEqual(
            FAIL, gate_serving_veto(**inputs)["verdict"])

    def test_veto3_ratio_over_fails(self) -> None:
        inputs = _good_veto_inputs()
        inputs["dryrun_daily_mean_oneway"] = 0.029 * 1.5001
        self.assertEqual(
            FAIL, gate_serving_veto(**inputs)["verdict"])

    def test_veto3_ratio_at_threshold_passes(self) -> None:
        # The campaign trigger is strictly > 1.5.
        inputs = _good_veto_inputs()
        inputs["anchor_daily_mean_oneway"] = 0.02
        inputs["dryrun_daily_mean_oneway"] = 0.03
        self.assertEqual(
            PASS, gate_serving_veto(**inputs)["verdict"])

    def test_corrupted_anchor_fails_closed(self) -> None:
        for bad in (0.0, -0.01, float("nan"), None, "0.03"):
            inputs = _good_veto_inputs()
            inputs["anchor_daily_mean_oneway"] = bad
            self.assertEqual(
                FAIL, gate_serving_veto(**inputs)["verdict"], bad)

    def test_nan_measurements_fail_closed(self) -> None:
        # ``nan > threshold`` is always False — corrupted measurements
        # must never read as favorable (codex #373 r7 precedent).
        for field in ("csi500_effect_share", "csi500_weight",
                      "unknown_weight", "dryrun_daily_mean_oneway"):
            inputs = _good_veto_inputs()
            inputs[field] = float("nan")
            self.assertEqual(
                FAIL, gate_serving_veto(**inputs)["verdict"], field)


class GateArtifactAssembly(unittest.TestCase):
    def _member_gates(self, *, fail_ic: bool = False) -> dict:
        return {
            "trainer_integrity": gate_trainer_integrity(_good_sidecar()),
            "ic_direction": gate_ic_direction(-0.01 if fail_ic else 0.02),
        }

    def test_all_pass_artifact(self) -> None:
        artifact = assemble_gate_artifact(
            scope=SCOPE_MEMBER,
            gates=self._member_gates(),
            subject={"pkl_sha256": "ab" * 32},
            window={"valid_start": "2026-01-05",
                    "valid_end": "2026-03-31"},
            anchor=None,
            generated_at="2026-07-22T10:00:00+00:00",
        )
        self.assertEqual(GATE_SCHEMA_VERSION,
                         artifact["schema_version"])
        self.assertEqual(PASS, artifact["overall"])
        self.assertEqual([], artifact["missing_gates"])
        # Content pin: per-gate verdicts AND numbers are in the
        # artifact (the executor and the audit trail read them).
        self.assertEqual(
            555,
            artifact["gates"]["trainer_integrity"]["best_iteration"])
        self.assertAlmostEqual(
            0.02, artifact["gates"]["ic_direction"]["ic_1d"])

    def test_any_gate_fail_fails_overall(self) -> None:
        artifact = assemble_gate_artifact(
            scope=SCOPE_MEMBER,
            gates=self._member_gates(fail_ic=True),
            subject={"pkl_sha256": "ab" * 32},
            window=None, anchor=None,
            generated_at="2026-07-22T10:00:00+00:00",
        )
        self.assertEqual(FAIL, artifact["overall"])

    def test_missing_gate_fails_overall(self) -> None:
        # codex #389 r10: a silently thinner gate set must never read
        # as PASS.
        gates = self._member_gates()
        del gates["ic_direction"]
        artifact = assemble_gate_artifact(
            scope=SCOPE_MEMBER, gates=gates,
            subject={"pkl_sha256": "ab" * 32},
            window=None, anchor=None,
            generated_at="2026-07-22T10:00:00+00:00",
        )
        self.assertEqual(FAIL, artifact["overall"])
        self.assertEqual(["ic_direction"], artifact["missing_gates"])

    def test_wrong_scope_gates_refused(self) -> None:
        with self.assertRaises(ValueError):
            assemble_gate_artifact(
                scope=SCOPE_ENSEMBLE,
                gates=self._member_gates(),
                subject={"manifest_sha256": "cd" * 32},
                window=None, anchor=None,
                generated_at="2026-07-22T10:00:00+00:00",
            )

    def test_unknown_scope_refused(self) -> None:
        with self.assertRaises(ValueError):
            assemble_gate_artifact(
                scope="fleet", gates={}, subject={},
                window=None, anchor=None,
                generated_at="2026-07-22T10:00:00+00:00",
            )


if __name__ == "__main__":
    unittest.main()
