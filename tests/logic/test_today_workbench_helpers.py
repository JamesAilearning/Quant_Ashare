"""Provenance and operation-state coverage for Today Workbench summaries."""

from __future__ import annotations

import unittest

from web.operator_ui.incumbent import IncumbentIdentity
from web.operator_ui.job_io import JobSummary
from web.operator_ui.pages._today_workbench_helpers import (
    summarise_daily_signal,
    summarise_operations,
)


def _ensemble_payload(
    *, rebalance_day: bool = True, manifest: str = "manifest"
) -> dict[str, object]:
    return {
        "artifact_schema_version": 2,
        "as_of_date": "2026-08-18",
        "entry_date": "2026-08-19",
        "rebalance_day": rebalance_day,
        "next_rebalance_date": "2026-08-25",
        "meta": {"ensemble": {"manifest_sha256": manifest}},
        "picks": [],
    }


class DailySignalSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.incumbent = IncumbentIdentity(
            kind="ensemble", manifest_sha256="manifest"
        )

    def test_matching_rebalance_artifact_is_not_an_execution_instruction(self) -> None:
        result = summarise_daily_signal(
            "2026-08-18",
            _ensemble_payload(),
            incumbent=self.incumbent,
            current_model_sha=None,
        )
        self.assertEqual(result.kind, "rebalance")
        self.assertIn("人工核对", result.detail)
        self.assertEqual(result.entry_date, "2026-08-19")

    def test_matching_hold_artifact_is_explicitly_non_actionable(self) -> None:
        result = summarise_daily_signal(
            "2026-08-18",
            _ensemble_payload(rebalance_day=False),
            incumbent=self.incumbent,
            current_model_sha=None,
        )
        self.assertEqual(result.kind, "hold")
        self.assertIn("不构成入场指令", result.detail)

    def test_provenance_or_payload_mismatch_never_becomes_a_signal(self) -> None:
        cases = (
            ("other manifest", _ensemble_payload(manifest="other")),
            (
                "wrong payload date",
                {**_ensemble_payload(), "as_of_date": "2026-08-17"},
            ),
            (
                "corrupt v2",
                {
                    "artifact_schema_version": 2,
                    "as_of_date": "2026-08-18",
                    "entry_date": "2026-08-19",
                },
            ),
        )
        for label, payload in cases:
            with self.subTest(label=label):
                result = summarise_daily_signal(
                    "2026-08-18",
                    payload,
                    incumbent=self.incumbent,
                    current_model_sha=None,
                )
                self.assertEqual(result.kind, "needs_verification")

    def test_invalid_picks_shape_never_becomes_a_current_signal(self) -> None:
        cases: tuple[tuple[str, object], ...] = (
            ("missing", None),
            ("not a list", "not-a-list"),
            ("non-object member", ["not-a-dict"]),
        )
        for label, picks in cases:
            with self.subTest(label=label):
                payload = _ensemble_payload()
                if label == "missing":
                    payload.pop("picks")
                else:
                    payload["picks"] = picks
                result = summarise_daily_signal(
                    "2026-08-18",
                    payload,
                    incumbent=self.incumbent,
                    current_model_sha=None,
                )
                self.assertEqual(result.kind, "needs_verification")
                self.assertIn("候选列表", result.detail)

    def test_empty_picks_remains_a_valid_rebalance_artifact(self) -> None:
        result = summarise_daily_signal(
            "2026-08-18",
            _ensemble_payload(),
            incumbent=self.incumbent,
            current_model_sha=None,
        )
        self.assertEqual(result.kind, "rebalance")


def _job(*, run_id: str, status: str, finished_at: str = "") -> JobSummary:
    return JobSummary(
        run_id=run_id,
        type="pipeline",
        status=status,
        finished_at=finished_at,
        error_message="failed detail" if status == "failed" else "",
    )


class OperationSummaryTests(unittest.TestCase):
    def test_running_job_has_priority_over_prior_failure(self) -> None:
        result = summarise_operations(
            (
                _job(
                    run_id="old-failure",
                    status="failed",
                    finished_at="2026-08-18T09:00:00Z",
                ),
                _job(
                    run_id="current",
                    status="running",
                    finished_at="2026-08-18T10:00:00Z",
                ),
            )
        )
        self.assertEqual(result.kind, "running")
        self.assertEqual(result.job.run_id if result.job else None, "current")

    def test_pending_job_is_not_misreported_as_idle(self) -> None:
        result = summarise_operations(
            (_job(run_id="queued", status="pending"),)
        )
        self.assertEqual(result.kind, "pending")
        self.assertEqual(result.job.run_id if result.job else None, "queued")

    def test_latest_exception_is_surfaced_when_nothing_is_running(self) -> None:
        result = summarise_operations(
            (
                _job(
                    run_id="old",
                    status="failed",
                    finished_at="2026-08-18T09:00:00Z",
                ),
                _job(
                    run_id="new",
                    status="stopped",
                    finished_at="2026-08-18T10:00:00Z",
                ),
            )
        )
        self.assertEqual(result.kind, "attention")
        self.assertEqual(result.job.run_id if result.job else None, "new")

    def test_partial_job_is_surfaced_as_an_exception(self) -> None:
        result = summarise_operations(
            (_job(run_id="partial", status="partial"),)
        )
        self.assertEqual(result.kind, "attention")
        self.assertEqual(result.job.run_id if result.job else None, "partial")


if __name__ == "__main__":
    unittest.main()
