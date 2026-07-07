"""Tests for the sidebar REGEN-2 anchor-health badge (PR-B).

Every probe path is injectable — no real gh/git/network dependency; the only
real-file test reads the repo's own baseline fixture. Spec:
``add-anchor-health-badge`` (v2-operator-ui-console ADDED).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from web.operator_ui.anchor_health import (
    ANCHOR_JOB_NAME,
    BASELINE_PATH,
    _ProbeFailure,
    baseline_identity,
    ci_leg_status,
    evidence_sidecar_for,
    normalized_sha256,
)

_ROOT = Path(__file__).resolve().parents[2]


class NormalizedShaTests(unittest.TestCase):
    def test_crlf_and_lf_variants_hash_identically(self) -> None:
        # The badge MUST agree with the anchor test's checkout-stable hash:
        # CRLF and LF checkouts of the same content produce one digest.
        with tempfile.TemporaryDirectory() as tmp:
            lf = Path(tmp) / "lf.json"
            crlf = Path(tmp) / "crlf.json"
            lf.write_bytes(b'{"a": 1}\n{"b": 2}\n')
            crlf.write_bytes(b'{"a": 1}\r\n{"b": 2}\r\n')
            self.assertEqual(normalized_sha256(lf), normalized_sha256(crlf))

    def test_real_baseline_fixture_is_hashable(self) -> None:
        self.assertTrue(BASELINE_PATH.is_file(), BASELINE_PATH)
        digest = normalized_sha256(BASELINE_PATH)
        self.assertEqual(len(digest), 64)


class BaselineIdentityTests(unittest.TestCase):
    def test_injected_git_output_is_parsed(self) -> None:
        identity = baseline_identity(
            run=lambda cmd: "2026-06-26T10:00:00+08:00 abc1234\n",
        )
        self.assertEqual(identity.signed_at, "2026-06-26T10:00:00+08:00")
        self.assertEqual(identity.signed_commit, "abc1234")
        self.assertIsNotNone(identity.sha8)
        self.assertEqual(len(identity.sha8 or ""), 8)

    def test_git_failure_degrades_to_unknown_not_guess(self) -> None:
        def _boom(cmd: list[str]) -> str:
            raise _ProbeFailure("no git / shallow clone")

        identity = baseline_identity(run=_boom)
        self.assertIsNone(identity.signed_at)
        self.assertIsNone(identity.signed_commit)
        self.assertIsNotNone(identity.sha8)  # local sha still resolves

    def test_missing_baseline_file_degrades_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ghost = Path(tmp) / "nope.json"
            identity = baseline_identity(
                baseline_path=ghost, run=lambda cmd: "",
            )
        self.assertIsNone(identity.sha8)
        self.assertFalse(identity.evidence_present)

    def test_evidence_sidecar_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "b.json"
            base.write_text("{}", encoding="utf-8")
            self.assertFalse(
                baseline_identity(
                    baseline_path=base, run=lambda cmd: "",
                ).evidence_present
            )
            # The WRONG (appended) name must NOT be mistaken for the sidecar —
            # this is exactly the drift codex #335 flagged.
            (Path(tmp) / "b.json.evidence.json").write_text("{}", encoding="utf-8")
            self.assertFalse(
                baseline_identity(
                    baseline_path=base, run=lambda cmd: "",
                ).evidence_present
            )
            # The canonical stem-based name is what counts.
            (Path(tmp) / "b.evidence.json").write_text("{}", encoding="utf-8")
            self.assertTrue(
                baseline_identity(
                    baseline_path=base, run=lambda cmd: "",
                ).evidence_present
            )

    def test_sidecar_name_matches_regression_guard_literal(self) -> None:
        # Cross-reference: the badge's sidecar name MUST equal the canonical
        # guard's committed literal, or the badge and the anchor test would
        # disagree about whether a re-signed baseline shipped its evidence.
        guard = (_ROOT / "tests/regression"
                 / "test_walk_forward_replay_baseline_regen2.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("walk_forward_baseline_metrics.evidence.json", guard)
        self.assertEqual(
            evidence_sidecar_for(BASELINE_PATH).name,
            "walk_forward_baseline_metrics.evidence.json",
        )


class CiLegStatusTests(unittest.TestCase):
    @staticmethod
    def _runner(list_out: str, view_out: str):  # type: ignore[no-untyped-def]
        def run(cmd: list[str]) -> str:
            if cmd[:3] == ["gh", "run", "list"]:
                return list_out
            if cmd[:3] == ["gh", "run", "view"]:
                return view_out
            raise AssertionError(f"unexpected cmd {cmd}")

        return run

    def test_anchor_leg_conclusion_resolved(self) -> None:
        list_out = json.dumps([
            {"databaseId": 42, "conclusion": "failure",
             "url": "https://x/runs/42"},
        ])
        view_out = json.dumps({"jobs": [
            {"name": "test (windows-latest, 3.10)", "conclusion": "success"},
            {"name": ANCHOR_JOB_NAME, "conclusion": "success"},
        ]})
        status = ci_leg_status(run=self._runner(list_out, view_out))
        # The LEG conclusion wins over the whole-run conclusion.
        self.assertEqual(status.conclusion, "success")
        self.assertEqual(status.url, "https://x/runs/42")
        self.assertEqual(status.detail, "")

    def test_missing_anchor_job_falls_back_to_run_conclusion(self) -> None:
        list_out = json.dumps([
            {"databaseId": 42, "conclusion": "success", "url": "u"},
        ])
        view_out = json.dumps({"jobs": [
            {"name": "some other job", "conclusion": "success"},
        ]})
        status = ci_leg_status(run=self._runner(list_out, view_out))
        self.assertEqual(status.conclusion, "success")
        self.assertIn("未找到锚腿", status.detail)

    def test_gh_absent_or_timeout_degrades_honestly(self) -> None:
        def _boom(cmd: list[str]) -> str:
            raise _ProbeFailure("FileNotFoundError: gh")

        status = ci_leg_status(run=_boom)
        self.assertEqual(status.conclusion, "unknown")
        self.assertIn("gh 不可用", status.detail)

    def test_unparsable_and_empty_outputs_degrade(self) -> None:
        status = ci_leg_status(run=self._runner("not json", "{}"))
        self.assertEqual(status.conclusion, "unknown")
        self.assertIn("不可解析", status.detail)
        status = ci_leg_status(run=self._runner("[]", "{}"))
        self.assertEqual(status.conclusion, "unknown")
        self.assertIn("无已完成", status.detail)

    def test_jobs_fetch_failure_falls_back_to_run_conclusion(self) -> None:
        list_out = json.dumps([
            {"databaseId": 42, "conclusion": "failure", "url": "u"},
        ])

        def run(cmd: list[str]) -> str:
            if cmd[:3] == ["gh", "run", "list"]:
                return list_out
            raise _ProbeFailure("TimeoutExpired")

        status = ci_leg_status(run=run)
        self.assertEqual(status.conclusion, "failure")
        self.assertIn("已用整 run 结论", status.detail)


class SourceContractTests(unittest.TestCase):
    def test_module_has_no_streamlit_dependency(self) -> None:
        src = (_ROOT / "web/operator_ui/anchor_health.py").read_text(
            encoding="utf-8",
        )
        self.assertNotIn("streamlit", src)

    def test_app_renders_cached_badge(self) -> None:
        app = (_ROOT / "web/operator_ui/app.py").read_text(encoding="utf-8")
        self.assertIn("st.cache_data(ttl=600", app)   # pull-based, no polling
        self.assertIn("_anchor_badge_probe", app)
        self.assertIn("REGEN-2 锚", app)
        self.assertIn("evidence", app)


if __name__ == "__main__":
    unittest.main()
