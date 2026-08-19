"""Tests for the read-only configuration review model."""

from __future__ import annotations

import unittest

from web.operator_ui.pages._config_run_helpers import (
    build_config_review_sections,
    config_preset_differences,
    unsupported_prefill_keys,
)


class ConfigRunReviewHelperTests(unittest.TestCase):
    def test_review_keeps_every_emitted_field_in_a_stable_section(self) -> None:
        emitted = {
            "mode": "pipeline",
            "provider_uri": "D:/data",
            "instruments": "csi300",
            "benchmark_code": "SH000300TR",
            "compute_device": "cpu",
            "future_runtime_key": "visible",
        }

        sections = build_config_review_sections(emitted)

        flattened = [key for section in sections for key, _ in section.rows]
        self.assertEqual(flattened, list(emitted))
        self.assertEqual(sections[-1].title, "其他已提交设置")
        self.assertEqual(sections[-1].rows, (("future_runtime_key", "visible"),))

    def test_preset_difference_marks_missing_fields_without_defaulting_them(self) -> None:
        differences = config_preset_differences(
            {"mode": "pipeline", "topk": 50},
            {"mode": "pipeline", "benchmark_code": "SH000300TR"},
        )

        assert differences is not None
        self.assertEqual([difference.key for difference in differences], ["topk", "benchmark_code"])
        self.assertTrue(differences[0].emitted_present)
        self.assertFalse(differences[0].preset_present)
        self.assertFalse(differences[1].emitted_present)
        self.assertTrue(differences[1].preset_present)

    def test_matching_preset_has_no_differences(self) -> None:
        emitted = {"mode": "walk_forward", "ensemble_window": 3}

        self.assertEqual(config_preset_differences(emitted, dict(emitted)), ())

    def test_unavailable_preset_is_not_reported_as_a_match(self) -> None:
        self.assertIsNone(config_preset_differences({"mode": "pipeline"}, None))

    def test_prefill_fields_not_emitted_are_named_for_operator_review(self) -> None:
        unsupported = unsupported_prefill_keys(
            {"mode": "pipeline", "topk": 50, "legacy_toggle": True},
            {"mode": "pipeline", "topk": 50},
        )

        self.assertEqual(unsupported, ("legacy_toggle",))


if __name__ == "__main__":
    unittest.main()
