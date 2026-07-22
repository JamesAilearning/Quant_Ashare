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
