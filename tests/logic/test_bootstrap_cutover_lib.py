"""Bootstrap-cutover decision logic (PR-C', promotion path).

Coverage matrix (>=1 refusal per gate + the admitting state):

  campaign eligibility — happy / wrong schema / promotion_eligible
      false-or-absent / non-finite net / malformed anchors / bad JSON.
  iso_week anchor      — happy / config binding mismatch / missing
      metrics / non-finite net / net <= 0.
  initial status       — happy shape / bad sidecar hash / bad anchor /
      empty note.
  baseline record      — happy shape / wrong member count.
  inference meta       — happy shape / missing field.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.bootstrap_cutover_lib import (  # noqa: E402
    BASELINE_SCHEMA_VERSION,
    RECERT_STATUS_SCHEMA_VERSION,
    CutoverRefusal,
    build_baseline_record,
    build_inference_meta,
    build_initial_status,
    check_campaign_eligibility,
    check_isoweek_anchor,
)

_SHA = "6a" * 32
_COMMIT = "3f" * 20


def _sidecar() -> dict:
    return {
        "schema_version": "csi800_cadence_verdict_v1",
        "anchors": {"pair_anchor": _COMMIT, "evidence_anchor": _COMMIT,
                    "n1_anchor": _COMMIT, "mainline_ref": "origin/main"},
        "inputs": {},
        "verdict": {"promotion_eligible": True,
                    "conservative_net_annualized": 0.0652,
                    "gross_retention": 0.7881},
    }


def _aggregate(net: float = 0.0601, n: int = 23) -> dict:
    """A well-formed anchored aggregate: the summary MUST be the fold
    rows' own mean (the promotion net is re-derived, codex #392 r1)."""
    folds = [{"fold_index": i, "annualized_return": net} for i in range(n)]
    return {"num_folds": n, "folds": folds,
            "aggregate_metrics": {"mean_annualized_return": net,
                                  "valid_folds_annualized_return": n}}


class CutoverPathPreconditions(unittest.TestCase):
    """Adversarial self-review: these are adjudicated WITH the gates,
    so a refusal is zero-write and ``--dry-run`` covers them."""

    _OK = dict(incumbent_exists=True, manifest_out_exists=False,
               status_exists=False, incumbent="Z:/incumbent.pkl",
               manifest_out="Z:/prod_manifest.json",
               status_path="docs/promotion/csi800_recert_status.json")

    def test_clean_slate_admits(self) -> None:
        from scripts.bootstrap_cutover_lib import check_cutover_paths

        check_cutover_paths(**self._OK)  # type: ignore[arg-type]

    def test_missing_incumbent_refused(self) -> None:
        from scripts.bootstrap_cutover_lib import check_cutover_paths

        with self.assertRaises(CutoverRefusal) as ctx:
            check_cutover_paths(
                **{**self._OK, "incumbent_exists": False})  # type: ignore[arg-type]
        self.assertIn("rollback kit", str(ctx.exception))

    def test_existing_production_manifest_refused(self) -> None:
        # A re-run must NOT reinstall the bootstrap trio over a
        # manifest a quarterly rotation already moved forward.
        from scripts.bootstrap_cutover_lib import check_cutover_paths

        with self.assertRaises(CutoverRefusal) as ctx:
            check_cutover_paths(
                **{**self._OK, "manifest_out_exists": True})  # type: ignore[arg-type]
        self.assertIn("silently revert", str(ctx.exception))

    def test_existing_status_artifact_refused(self) -> None:
        from scripts.bootstrap_cutover_lib import check_cutover_paths

        with self.assertRaises(CutoverRefusal) as ctx:
            check_cutover_paths(
                **{**self._OK, "status_exists": True})  # type: ignore[arg-type]
        self.assertIn("written ONCE", str(ctx.exception))


class WriteTargetCollisions(unittest.TestCase):
    """codex #392 r4: every artifact the cutover writes owns its path,
    or a later write silently overwrites an earlier one."""

    def test_distinct_targets_admit(self) -> None:
        from scripts.bootstrap_cutover_lib import check_write_targets

        check_write_targets({
            "manifest_out": "/prod/manifest.json",
            "status_artifact": "/repo/docs/promotion/status.json",
            "baseline": "/repo/docs/promotion/baseline.json",
            "member[0] inference meta": "/models/m0.meta.json",
        })

    def test_collisions_refused(self) -> None:
        from scripts.bootstrap_cutover_lib import check_write_targets

        cases = {
            "manifest == status": {
                "manifest_out": "/repo/docs/promotion/status.json",
                "status_artifact": "/repo/docs/promotion/status.json"},
            "manifest == baseline": {
                "manifest_out": "/repo/docs/promotion/baseline.json",
                "baseline": "/repo/docs/promotion/baseline.json"},
            "manifest == member meta": {
                "manifest_out": "/models/m0.meta.json",
                "member[0] inference meta": "/models/m0.meta.json"},
            "two members share a meta": {
                "member[0] inference meta": "/models/m.meta.json",
                "member[1] inference meta": "/models/m.meta.json"},
        }
        for label, targets in cases.items():
            with self.assertRaises(CutoverRefusal, msg=label) as ctx:
                check_write_targets(targets)
            self.assertIn("collision", str(ctx.exception))


class ExecutorReadDiscipline(unittest.TestCase):
    """Source pins for the two adversarial-self-review P1s."""

    _SRC = (_PROJECT_ROOT / "scripts"
            / "bootstrap_ensemble_cutover.py").read_text(encoding="utf-8")

    def test_sidecar_is_read_at_the_pinned_revision(self) -> None:
        # `--verify` validates the MAINLINE-anchored sidecar, so the
        # bytes adjudicated (and frozen into the 15-month status
        # artifact) must come from that same revision — never the
        # working tree.
        self.assertIn("_show(repo, rev, VERDICT_SIDECAR_PATH)", self._SRC)
        self.assertNotIn("repo / VERDICT_SIDECAR_PATH", self._SRC)
        # One revision for every gate, with movement refused.
        self.assertIn("the mainline moved while the campaign "
                      "verification ran", self._SRC)

    def test_path_preconditions_run_in_the_gate_phase(self) -> None:
        gate_phase, write_phase = self._SRC.split("def main(", 1)
        self.assertIn("check_cutover_paths", gate_phase)
        # The write phase must NOT carry its own late existence guard
        # (that was the post-write refusal this pins against).
        self.assertNotIn("already exists — the initial", write_phase)


class CampaignEligibility(unittest.TestCase):
    def test_eligible_sidecar_admits(self) -> None:
        payload = check_campaign_eligibility(json.dumps(_sidecar()))
        self.assertIs(True, payload["verdict"]["promotion_eligible"])

    def test_refusals(self) -> None:
        cases = {
            "schema": lambda p: p.update(schema_version="v0"),
            "not eligible": lambda p: p["verdict"].update(
                promotion_eligible=False),
            "eligible absent": lambda p: p["verdict"].pop(
                "promotion_eligible"),
            "eligible truthy-not-true": lambda p: p["verdict"].update(
                promotion_eligible=1),
            "net nan": lambda p: p["verdict"].update(
                conservative_net_annualized=float("nan")),
            "no verdict block": lambda p: p.pop("verdict"),
            "no anchors": lambda p: p.pop("anchors"),
            "bad anchor": lambda p: p["anchors"].update(
                evidence_anchor="not-a-commit"),
        }
        for label, mutate in cases.items():
            payload = _sidecar()
            mutate(payload)
            with self.assertRaises(CutoverRefusal, msg=label):
                check_campaign_eligibility(json.dumps(payload))
        for raw in ("not json {", json.dumps(["a"])):
            with self.assertRaises(CutoverRefusal, msg=raw):
                check_campaign_eligibility(raw)


class IsoweekAnchor(unittest.TestCase):
    def test_bound_and_positive_admits(self) -> None:
        out = check_isoweek_anchor(
            _aggregate(), expected_config_sha256="ab" * 32,
            actual_config_sha256="ab" * 32)
        self.assertAlmostEqual(0.0601, out["net_annualized"])
        self.assertEqual(23, out["num_folds"])

    def test_config_binding_mismatch_refused(self) -> None:
        with self.assertRaises(CutoverRefusal) as ctx:
            check_isoweek_anchor(
                _aggregate(), expected_config_sha256="ab" * 32,
                actual_config_sha256="cd" * 32)
        self.assertIn("does\nnot bind".replace("\n", " "),
                      str(ctx.exception).replace("\n", " "))

    def test_non_positive_or_corrupt_net_refused(self) -> None:
        for net in (0.0, -0.01, float("nan"), float("inf")):
            with self.assertRaises(CutoverRefusal, msg=net):
                check_isoweek_anchor(
                    _aggregate(net), expected_config_sha256="ab" * 32,
                    actual_config_sha256="ab" * 32)

    def test_malformed_aggregate_refused(self) -> None:
        for bad in ({}, {"aggregate_metrics": "x"}, ["list"], None):
            with self.assertRaises(CutoverRefusal, msg=bad):
                check_isoweek_anchor(
                    bad, expected_config_sha256="ab" * 32,
                    actual_config_sha256="ab" * 32)

    def test_net_is_rederived_from_the_fold_rows(self) -> None:
        # codex #392 r1: the promotion net authority must not be an
        # asserted summary number. A torn report whose summary stayed
        # positive while its folds went missing / duplicated /
        # negative must refuse.
        cases = {
            "no folds": lambda a: a.pop("folds"),
            "empty folds": lambda a: a.update(folds=[]),
            "count mismatch": lambda a: a["folds"].pop(),
            "duplicate index": lambda a: a["folds"][1].update(
                fold_index=0),
            "shifted indexes": lambda a: [
                row.update(fold_index=row["fold_index"] + 1)
                for row in a["folds"]],
            "non-finite fold": lambda a: a["folds"][3].update(
                annualized_return=float("nan")),
            "row not an object": lambda a: a["folds"].__setitem__(
                2, "torn"),
            "partial scoring": lambda a: a["aggregate_metrics"].update(
                valid_folds_annualized_return=22),
            # The summary says +6% while the folds actually average
            # NEGATIVE — the exact scenario the re-derivation exists
            # for.
            "summary lies": lambda a: [
                row.update(annualized_return=-0.02)
                for row in a["folds"]],
        }
        for label, mutate in cases.items():
            aggregate = _aggregate()
            mutate(aggregate)
            with self.assertRaises(CutoverRefusal, msg=label):
                check_isoweek_anchor(
                    aggregate, expected_config_sha256="ab" * 32,
                    actual_config_sha256="ab" * 32)

    def test_committed_evidence_reproduces_exactly(self) -> None:
        # The REAL anchored evidence: fold mean == serialized summary.
        import json

        path = (_PROJECT_ROOT / "docs" / "research" / "evidence"
                / "csi800_n5_runs"
                / "csi800_cadence5_conservative_isoweek"
                / "walk_forward_report.json")
        aggregate = json.loads(path.read_text(encoding="utf-8"))
        out = check_isoweek_anchor(
            aggregate, expected_config_sha256="ab" * 32,
            actual_config_sha256="ab" * 32)
        self.assertEqual(23, out["num_folds"])
        self.assertEqual(out["net_annualized_serialized"],
                         out["net_annualized"])
        self.assertGreater(out["net_annualized"], 0.0)


class InitialStatus(unittest.TestCase):
    def test_shape(self) -> None:
        status = build_initial_status(
            verdict_sidecar_path="docs/research/x.json",
            verdict_sidecar_sha256=_SHA,
            evidence_anchor_commit=_COMMIT, note="bootstrap WIN")
        self.assertEqual(RECERT_STATUS_SCHEMA_VERSION,
                         status["schema_version"])
        self.assertEqual("WIN", status["verdict"])
        self.assertEqual(_SHA, status["verdict_sidecar_sha256"])
        # The quarterly executor must be able to parse what we write.
        from scripts.rotation_lib import parse_recert_status

        parse_recert_status(json.dumps(status))

    def test_refusals(self) -> None:
        base = dict(verdict_sidecar_path="docs/research/x.json",
                    verdict_sidecar_sha256=_SHA,
                    evidence_anchor_commit=_COMMIT, note="ok")
        for key, bad in (("verdict_sidecar_sha256", "short"),
                         ("evidence_anchor_commit", "short"),
                         ("note", "   ")):
            kwargs = dict(base)
            kwargs[key] = bad
            with self.assertRaises(CutoverRefusal, msg=key):
                build_initial_status(**kwargs)  # type: ignore[arg-type]


class BaselineRecord(unittest.TestCase):
    def _members(self, n: int = 3) -> list[dict]:
        return [{"pkl_path": f"m{i}.pkl", "pkl_sha256": f"{i}" * 64}
                for i in range(n)]

    def test_shape(self) -> None:
        record = build_baseline_record(
            manifest_path="Z:/manifest.json", manifest_sha256="cd" * 32,
            members=self._members(), incumbent_backup={"a.pkl": "a.bak"},
            campaign={"x": 1}, isoweek={"y": 2},
            gate_artifacts={"ensemble": "g.json"},
            generated_at="2026-07-23T00:00:00+00:00")
        self.assertEqual(BASELINE_SCHEMA_VERSION,
                         record["schema_version"])
        self.assertEqual("ensemble_manifest", record["serving"]["mode"])
        self.assertEqual(3, len(record["serving"]["members"]))
        self.assertIn("campaign", record["authorized_by"])

    def test_wrong_member_count_refused(self) -> None:
        for n in (2, 4):
            with self.assertRaises(CutoverRefusal, msg=n):
                build_baseline_record(
                    manifest_path="Z:/m.json", manifest_sha256="cd" * 32,
                    members=self._members(n), incumbent_backup={},
                    campaign={}, isoweek={}, gate_artifacts={},
                    generated_at="2026-07-23T00:00:00+00:00")


class InferenceMeta(unittest.TestCase):
    def test_shape_mirrors_stage4_contract(self) -> None:
        meta = build_inference_meta(
            model_path="Z:/m.pkl", fit_start="2024-02-19",
            fit_end="2026-02-13", model_type="LGBModel",
            promoted_at="2026-07-23T00:00:00+00:00")
        self.assertEqual("2024-02-19", meta["fit_start_for_inference"])
        self.assertEqual("2026-02-13", meta["fit_end_for_inference"])
        self.assertEqual("2024-02-19..2026-02-13", meta["train_window"])
        # The decision-page banner contract fields.
        for key in ("model_path", "model_type", "promoted_at"):
            self.assertIn(key, meta)

    def test_missing_field_refused(self) -> None:
        base = dict(model_path="Z:/m.pkl", fit_start="2024-02-19",
                    fit_end="2026-02-13", model_type="LGBModel",
                    promoted_at="2026-07-23T00:00:00+00:00")
        for key in ("fit_start", "fit_end", "model_type", "promoted_at"):
            kwargs = dict(base)
            kwargs[key] = "  "
            with self.assertRaises(CutoverRefusal, msg=key):
                build_inference_meta(**kwargs)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
