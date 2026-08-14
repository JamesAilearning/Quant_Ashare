"""Source-contract guards for the 今日推荐 page (A2, add-daily-decision-page).

The page's hard boundaries — read-only except journal appends, no job/training
triggers, WARN-never-default banner, registration + documentation — are pinned
at the source level (the repo's UI-page test idiom), plus runtime tests for the
pure helpers.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PAGE = _ROOT / "web" / "operator_ui" / "pages" / "daily_decision.py"
_HELPERS = _ROOT / "web" / "operator_ui" / "pages" / "_daily_decision_helpers.py"
_APP = _ROOT / "web" / "operator_ui" / "app.py"
_ENV_DOC = _ROOT / "docs" / "operations-env-vars.md"
_README = _ROOT / "web" / "README.md"


class PageBoundaryTests(unittest.TestCase):
    """今日推荐 must never launch/stop jobs, touch training, or import the
    launch/jobs surfaces the campaign depends on."""

    def setUp(self) -> None:
        self.page = _PAGE.read_text(encoding="utf-8")

    def test_no_job_or_training_triggers(self) -> None:
        for forbidden in (
            "JobManager", "subprocess", "job_runner", "config_run",
            "import qlib", "recommend(",  # never re-runs inference
        ):
            self.assertNotIn(forbidden, self.page, forbidden)

    def test_only_write_surface_is_the_journal_append(self) -> None:
        # The page itself holds no filesystem write API — appends go through
        # decision_journal (whose write behavior is threat-tested).
        for write_api in ("open(", "write_text", "write_bytes", "mkdir"):
            self.assertNotIn(write_api, self.page, write_api)
        self.assertIn("append_decision", self.page)

    def test_banner_warns_and_never_defaults(self) -> None:
        self.assertIn("模型元信息缺失", self.page)
        self.assertIn("绝不用默认值", self.page)

    def test_banner_renders_present_fields_only_no_placeholder(self) -> None:
        # codex P2 on #330: a missing banner field lives ONLY in the WARN —
        # the value row is built by membership checks and shows no "—"
        # placeholder that would disguise the absence as a benign blank.
        self.assertIn('if "fit_end_for_inference" in _banner_values', self.page)
        self.assertIn('if "promoted_at" in _banner_values', self.page)
        self.assertIn("st.columns(len(_banner_items))", self.page)
        self.assertNotIn('_banner_values.get("fit_end_for_inference", "—")', self.page)
        self.assertNotIn('_banner_values.get("promoted_at", "—")', self.page)

    def test_stale_artifact_cross_check_present(self) -> None:
        self.assertIn("其他模型", self.page)      # sha mismatch WARN
        self.assertIn("旧版工件", self.page)      # v1 WARN

    def test_form_uses_session_nonce_and_explicit_button(self) -> None:
        self.assertIn('st.session_state["dd_nonce"]', self.page)
        self.assertIn("uuid4().hex", self.page)
        self.assertIn('st.button("✍ 记录决策"', self.page)


class RegistrationAndDocsTests(unittest.TestCase):
    def test_page_registered_in_run_group_with_icon(self) -> None:
        app = _APP.read_text(encoding="utf-8")
        self.assertIn('daily_decision.py"), title="今日推荐"', app)
        self.assertIn('"今日推荐": "\\U0001f4dd"', app)

    def test_env_var_documented(self) -> None:
        doc = _ENV_DOC.read_text(encoding="utf-8")
        self.assertIn("QUANT_DECISION_JOURNAL_DIR", doc)
        self.assertIn("D:/stock/operator_journal", doc)

    def test_readme_updated_with_boundary(self) -> None:
        readme = _README.read_text(encoding="utf-8")
        self.assertNotIn("Skeleton only", readme)
        self.assertIn("daily_decision.py", readme)
        self.assertIn("NEVER an input to official metrics", readme)


class HelpersRuntimeTests(unittest.TestCase):
    """The pure helpers behave per spec (no Streamlit needed)."""

    def test_hold_state_three_way(self) -> None:
        # PR-A (csi800-n5-production-promotion, codex #385 r5): the HOLD
        # reader — explicit false = HOLD; true or ABSENT (legacy daily
        # artifact) renders exactly as before; a present non-bool is a
        # shape violation surfaced loudly, never guessed around.
        from web.operator_ui.pages._daily_decision_helpers import hold_state

        hold = hold_state({"rebalance_day": False,
                           "next_rebalance_date": "2025-07-07"})
        self.assertTrue(hold.is_hold)
        self.assertEqual(hold.next_rebalance_date, "2025-07-07")
        self.assertIsNone(hold.malformed)

        active = hold_state({"rebalance_day": True,
                             "next_rebalance_date": "2025-07-01"})
        self.assertFalse(active.is_hold)
        self.assertIsNone(active.malformed)

        legacy = hold_state({"as_of_date": "2025-06-30"})
        self.assertFalse(legacy.is_hold)
        self.assertIsNone(legacy.next_rebalance_date)
        self.assertIsNone(legacy.malformed)

        bad = hold_state({"rebalance_day": "false"})
        self.assertFalse(bad.is_hold)
        self.assertIsNotNone(bad.malformed)

        # codex #386 r1: a PRESENT null is a shape violation, NOT the
        # legacy-absent case — it must not silently downgrade to daily
        # (actionable) semantics.
        null_present = hold_state({"rebalance_day": None})
        self.assertFalse(null_present.is_hold)
        self.assertIsNotNone(null_present.malformed)

    def test_hold_state_null_next_anchor_disclosed(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import hold_state

        hold = hold_state({"rebalance_day": False,
                           "next_rebalance_date": None})
        self.assertTrue(hold.is_hold)
        self.assertIsNone(hold.next_rebalance_date)

    def test_page_blocks_entry_form_on_hold(self) -> None:
        # Source-level pin (same style as the boundary tests above): the
        # page consults hold_state and refuses to render the entry form
        # on a HOLD artifact.
        src = _PAGE.read_text(encoding="utf-8")
        self.assertIn("hold_state", src)
        self.assertIn("_hold.is_hold", src)
        self.assertIn("不构成入场指令", src)

    def test_cost_reference_is_score_minus_30bps(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import (
            ROUND_TRIP_COST,
            cost_reference,
        )
        self.assertEqual(ROUND_TRIP_COST, 0.0030)
        self.assertAlmostEqual(cost_reference(0.0123), 0.0093)

    def test_banner_status_flags_missing_never_defaults(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import (
            BANNER_FIELDS,
            banner_status,
        )
        values, missing = banner_status(None)
        self.assertEqual(values, {})
        self.assertEqual(missing, BANNER_FIELDS)
        partial = {"fit_end_for_inference": "2024-12-18", "train_window": []}
        values, missing = banner_status(partial)
        self.assertIn("fit_end_for_inference", values)
        self.assertIn("train_window", missing)  # empty list == missing
        self.assertIn("promoted_at", missing)
        self.assertNotIn("train_window", values)
        # model_type is a CONTRACT field (spec: model identity = model_path +
        # model_type) — its absence must be reported, not treated as optional
        # display enrichment (codex P2 on #330).
        self.assertIn("model_type", missing)

    def test_artifact_meta_status_v1_and_mismatch(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import (
            artifact_meta_status,
        )
        v1 = artifact_meta_status({"picks": []}, current_model_sha="ab")
        self.assertTrue(v1.artifact_is_v1)
        self.assertFalse(v1.artifact_is_corrupt_v2)
        self.assertIsNone(v1.sha_mismatch)
        v2 = {"meta": {"model_pkl_sha256": "aa"}}
        self.assertTrue(artifact_meta_status(v2, "bb").sha_mismatch)
        self.assertFalse(artifact_meta_status(v2, "aa").sha_mismatch)
        self.assertIsNone(artifact_meta_status(v2, None).sha_mismatch)

    def test_artifact_meta_status_ensemble_identity(self) -> None:
        # codex #390 r3: an ensemble artifact's identity is the manifest
        # sha256, NOT a single-pickle sha — comparing it against the
        # trainer sidecar would misreport a valid artifact as "other
        # model". The status flags ensemble explicitly, keeps mismatch
        # None, and the page renders a dedicated notice.
        from web.operator_ui.pages._daily_decision_helpers import (
            artifact_meta_status,
        )
        ens = {"meta": {"model_path": "D:/manifest.json",
                        "ensemble": {"manifest_sha256": "cc" * 32}}}
        status = artifact_meta_status(ens, current_model_sha="ab")
        self.assertTrue(status.artifact_is_ensemble)
        self.assertEqual("cc" * 32, status.artifact_ensemble_sha)
        self.assertIsNone(status.sha_mismatch)
        self.assertIsNone(status.artifact_model_sha)
        self.assertFalse(status.artifact_is_v1)
        self.assertFalse(status.artifact_is_corrupt_v2)
        # Malformed ensemble block (no manifest_sha256): still flagged
        # ensemble but with no identity — the page warns instead of
        # showing a bindable sha.
        broken = artifact_meta_status(
            {"meta": {"ensemble": {}}}, current_model_sha=None)
        self.assertTrue(broken.artifact_is_ensemble)
        self.assertIsNone(broken.artifact_ensemble_sha)
        # codex #390 r5: key PRESENCE marks the artifact ensemble-
        # shaped — a non-dict block (plus a stale single sha) is
        # malformed-ensemble, never a comparable single-pickle
        # artifact.
        nondict = artifact_meta_status(
            {"meta": {"ensemble": "corrupt",
                      "model_pkl_sha256": "aa"}},
            current_model_sha="aa")
        self.assertTrue(nondict.artifact_is_ensemble)
        self.assertIsNone(nondict.artifact_ensemble_sha)
        self.assertIsNone(nondict.sha_mismatch)
        # Single-model artifacts keep the flag off (default path pinned
        # by test_artifact_meta_status_v1_and_mismatch).
        single = artifact_meta_status(
            {"meta": {"model_pkl_sha256": "aa"}}, "aa")
        self.assertFalse(single.artifact_is_ensemble)
        # Page renders the dedicated ensemble branch before the v1 /
        # mismatch branches.
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("artifact_is_ensemble", page)
        self.assertIn("ensemble(manifest)", page)

    def test_journal_model_id_ensemble_prefix(self) -> None:
        # codex #390 r3: ensemble journal identity = "ensemble:<manifest
        # sha>" — content-bound and impossible to confuse with a pickle
        # digest.
        from web.operator_ui.pages._daily_decision_helpers import (
            journal_model_id,
        )
        self.assertEqual(
            journal_model_id({"meta": {
                "model_path": "D:/manifest.json",
                "ensemble": {"manifest_sha256": "cc" * 32}}}),
            "ensemble:" + "cc" * 32,
        )
        # Malformed ensemble block falls through to the honest
        # path-based fallback rather than fabricating an id.
        self.assertEqual(
            journal_model_id({"meta": {
                "model_path": "D:/manifest.json", "ensemble": {}}}),
            "D:/manifest.json",
        )
        # codex #390 r4: a malformed ensemble block NEVER falls through
        # to model_pkl_sha256 — a hand-edited artifact carrying both
        # would re-enter the single-pickle identity namespace.
        self.assertEqual(
            journal_model_id({"meta": {
                "model_path": "D:/manifest.json",
                "model_pkl_sha256": "aa" * 32,
                "ensemble": {}}}),
            "D:/manifest.json",
        )
        # No path either: dedicated sentinel, never a bare sha.
        self.assertEqual(
            journal_model_id({"meta": {
                "model_pkl_sha256": "aa" * 32, "ensemble": {}}}),
            "unknown(malformed-ensemble-artifact)",
        )
        # codex #390 r5: a NON-DICT ensemble value is still ensemble-
        # shaped (key presence decides) — the stale sha stays out of
        # the journal namespace.
        self.assertEqual(
            journal_model_id({"meta": {
                "model_path": "D:/manifest.json",
                "model_pkl_sha256": "aa" * 32,
                "ensemble": "corrupt"}}),
            "D:/manifest.json",
        )
        self.assertEqual(
            journal_model_id({"meta": {
                "model_pkl_sha256": "aa" * 32,
                "ensemble": ["corrupt"]}}),
            "unknown(malformed-ensemble-artifact)",
        )

    def test_v2_marker_without_meta_is_corrupt_not_legacy(self) -> None:
        # codex P2 on #330: the producer ALWAYS writes a dict meta for v2 —
        # a v2-marked file with missing/non-dict meta is corrupt and must not
        # be soft-labelled as an expected legacy v1 artifact.
        from web.operator_ui.pages._daily_decision_helpers import (
            artifact_meta_status,
        )
        for bad in ({"artifact_schema_version": 2},
                    {"artifact_schema_version": 2, "meta": "not-a-dict"}):
            status = artifact_meta_status(bad, current_model_sha="ab")
            self.assertTrue(status.artifact_is_corrupt_v2, bad)
            self.assertFalse(status.artifact_is_v1, bad)
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("损坏的 v2 工件", page)

    def test_nonce_rotates_on_success_and_duplicate(self) -> None:
        # codex P2 on #330: a stale already-persisted nonce must not pin the
        # form — BOTH the success and the duplicate-intercept branches mint a
        # fresh nonce (plus the initial mint = 3 sites).
        page = _PAGE.read_text(encoding="utf-8")
        self.assertEqual(
            page.count('st.session_state["dd_nonce"] = uuid4().hex'), 3,
        )

    def test_journal_model_id_prefers_sha_then_honest_sentinel(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import (
            journal_model_id,
        )
        self.assertEqual(
            journal_model_id({"meta": {"model_pkl_sha256": "aa"}}), "aa",
        )
        self.assertEqual(
            journal_model_id({"meta": {"model_path": "D:/m.pkl"}}), "D:/m.pkl",
        )
        self.assertEqual(journal_model_id({}), "unknown(v1-artifact)")

    def test_list_artifacts_sorted_desc_and_pattern_locked(self) -> None:
        import tempfile

        from web.operator_ui.pages._daily_decision_helpers import (
            list_recommendation_artifacts,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (
                "daily_recommendation_2026-07-01.json",
                "daily_recommendation_2026-07-03.json",
                "daily_recommendation_2026-07-03_scored_full.csv",  # not JSON artifact
                "unrelated.json",
            ):
                (root / name).write_text("{}", encoding="utf-8")
            found = list_recommendation_artifacts(root)
        self.assertEqual([d for d, _ in found], ["2026-07-03", "2026-07-01"])

    def test_banner_meta_is_promotion_sidecar_only_no_fallthrough(self) -> None:
        # codex P2 on #330: a trainer sidecar must NOT stand in for a missing
        # promotion meta — the banner reports absence loudly instead.
        import json
        import tempfile

        from web.operator_ui.pages._daily_decision_helpers import (
            load_promotion_meta,
            load_trainer_sidecar_sha,
        )
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "m.pkl"
            # ONLY the trainer sidecar exists (no promotion meta).
            (Path(tmp) / "m.pkl.meta.json").write_text(
                json.dumps({"pkl_sha256": "ab" * 32, "model_type": "LGBModel"}),
                encoding="utf-8",
            )
            self.assertIsNone(load_promotion_meta(str(model)))
            self.assertEqual(load_trainer_sidecar_sha(str(model)), "ab" * 32)

    def test_picks_shape_violation_raises_not_empty(self) -> None:
        # codex P2 on #330: missing/non-list picks is a corrupt artifact —
        # it must fail loud, never masquerade as the benign empty state.
        from web.operator_ui.pages._daily_decision_helpers import (
            picks_table_rows,
        )
        with self.assertRaisesRegex(ValueError, "形状违约"):
            picks_table_rows({})  # picks missing
        with self.assertRaisesRegex(ValueError, "形状违约"):
            picks_table_rows({"picks": "not-a-list"})
        with self.assertRaisesRegex(ValueError, "形状违约"):
            picks_table_rows({"picks": ["not-a-dict"]})
        self.assertEqual(picks_table_rows({"picks": []}), [])  # legit empty

    def test_page_renders_shape_violation_and_journal_misconfig(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("except ValueError", page)          # shape error branch
        self.assertIn("决策日志不可用", page)              # journal misconfig branch

    def test_page_stops_on_filename_payload_date_mismatch(self) -> None:
        # codex P2 on #330: a renamed/copied artifact (filename date != payload
        # as_of_date) must be treated as corrupt BEFORE any journal write —
        # otherwise the decision records under the payload date and vanishes
        # from the selected day's table.
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("_payload_as_of != _selected_date", page)
        self.assertIn("as_of_date 不一致", page)

    def test_picks_rows_pass_through_only_plus_cost_column(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import (
            picks_table_rows,
        )
        payload = {
            "picks": [{
                "rank": 1, "stock_code": "SH600000", "stock_name": "浦发银行",
                "predicted_score": 0.0123, "tradable_flag": True,
                "unavailable_reason": "",
            }],
        }
        rows = picks_table_rows(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["代码"], "SH600000")
        self.assertAlmostEqual(
            float(rows[0]["评分−30bps(往返成本参照)"]), 0.0093,
        )
        self.assertEqual(rows[0]["不可用原因"], "")


if __name__ == "__main__":
    unittest.main()


class IncumbentEnsembleIdentityTests(unittest.TestCase):
    """2026-08-14: production switched to a 3-member ensemble on 2026-08-05,
    but the page kept describing the retired single model and printed a
    now-false claim ("当前生产为单模型形态") plus a TODO that had already come
    due. These pin the three states the banner and the cross-check must have."""

    def setUp(self) -> None:
        self.page = _PAGE.read_text(encoding="utf-8")
        self.helpers = _HELPERS.read_text(encoding="utf-8")

    # --- runtime: the three incumbent states -------------------------------

    # A manifest the CANONICAL serving validator accepts. Windows are the
    # real production ones (92d stagger, ~24m spans); identity fields differ
    # per member because the validator refuses repeated members.
    _GOOD = {
        "schema_version": "csi800_n5_ensemble_manifest_v1",
        "members": [
            {"pkl_path": f"/m{i}.pkl", "pkl_sha256": str(i) * 64,
             "meta_path": f"/m{i}.pkl.meta.json", "meta_sha256": f"{i}a" * 32,
             "fit_start": fs, "fit_end": fe}
            for i, (fs, fe) in enumerate(
                [("2023-09-28", "2025-09-29"),
                 ("2023-12-29", "2025-12-30"),
                 ("2024-04-01", "2026-04-01")], start=1)
        ],
    }

    def _identity(self, payload, *, name="m.json"):
        import json
        import tempfile

        from web.operator_ui.pages._daily_decision_helpers import (
            load_ensemble_manifest_identity,
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / name
            p.write_text(json.dumps(payload) if not isinstance(payload, str)
                         else payload, encoding="utf-8")
            return load_ensemble_manifest_identity(str(p))

    def test_readable_manifest_yields_ensemble_identity(self) -> None:
        ident = self._identity(self._GOOD)
        self.assertEqual("ensemble", ident.kind)
        self.assertTrue(ident.is_ensemble)
        self.assertEqual(3, len(ident.members))
        self.assertEqual(64, len(str(ident.manifest_sha256)))
        self.assertEqual("2026-04-01", ident.members[-1]["fit_end"])

    def test_identity_delegates_to_the_canonical_serving_validator(self) -> None:
        # codex #430: a hand-rolled parser here would be a SECOND, weaker
        # reading of the same file — it could vouch for a manifest the real
        # serving path refuses. These shapes are exactly what the weaker
        # parser accepted and the canonical validator rejects.
        import copy

        cases = {
            "wrong schema": {**self._GOOD, "schema_version": "vX"},
            "two members": {**self._GOOD,
                            "members": self._GOOD["members"][:2]},
            "missing identity field": None,     # filled below
            "duplicate member": None,
            "bad stagger": None,
        }
        m = copy.deepcopy(self._GOOD)
        del m["members"][0]["pkl_sha256"]
        cases["missing identity field"] = m
        m = copy.deepcopy(self._GOOD)
        m["members"][1] = copy.deepcopy(m["members"][0])
        cases["duplicate member"] = m
        m = copy.deepcopy(self._GOOD)
        m["members"][1]["fit_end"] = "2025-10-01"   # 2d gap, not quarterly
        cases["bad stagger"] = m
        for label, payload in cases.items():
            with self.subTest(case=label):
                self.assertEqual("unresolvable", self._identity(payload).kind)

    def test_unreadable_or_malformed_manifest_is_unresolvable(self) -> None:
        # Every malformed shape must land in "unresolvable", NEVER degrade to
        # the single-model banner (that would name a possibly-retired model).
        for payload in ("{not json", {"members": []}, {"members": "x"},
                        {"members": [1, 2]}, {"schema_version": "v1"}, []):
            with self.subTest(payload=str(payload)[:24]):
                ident = self._identity(payload)
                self.assertEqual("unresolvable", ident.kind)
                self.assertIsNotNone(ident.error)
                self.assertFalse(ident.is_ensemble)

    def test_missing_file_is_unresolvable(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import (
            load_ensemble_manifest_identity,
        )
        ident = load_ensemble_manifest_identity("Z:/nonexistent/manifest.json")
        self.assertEqual("unresolvable", ident.kind)

    def test_unset_pointer_uses_the_documented_default_not_single(self) -> None:
        # codex #430 r1: reading "variable not configured" as "production
        # went back to one model" fabricates a fact — and on any box that
        # upgraded the UI without adding the variable it would BOTH show the
        # retired model AND warn against the correct ensemble lists.
        import os
        from unittest.mock import patch

        from web.operator_ui.pages import _daily_decision_helpers as H

        with patch.dict(os.environ, {H.ENV_ENSEMBLE_MANIFEST: ""}, clear=False):
            with patch.object(H, "load_ensemble_manifest_identity") as fake:
                fake.return_value = H.IncumbentIdentity(kind="ensemble")
                H.resolve_incumbent()
            fake.assert_called_once_with(H.DEFAULT_ENSEMBLE_MANIFEST)

    def test_single_model_requires_the_explicit_opt_out(self) -> None:
        import os
        from unittest.mock import patch

        from web.operator_ui.pages._daily_decision_helpers import (
            ENV_ENSEMBLE_MANIFEST,
            SINGLE_MODEL_SENTINEL,
            resolve_incumbent,
        )
        with patch.dict(os.environ,
                        {ENV_ENSEMBLE_MANIFEST: SINGLE_MODEL_SENTINEL},
                        clear=False):
            self.assertEqual("single", resolve_incumbent().kind)

    def test_default_points_at_the_cutover_manifest(self) -> None:
        # The default must name the manifest the 2026-08-05 cutover wrote —
        # a default pointing anywhere else silently reinstates the bug.
        from web.operator_ui.pages._daily_decision_helpers import (
            DEFAULT_ENSEMBLE_MANIFEST,
        )
        self.assertTrue(
            DEFAULT_ENSEMBLE_MANIFEST.endswith(
                "csi800_n5_ensemble_manifest.json"),
            DEFAULT_ENSEMBLE_MANIFEST)
        doc = _ENV_DOC.read_text(encoding="utf-8")
        self.assertIn(DEFAULT_ENSEMBLE_MANIFEST, doc)

    # --- source: banner + cross-check must honour those states -------------

    def test_banner_refuses_to_fall_back_when_unresolvable(self) -> None:
        self.assertIn('_incumbent.kind == "unresolvable"', self.page)
        self.assertIn("绝不退回单模型形态顶替", self.page)

    def test_ensemble_banner_shows_manifest_identity(self) -> None:
        self.assertIn("现任生产模型(ensemble)", self.page)
        self.assertIn("_incumbent.manifest_sha256", self.page)
        self.assertIn("_incumbent.members", self.page)

    def test_single_model_banner_suppressed_under_ensemble(self) -> None:
        # Leaving the promotion banner on under an ensemble incumbent is
        # exactly the bug: it describes a model that is not serving.
        self.assertIn('if _incumbent.kind != "single"', self.page)

    def test_incumbent_cross_check_replaces_the_expired_claim(self) -> None:
        # The false statement and the come-due TODO must be gone...
        self.assertNotIn("当前生产为单模型形态", self.page)
        self.assertNotIn("随生产切换(PR-C')落地", self.page)
        # ...replaced by a real three-way comparison.
        self.assertIn("_art_sha == _incumbent.manifest_sha256", self.page)
        self.assertIn("另一份 manifest", self.page)
        self.assertIn("现任是单模型形态", self.page)
        self.assertIn("现任 manifest 不可解析", self.page)

    # --- the read-side-only asymmetry --------------------------------------

    def test_env_var_documented_as_read_side_only(self) -> None:
        doc = _ENV_DOC.read_text(encoding="utf-8")
        self.assertIn("QUANT_ENSEMBLE_MANIFEST", doc)
        self.assertIn("Read-side only", doc)

    def test_cli_ensemble_manifest_has_no_implicit_default(self) -> None:
        # The side that PRODUCES a list must never pick its model implicitly.
        # A future "convenience" default here would make a wrong order list
        # possible from a stale environment variable.
        cli = (_ROOT / "scripts" / "daily_recommend.py").read_text(encoding="utf-8")
        import re
        m = re.search(r'"--ensemble-manifest",\s*default=([^,\)]+)', cli)
        self.assertIsNotNone(m, "--ensemble-manifest 的 default 未找到")
        self.assertEqual("None", m.group(1).strip())


class IncumbentShapeMismatchTests(unittest.TestCase):
    """codex #430: the shape matrix (incumbent × artifact) must have no hole
    that falls through to the RETIRED single model's sidecar comparison."""

    def setUp(self) -> None:
        self.page = _PAGE.read_text(encoding="utf-8")

    def test_single_shaped_artifact_under_ensemble_incumbent_is_rejected(self) -> None:
        # A single-model artifact whose sha happens to match the old sidecar
        # would otherwise emit NO warning at all — presenting a
        # non-incumbent artifact as safe.
        #
        # Anchor inside the CROSS-CHECK block, not by bare string: the banner
        # above also branches on `_incumbent.is_ensemble`, so a naive
        # assertIn/index would be satisfied by the banner and pass even with
        # this guard deleted (caught by mutation testing on this very pin).
        _xcheck = self.page[self.page.index("if _meta_status.artifact_is_ensemble:"):]
        self.assertIn("elif _incumbent.is_ensemble:", _xcheck)
        self.assertIn("该工件是**单模型形态**", _xcheck)
        # ...and it must sit BEFORE the legacy sidecar comparison.
        i_shape = _xcheck.index("elif _incumbent.is_ensemble:")
        i_legacy = _xcheck.index("elif _meta_status.sha_mismatch is True:")
        self.assertLess(
            i_shape, i_legacy,
            "形态不符必须在落回旧单模型 sidecar 比对之前拦下")

    def test_ensemble_artifact_under_single_incumbent_is_a_known_mismatch(self) -> None:
        # An unset pointer is a DEFINITE single-model incumbent, not an
        # unknown — the page must say "do not use", not "cannot determine".
        self.assertIn('elif _incumbent.kind == "single":', self.page)
        i_known = self.page.index('elif _incumbent.kind == "single":')
        i_unknown = self.page.index("现任 manifest 不可解析")
        self.assertLess(i_known, i_unknown)
