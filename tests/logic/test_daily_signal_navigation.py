"""Behaviour coverage for explicit daily-signal routing."""

from __future__ import annotations

import unittest

from web.operator_ui.daily_signal_navigation import (
    DAILY_DECISION_DATE_KEY,
    DAILY_DECISION_REQUESTED_DATE_KEY,
    prepare_daily_decision_selection,
    published_recommendation_date,
    recommendation_artifact_date,
)


class RecommendationArtifactDateTests(unittest.TestCase):
    def test_only_canonical_dated_json_is_routable(self) -> None:
        self.assertEqual(
            recommendation_artifact_date("daily_recommendation_2026-08-18.json"),
            "2026-08-18",
        )
        self.assertEqual(
            recommendation_artifact_date("nested\\daily_recommendation_2026-08-18.json"),
            "2026-08-18",
        )
        for value in (
            "daily_recommendation_2026-08-18.csv",
            "daily_recommendation_2026-02-30.json",
            "daily_recommendation_latest.json",
        ):
            with self.subTest(value=value):
                self.assertIsNone(recommendation_artifact_date(value))

    def test_success_route_requires_exactly_one_dated_json(self) -> None:
        self.assertEqual(
            published_recommendation_date(
                (
                    "daily_recommendation_2026-08-18.json",
                    "daily_recommendation_2026-08-18.csv",
                )
            ),
            "2026-08-18",
        )
        self.assertIsNone(
            published_recommendation_date(
                (
                    "daily_recommendation_2026-08-18.json",
                    "daily_recommendation_2026-08-17.json",
                )
            )
        )


class SelectionHandoffTests(unittest.TestCase):
    def test_valid_requested_date_seeds_selectbox_once(self) -> None:
        state: dict[str, object] = {
            DAILY_DECISION_REQUESTED_DATE_KEY: "2026-08-18",
            DAILY_DECISION_DATE_KEY: "2026-08-17",
        }
        selected = prepare_daily_decision_selection(
            state, ("2026-08-18", "2026-08-17")
        )
        self.assertEqual(selected, "2026-08-18")
        self.assertEqual(state[DAILY_DECISION_DATE_KEY], "2026-08-18")
        self.assertNotIn(DAILY_DECISION_REQUESTED_DATE_KEY, state)

    def test_stale_requested_and_selected_dates_are_discarded(self) -> None:
        state: dict[str, object] = {
            DAILY_DECISION_REQUESTED_DATE_KEY: "2026-08-01",
            DAILY_DECISION_DATE_KEY: "2026-08-02",
        }
        selected = prepare_daily_decision_selection(state, ("2026-08-18",))
        self.assertIsNone(selected)
        self.assertNotIn(DAILY_DECISION_REQUESTED_DATE_KEY, state)
        self.assertNotIn(DAILY_DECISION_DATE_KEY, state)


if __name__ == "__main__":
    unittest.main()
