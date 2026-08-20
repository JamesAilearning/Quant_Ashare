"""Tests for the read-only configuration review model."""

from __future__ import annotations

import unittest

from web.operator_ui.pages._config_run_helpers import (
    build_config_review_sections,
    config_preset_differences,
    effective_preset_for_review,
    snapshot_preset_for_review,
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

    def test_effective_preset_matches_generated_fields_and_omits_local_paths(self) -> None:
        emitted = {
            "mode": "pipeline",
            "provider_uri": "D:/machine-a/provider",
            "namechange_path": "D:/machine-a/namechange.csv",
            "train_start": "2022-01-01",
            "train_end": "2024-12-31",
            "topk": 50,
            "commission_rate": 0.0005,
        }
        raw_preset = {"mode": "pipeline", "topk": 50}

        effective = effective_preset_for_review(
            emitted,
            raw_preset,
            normalization_defaults={"commission_rate": 0.0005},
        )

        assert effective is not None
        self.assertEqual(config_preset_differences(emitted, effective), ())
        self.assertNotIn("provider_uri", effective)
        self.assertNotIn("namechange_path", effective)

    def test_preset_snapshot_keeps_the_before_edit_review_baseline(self) -> None:
        before_edit = {
            "mode": "pipeline",
            "topk": 50,
            "train_start": "2022-01-01",
            "commission_rate": 0.0005,
        }
        raw_preset = {"mode": "pipeline", "topk": 50}
        baseline = snapshot_preset_for_review(
            before_edit,
            raw_preset,
            normalization_defaults={"commission_rate": 0.0005},
            snapshot=None,
        )
        assert baseline is not None

        after_edit = {**before_edit, "topk": 30, "train_start": "2023-01-01"}
        retained = snapshot_preset_for_review(
            after_edit,
            raw_preset,
            normalization_defaults={"commission_rate": 0.0005},
            snapshot=baseline,
        )

        differences = config_preset_differences(after_edit, retained)
        assert differences is not None
        self.assertEqual([difference.key for difference in differences], ["topk", "train_start"])
        self.assertEqual(differences[0].preset_value, 50)
        self.assertEqual(differences[1].preset_value, "2022-01-01")

    def test_unreadable_then_restored_preset_rebuilds_the_review_baseline(self) -> None:
        emitted = {"mode": "pipeline", "topk": 30}

        baseline = snapshot_preset_for_review(
            {"mode": "pipeline", "topk": 50},
            {"mode": "pipeline", "topk": 50},
            normalization_defaults={},
            snapshot=None,
        )

        self.assertIsNone(
            snapshot_preset_for_review(
                emitted,
                None,
                normalization_defaults={},
                snapshot=baseline,
            )
        )
        restored = snapshot_preset_for_review(
            emitted,
            {"mode": "pipeline", "topk": 40},
            normalization_defaults={},
            snapshot=None,
        )
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored["topk"], 40)

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
