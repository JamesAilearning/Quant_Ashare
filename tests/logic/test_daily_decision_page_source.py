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

    def test_artifact_meta_status_v1_and_mismatch(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import (
            artifact_meta_status,
        )
        v1 = artifact_meta_status({"picks": []}, current_model_sha="ab")
        self.assertTrue(v1.artifact_is_v1)
        self.assertIsNone(v1.sha_mismatch)
        v2 = {"meta": {"model_pkl_sha256": "aa"}}
        self.assertTrue(artifact_meta_status(v2, "bb").sha_mismatch)
        self.assertFalse(artifact_meta_status(v2, "aa").sha_mismatch)
        self.assertIsNone(artifact_meta_status(v2, None).sha_mismatch)

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
