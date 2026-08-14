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
        # The page classifies the artifact's SHAPE through the pure matrix
        # helper rather than re-deriving the flags inline.
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("artifact_kind_of(_meta_status)", page)
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

    def test_a_blank_model_path_reads_no_sidecar_and_does_not_crash(
            self) -> None:
        # codex #431 r24: the resolver now mirrors the CLI, which does NOT
        # substitute the default for an empty QUANT_MODEL_PATH — so a blank
        # path became reachable here. `Path("").with_suffix(...)` raises
        # "empty name", which would replace this page with a traceback.
        # These loaders are best-effort-or-None, and "no model to read a
        # sidecar beside" is exactly None.
        from web.operator_ui.pages._daily_decision_helpers import (
            load_promotion_meta,
            load_trainer_sidecar_sha,
            model_meta_paths,
        )
        for blank in ("", "   "):
            with self.subTest(model_path=repr(blank)):
                self.assertIsNone(load_promotion_meta(blank))
                self.assertIsNone(load_trainer_sidecar_sha(blank))
                # …and the path builder refuses rather than inventing a pair
                # rooted at the working directory
                with self.assertRaises(ValueError):
                    model_meta_paths(blank)

    def test_the_page_names_an_empty_model_path_env_as_the_cause(self) -> None:
        # Otherwise the operator sees only "元信息缺失" with an empty
        # backtick where the data source should be (r24) — but ONLY under a
        # single-model incumbent: in ensemble mode the CLI refuses `--model`
        # outright (mutually exclusive with `--ensemble-manifest`) and never
        # reads _DEFAULT_MODEL, so an empty override changes nothing and a
        # red banner would report an impossible failure on the deployment
        # production actually runs (codex #431 r25).
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn(
            'if _incumbent.kind == "single" and not _model_path.strip():',
            page)
        self.assertIn("`QUANT_MODEL_PATH` 被设为空值", page)
        # the guard must sit AFTER the incumbent is resolved
        self.assertLess(page.index("_incumbent = resolve_incumbent()"),
                        page.index("`QUANT_MODEL_PATH` 被设为空值"))

    def test_an_irrelevant_model_override_does_not_block_the_ensemble_command(
            self) -> None:
        # The same rule on the cockpit side: an empty QUANT_MODEL_PATH must
        # not refuse a command that never carries `--model` (r25).
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import morning_command
        ens = IncumbentIdentity(
            kind="ensemble", manifest_path="M.json",
            members=({"fit_start": "2024-01-01", "fit_end": "2026-04-01"},))
        cmd = morning_command(
            ens, model_path="", provider_uri="P", delisted_registry="R",
            name_source="N", bundle_max_age_days=14)
        self.assertNotIn("无法生成可粘贴命令", cmd.title)
        self.assertIn("--ensemble-manifest", cmd.command)
        self.assertNotIn("--model", cmd.command)
        # …while the single-model deployment, where it DOES matter, refuses
        single = morning_command(
            IncumbentIdentity(kind="single"), model_path="", provider_uri="P",
            delisted_registry="R", name_source="N", bundle_max_age_days=14)
        self.assertIn("无法生成可粘贴命令", single.title)

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

        # Patch where the resolver LIVES (web.operator_ui.incumbent), not
        # where 今日推荐 re-exports it from: the resolver moved to package
        # level so 生产运维 asks the same code, and a page-local patch would
        # no longer intercept the call it is meant to observe.
        from web.operator_ui import incumbent as H

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
        # ...replaced by a real comparison. The digest comparison itself now
        # lives in provenance_verdict (behaviourally tested by
        # ProvenanceWiringTests) rather than as page source, so pin it where
        # it runs instead of where it used to be written.
        self.assertIn("art_sha == inc_sha", _HELPERS.read_text(encoding="utf-8"))
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


class ProvenanceMatrixTotalityTests(unittest.TestCase):
    """codex #430 r1..r4 each found ANOTHER cell of the same incumbent ×
    artifact matrix, because an ordered elif chain offers no structural
    guarantee that the cells are exhausted — a hole just falls through to
    whatever branch happens to be next (r4's hole fell through to the
    RETIRED model's sidecar compare, where a matching sha printed nothing).

    The matrix is now a pure function, and this table is the whole of it.
    """

    # Every (incumbent, artifact) pair, with the sub-parameter settings that
    # can change the answer. Each row is a claim about what is TRUE for that
    # pair — not a restatement of the implementation's branch order.
    CELLS: dict[tuple[str, str], tuple[tuple[dict[str, object], str], ...]] = {
        # ---- incumbent = ensemble (production shape since 2026-08-05) ----
        ("ensemble", "ensemble"): (
            ({"ensemble_sha_matches": True}, "matches_incumbent"),
            ({"ensemble_sha_matches": False}, "other_manifest"),
        ),
        ("ensemble", "ensemble_no_sha"): (({}, "ensemble_sha_missing"),),
        ("ensemble", "v1"): (({}, "v1_unknown_provenance"),),
        # r1: a single-model artifact must be stopped here, NOT handed to the
        # retired model's sidecar compare.
        ("ensemble", "single"): (
            ({"single_sha_mismatch": False}, "shape_single_under_ensemble"),
            ({"single_sha_mismatch": True}, "shape_single_under_ensemble"),
            ({"single_sha_mismatch": None}, "shape_single_under_ensemble"),
        ),
        # ---- incumbent = single (reached ONLY via the explicit opt-out) ----
        ("single", "ensemble"): (({}, "ensemble_under_single"),),
        # r5: the meta.ensemble block DECLARES the shape — losing the digest
        # loses the identity, not the shape. Against a CONFIRMED single-model
        # incumbent that is still a provable mismatch, so it must keep the
        # 请勿据此下单 refusal rather than drop to "身份无法绑定".
        ("single", "ensemble_no_sha"): (({}, "ensemble_under_single"),),
        ("single", "v1"): (({}, "v1_unknown_provenance"),),
        ("single", "single"): (
            ({"single_sha_mismatch": True}, "single_sha_mismatch"),
            ({"single_sha_mismatch": None}, "single_sha_unknown"),
            ({"single_sha_mismatch": False}, "single_sha_ok"),
        ),
        # ---- incumbent = unresolvable (pointer set, validator refused) ----
        ("unresolvable", "ensemble"): (
            ({"ensemble_sha_matches": False}, "incumbent_unresolved"),
        ),
        ("unresolvable", "ensemble_no_sha"): (({}, "ensemble_sha_missing"),),
        ("unresolvable", "v1"): (({}, "v1_unknown_provenance"),),
        # r4: this cell had no branch of its own and fell through to the
        # legacy sidecar compare against the RETIRED model — where a matching
        # sha emitted NO warning at all and the artifact read as verified.
        ("unresolvable", "single"): (
            ({"single_sha_mismatch": False}, "incumbent_unresolved"),
            ({"single_sha_mismatch": True}, "incumbent_unresolved"),
            ({"single_sha_mismatch": None}, "incumbent_unresolved"),
        ),
    }

    def test_the_table_itself_covers_every_cell(self) -> None:
        # Guards the TEST, not the code: a table that quietly omits a pair
        # would pass every assertion below while checking nothing about it.
        from web.operator_ui.pages import _daily_decision_helpers as h
        want = {(i, a) for i in h.INCUMBENT_KINDS for a in h.ARTIFACT_KINDS}
        self.assertEqual(want, set(self.CELLS), "矩阵有格子没写进表")
        self.assertEqual(12, len(want), "现任 3 态 × 工件 4 形 = 12 格")

    def test_every_cell_resolves_to_the_expected_verdict(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import (
            classify_provenance,
        )
        for (inc, art), cases in self.CELLS.items():
            for kwargs, want in cases:
                with self.subTest(incumbent=inc, artifact=art, **kwargs):
                    got = classify_provenance(
                        incumbent_kind=inc, artifact_kind=art, **kwargs)
                    self.assertEqual(want, got)

    def test_only_one_cell_is_allowed_to_say_nothing(self) -> None:
        # Silence is exactly how a non-incumbent artifact gets presented as
        # safe. Exactly ONE pair may be silent: the incumbent is a single
        # model AND the artifact's sidecar sha equals it.
        silent = {cell for cell, cases in self.CELLS.items()
                  for _kw, want in cases if want == "single_sha_ok"}
        self.assertEqual({("single", "single")}, silent)

    def test_unknown_inputs_raise_instead_of_falling_through(self) -> None:
        # A future shape (say a third serving topology) must break loudly,
        # not silently land in whichever cell the code happens to check last.
        from web.operator_ui.pages._daily_decision_helpers import (
            classify_provenance,
        )
        with self.assertRaises(ValueError):
            classify_provenance(incumbent_kind="nope", artifact_kind="v1")
        with self.assertRaises(ValueError):
            classify_provenance(incumbent_kind="ensemble", artifact_kind="nope")

    def test_shape_mismatch_outranks_every_unknown(self) -> None:
        # codex #430 r5, stated as the RULE rather than as one more cell: a
        # shape mismatch is the only DEFINITE refusal derivable with no
        # identity at all, so no "unknown" may soften it. Ordering it after
        # the unknowns is exactly what made ("single", "ensemble_no_sha")
        # under-warn.
        from web.operator_ui.pages._daily_decision_helpers import (
            VERDICT_ENSEMBLE_UNDER_SINGLE,
            VERDICT_SHAPE_SINGLE_UNDER_ENSEMBLE,
            classify_provenance,
        )
        # Every artifact whose meta DECLARES an ensemble shape — digest
        # present or not — is a provable mismatch under a confirmed single.
        for art in ("ensemble", "ensemble_no_sha"):
            with self.subTest(artifact=art):
                self.assertEqual(
                    VERDICT_ENSEMBLE_UNDER_SINGLE,
                    classify_provenance(
                        incumbent_kind="single", artifact_kind=art))
        # ...and the mirror direction, regardless of what the retired
        # model's sidecar comparison would have said.
        for mismatch in (True, False, None):
            with self.subTest(sha_mismatch=mismatch):
                self.assertEqual(
                    VERDICT_SHAPE_SINGLE_UNDER_ENSEMBLE,
                    classify_provenance(
                        incumbent_kind="ensemble", artifact_kind="single",
                        single_sha_mismatch=mismatch))

    def test_declared_shape_survives_a_missing_digest(self) -> None:
        # The distinction the r5 bug collapsed: identity and shape are
        # separate facts. `ensemble_no_sha` has lost only the former.
        from web.operator_ui.pages._daily_decision_helpers import (
            _ARTIFACT_SHAPE,
        )
        self.assertEqual(
            _ARTIFACT_SHAPE["ensemble"], _ARTIFACT_SHAPE["ensemble_no_sha"])
        self.assertIsNone(_ARTIFACT_SHAPE["v1"], "v1 连形态都无从得知")

    def test_artifact_kind_of_maps_each_shape_to_exactly_one_kind(self) -> None:
        # The matrix is only total if the shape classifier is ONTO it.
        from web.operator_ui.pages._daily_decision_helpers import (
            ARTIFACT_KINDS,
            artifact_kind_of,
            artifact_meta_status,
        )
        payloads = {
            "ensemble": {"meta": {"ensemble": {"manifest_sha256": "cc" * 32}}},
            "ensemble_no_sha": {"meta": {"ensemble": {}}},
            "v1": {"picks": []},
            "single": {"meta": {"model_pkl_sha256": "aa"}},
        }
        self.assertEqual(set(ARTIFACT_KINDS), set(payloads))
        for want, payload in payloads.items():
            with self.subTest(kind=want):
                got = artifact_kind_of(artifact_meta_status(payload, "aa"))
                self.assertEqual(want, got)


def _dispatch_segments(page: str) -> dict[str, str]:
    """Split the page's verdict dispatch into one source segment per verdict.

    Slicing by verdict beats a fixed-size window: a window that runs past its
    branch starts passing on the NEIGHBOUR's words (the previous window had
    193 characters of margin before it reached another branch carrying the
    same token), and a window that is only asked whether two tokens co-occur
    cannot tell which arm of a conditional each one belongs to.
    """
    import re
    block = page[page.index("_verdict = provenance_verdict("):]
    # Bound the LAST branch at the fail-loud tail — otherwise its "segment"
    # runs to end of file and picks up every st.* call on the page.
    block = block[:block.index("elif _verdict != VERDICT_SINGLE_SHA_OK:")]
    parts = re.split(r"^(?:if|elif) _verdict == (VERDICT_\w+):$", block, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


class ProvenanceWiringTests(unittest.TestCase):
    """After the matrix moved to a pure function, the page's LAST piece of
    logic was the four call-site arguments — and a source-level pin cannot
    tell `incumbent_kind=incumbent.kind` from a plausible-looking
    `"ensemble" if incumbent.is_ensemble else "single"`. So the wiring is a
    function too, and these tests drive it with real dataclass values."""

    def _identity(self, **kw: object) -> object:
        from web.operator_ui.pages._daily_decision_helpers import (
            IncumbentIdentity,
        )
        return IncumbentIdentity(**kw)  # type: ignore[arg-type]

    def _status(self, payload: dict, current_sha: str | None = None) -> object:
        from web.operator_ui.pages._daily_decision_helpers import (
            artifact_meta_status,
        )
        return artifact_meta_status(payload, current_sha)

    def test_page_delegates_the_whole_wiring(self) -> None:
        # If the page ever calls classify_provenance directly again, the four
        # arguments come back into un-runnable source-only territory.
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn(
            "_verdict = provenance_verdict(_incumbent, _meta_status)", page)
        self.assertNotIn("classify_provenance(", page)

    def test_unresolvable_incumbent_is_not_collapsed_into_single(self) -> None:
        # THE r4 miswiring, as behaviour: an unconfirmed incumbent plus an old
        # single-model artifact whose sidecar sha happens to MATCH the retired
        # model. Collapse unresolvable→single here and the verdict becomes the
        # one silent cell — the page says nothing and the artifact reads as
        # verified.
        from web.operator_ui.pages._daily_decision_helpers import (
            VERDICT_INCUMBENT_UNRESOLVED,
            VERDICT_SINGLE_SHA_OK,
            provenance_verdict,
        )
        inc = self._identity(
            kind="unresolvable", manifest_path="Z:/broken.json", error="boom")
        status = self._status({"meta": {"model_pkl_sha256": "aa"}}, "aa")
        got = provenance_verdict(inc, status)  # type: ignore[arg-type]
        self.assertNotEqual(VERDICT_SINGLE_SHA_OK, got, "静默 = 读起来像已核对")
        self.assertEqual(VERDICT_INCUMBENT_UNRESOLVED, got)

    def test_sidecar_comparison_is_really_forwarded(self) -> None:
        # The other uncovered argument: hard-wire single_sha_mismatch and a
        # stale artifact under an explicit single-model incumbent goes silent.
        from web.operator_ui.pages._daily_decision_helpers import (
            VERDICT_SINGLE_SHA_MISMATCH,
            VERDICT_SINGLE_SHA_OK,
            VERDICT_SINGLE_SHA_UNKNOWN,
            provenance_verdict,
        )
        inc = self._identity(kind="single")
        payload = {"meta": {"model_pkl_sha256": "aa"}}
        for current, want in (
            ("bb", VERDICT_SINGLE_SHA_MISMATCH),
            ("aa", VERDICT_SINGLE_SHA_OK),
            (None, VERDICT_SINGLE_SHA_UNKNOWN),
        ):
            with self.subTest(current_model_sha=current):
                self.assertEqual(
                    want,
                    provenance_verdict(  # type: ignore[arg-type]
                        inc, self._status(payload, current)))

    def test_manifest_digests_are_compared_not_assumed(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import (
            VERDICT_MATCHES_INCUMBENT,
            VERDICT_OTHER_MANIFEST,
            provenance_verdict,
        )
        inc = self._identity(
            kind="ensemble", manifest_path="m.json", manifest_sha256="cc" * 32)
        for art_sha, want in (("cc" * 32, VERDICT_MATCHES_INCUMBENT),
                              ("dd" * 32, VERDICT_OTHER_MANIFEST)):
            with self.subTest(artifact_sha=art_sha[:4]):
                status = self._status(
                    {"meta": {"ensemble": {"manifest_sha256": art_sha}}})
                self.assertEqual(
                    want, provenance_verdict(inc, status))  # type: ignore[arg-type]

    def test_a_bindable_digest_is_a_precondition_of_the_comparison(self) -> None:
        # provenance_verdict guards against two empty digests comparing equal
        # into the page's only green light. That guard is REDUNDANT today for
        # exactly one reason: artifact_kind_of routes every digest-less
        # ensemble artifact to `ensemble_no_sha`, which the matrix answers
        # before any comparison happens. Pin that routing — it is what makes
        # the guard redundant, and if it breaks the guard becomes the only
        # thing standing between an empty digest and a green "与现任一致".
        from web.operator_ui.pages._daily_decision_helpers import (
            ArtifactMetaStatus,
            artifact_kind_of,
        )
        for empty in ("", None):
            with self.subTest(artifact_ensemble_sha=empty):
                status = ArtifactMetaStatus(
                    artifact_is_v1=False, artifact_is_corrupt_v2=False,
                    artifact_model_sha=None, current_model_sha=None,
                    sha_mismatch=None, artifact_is_ensemble=True,
                    artifact_ensemble_sha=empty)
                self.assertEqual("ensemble_no_sha", artifact_kind_of(status))

    def test_an_incumbent_without_a_digest_never_reads_as_a_match(self) -> None:
        # Defensive: two empty digests comparing equal would hand out the
        # page's ONLY green light on no evidence at all.
        from web.operator_ui.pages._daily_decision_helpers import (
            VERDICT_MATCHES_INCUMBENT,
            provenance_verdict,
        )
        inc = self._identity(
            kind="ensemble", manifest_path="m.json", manifest_sha256=None)
        status = self._status(
            {"meta": {"ensemble": {"manifest_sha256": "cc" * 32}}})
        self.assertNotEqual(
            VERDICT_MATCHES_INCUMBENT,
            provenance_verdict(inc, status))  # type: ignore[arg-type]


class ProvenanceRenderingTests(unittest.TestCase):
    """The page's only remaining job is turning a verdict into words. Pinning
    the CONSTANT NAME is not enough — a branch gutted to
    ``st.caption("提示。")`` keeps its name and loses the refusal."""

    # One phrase per verdict that must appear in ITS branch and in NO other.
    DISTINCTIVE = {
        "VERDICT_MATCHES_INCUMBENT": "与现任一致",
        "VERDICT_OTHER_MANIFEST": "出自**另一份 manifest**",
        "VERDICT_ENSEMBLE_UNDER_SINGLE": "现任是单模型形态",
        "VERDICT_INCUMBENT_UNRESOLVED": "现任 manifest 不可解析",
        "VERDICT_ENSEMBLE_SHA_MISSING": "请核对工件来源",
        "VERDICT_V1_UNKNOWN": "旧版工件",
        "VERDICT_SHAPE_SINGLE_UNDER_ENSEMBLE": "该工件是**单模型形态**",
        "VERDICT_SINGLE_SHA_MISMATCH": "由**其他模型**生成",
        "VERDICT_SINGLE_SHA_UNKNOWN": "无法交叉核对工件↔模型",
    }
    # Verdicts that are DEFINITE refusals: the artifact is provably not the
    # incumbent's output, or its provenance cannot be confirmed at all.
    REFUSALS = (
        "VERDICT_OTHER_MANIFEST",
        "VERDICT_ENSEMBLE_UNDER_SINGLE",
        "VERDICT_INCUMBENT_UNRESOLVED",
        "VERDICT_SHAPE_SINGLE_UNDER_ENSEMBLE",
    )

    def setUp(self) -> None:
        self.page = _PAGE.read_text(encoding="utf-8")
        self.xcheck = self.page[
            self.page.index("_verdict = provenance_verdict("):]
        self.seg = _dispatch_segments(self.page)

    def test_every_verdict_has_its_own_branch(self) -> None:
        # Every verdict the helper can return is dispatched — except the one
        # that is deliberately silent, which the fail-loud tail handles.
        from web.operator_ui.pages import _daily_decision_helpers as h
        names = {n for n in dir(h) if n.startswith("VERDICT_")}
        self.assertEqual(names - {"VERDICT_SINGLE_SHA_OK"}, set(self.seg))

    def test_each_branch_says_its_own_words(self) -> None:
        # ...and only its own: a phrase that also appears next door would let
        # a gutted branch pass on the neighbour's text.
        self.assertEqual(set(self.DISTINCTIVE), set(self.seg))
        for name, phrase in self.DISTINCTIVE.items():
            with self.subTest(verdict=name):
                self.assertIn(phrase, self.seg[name])
                elsewhere = [o for o, body in self.seg.items()
                             if o != name and phrase in body]
                self.assertEqual([], elsewhere, f"{phrase} 不该出现在别的分支")

    def test_definite_refusals_are_warnings_that_forbid_trading(self) -> None:
        # spec.md writes 请勿据此下单 as a MUST; before this test every one of
        # the page's five occurrences could be deleted with the suite green.
        for name in self.REFUSALS:
            with self.subTest(verdict=name):
                self.assertIn("st.warning(", self.seg[name])
                self.assertIn("请勿据此下单", self.seg[name])

    def test_the_only_green_light_is_the_incumbent_match(self) -> None:
        infos = [n for n, body in self.seg.items() if "st.info(" in body]
        self.assertEqual(["VERDICT_MATCHES_INCUMBENT"], infos)

    def test_an_unrendered_verdict_fails_loud(self) -> None:
        # The dispatch must not end in a bare `else: pass`: "no message" is
        # indistinguishable from "checked and fine".
        self.assertIn("elif _verdict != VERDICT_SINGLE_SHA_OK:", self.xcheck)
        self.assertIn("未渲染的来源裁定", self.xcheck)

    def test_the_digest_text_is_bound_to_the_arm_that_has_a_digest(self) -> None:
        # r5 made this branch reachable with an EMPTY _art_sha. Asserting only
        # that both strings occur somewhere nearby cannot tell the two arms
        # apart — swap them and the page prints a digest that does not exist
        # for the artifact that lacks one, and denies the digest of the one
        # that has it. Pin the conditional itself.
        seg = self.seg["VERDICT_ENSEMBLE_UNDER_SINGLE"]
        self.assertIn('f"(sha256 `{_art_sha[:12]}…`)" if _art_sha', seg)
        self.assertIn('else "(meta.ensemble 缺 manifest_sha256', seg)

    def test_ensemble_artifact_under_single_incumbent_is_known(self) -> None:
        # An explicit `none` opt-out is a DEFINITE single-model incumbent, so
        # an ensemble artifact provably did not come from it.
        i_known = self.xcheck.index("VERDICT_ENSEMBLE_UNDER_SINGLE")
        i_unknown = self.xcheck.index("VERDICT_INCUMBENT_UNRESOLVED")
        self.assertLess(i_known, i_unknown)

    def test_single_incumbent_message_names_the_explicit_opt_out(self) -> None:
        # After the default-manifest change, `single` is reachable ONLY via
        # the explicit sentinel; telling operators 变量未设 would send them
        # troubleshooting in the opposite direction.
        seg = self.seg["VERDICT_ENSEMBLE_UNDER_SINGLE"]
        self.assertIn("显式设为 `none`", seg)
        self.assertNotIn("(QUANT_ENSEMBLE_MANIFEST 未设)", self.xcheck)

    def test_unresolvable_incumbent_never_reaches_the_legacy_compare(self) -> None:
        # r4: the legacy sidecar compare is against the RETIRED single model.
        # It may only run when the incumbent is a CONFIRMED single model.
        from web.operator_ui.pages._daily_decision_helpers import (
            VERDICT_INCUMBENT_UNRESOLVED,
            classify_provenance,
        )
        for mismatch in (True, False, None):
            with self.subTest(sha_mismatch=mismatch):
                self.assertEqual(
                    VERDICT_INCUMBENT_UNRESOLVED,
                    classify_provenance(
                        incumbent_kind="unresolvable", artifact_kind="single",
                        single_sha_mismatch=mismatch))

    def test_v1_artifact_is_not_called_single_model_shaped(self) -> None:
        # r2: a v1 artifact carries no meta at all — its provenance is
        # unknown, so the matrix gives it its own verdict under EVERY
        # incumbent rather than folding it into the shape check.
        from web.operator_ui.pages._daily_decision_helpers import (
            INCUMBENT_KINDS,
            VERDICT_V1_UNKNOWN,
            classify_provenance,
        )
        for inc in INCUMBENT_KINDS:
            with self.subTest(incumbent=inc):
                self.assertEqual(
                    VERDICT_V1_UNKNOWN,
                    classify_provenance(incumbent_kind=inc, artifact_kind="v1"))


class ProposalConsistencyTests(unittest.TestCase):
    """A change whose proposal contradicts its own spec/implementation would
    archive contradictory governance history (codex #430 r2)."""

    def test_proposal_does_not_claim_unset_means_single(self) -> None:
        prop = (_ROOT / "openspec" / "changes"
                / "2026-08-14-ui-incumbent-ensemble-identity"
                / "proposal.md").read_text(encoding="utf-8")
        self.assertIn("未设 ≠ 单模型", prop)
        self.assertNotIn("单模型（未设该变量）", prop)
