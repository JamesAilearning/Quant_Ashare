"""Tests for the read-only configuration review model."""

from __future__ import annotations

import unittest

from web.operator_ui.pages._config_run_helpers import (
    DIVERGENCE_CHANGED,
    DIVERGENCE_MODE_INAPPLICABLE,
    DIVERGENCE_RUN_SCOPED,
    DIVERGENCE_SOURCE_MISSING,
    build_config_review_sections,
    config_preset_differences,
    divergences_of,
    effective_preset_for_review,
    explicitly_applied_preset_name,
    portable_config_for_preset_review,
    prefill_baseline_with_source_mode,
    prefill_divergences_from_source_run,
    snapshot_preset_for_review,
    unsupported_prefill_keys,
)


class ConfigRunReviewHelperTests(unittest.TestCase):
    def test_review_baseline_requires_an_explicitly_applied_preset(self) -> None:
        self.assertEqual(
            explicitly_applied_preset_name(None, custom_preset_name="Custom"),
            "Custom",
        )
        self.assertEqual(
            explicitly_applied_preset_name("Default", custom_preset_name="Custom"),
            "Default",
        )

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

    def test_portable_review_config_excludes_only_machine_local_fields(self) -> None:
        portable = portable_config_for_preset_review(
            {
                "mode": "pipeline",
                "provider_uri": "D:/machine-a/provider",
                "namechange_path": "D:/machine-a/namechange.csv",
                "topk": 50,
            }
        )

        self.assertEqual(portable, {"mode": "pipeline", "topk": 50})

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

    def test_run_scoped_output_dir_is_not_an_unsupported_field(self) -> None:
        # output_dir 由 JobManager 在每次启动时强制注入,所以它在**每份**
        # 归档配置里都有、在**每份**待提交配置里都没有。把它报成「本页
        # schema 不支持」等于给每次重跑挂一句常驻假警告——操作人正是这样
        # 学会忽略整块提示的。
        self.assertEqual(
            unsupported_prefill_keys(
                {"mode": "pipeline", "topk": 50, "output_dir": "runs/x"},
                {"mode": "pipeline", "topk": 50},
            ),
            (),
        )

    def test_values_changed_since_prefill_are_named(self) -> None:
        # 预填之后操作人还能改任何字段,提交出去的可以不是被重跑的那份配
        # 置。两侧都有、值不同 = 唯一需要操作人逐项确认的那类。
        divergences = prefill_divergences_from_source_run(
            {"mode": "pipeline", "topk": 50, "n_drop": 5,
             "model_type": "LGBModel"},
            {"mode": "pipeline", "topk": 30, "n_drop": 5,
             "model_type": "LGBModel"},
            known_keys=("mode", "topk", "n_drop", "model_type"),
        )

        self.assertEqual(len(divergences), 1)
        self.assertEqual(divergences[0].key, "topk")
        self.assertEqual(divergences[0].classification, DIVERGENCE_CHANGED)
        self.assertEqual(divergences[0].source_value, 50)
        self.assertEqual(divergences[0].emitted_value, 30)
        self.assertTrue(divergences[0].source_present)
        self.assertTrue(divergences[0].emitted_present)

    def test_identical_prefill_reports_no_divergence(self) -> None:
        self.assertEqual(
            prefill_divergences_from_source_run(
                {"mode": "pipeline", "topk": 50},
                {"mode": "pipeline", "topk": 50},
                known_keys=("mode", "topk"),
            ),
            (),
        )

    def test_numeric_equivalence_is_not_reported_as_a_change(self) -> None:
        # 预填走 yaml.safe_load,生效值走表单控件——同一个数可以是 50 与
        # 50.0。报成差异只会制造噪音、把真差异淹掉。
        self.assertEqual(
            prefill_divergences_from_source_run(
                {"topk": 50, "learning_rate": 0.05},
                {"topk": 50.0, "learning_rate": 0.05},
                known_keys=("topk", "learning_rate"),
            ),
            (),
        )

    def test_bool_is_not_compared_as_a_number(self) -> None:
        # isinstance(True, int) 为真——按数值比会把 True 与 1 判成相同,
        # 而它们在配置语义里不是一回事（risk_constraints_enabled 尤其）。
        divergences = prefill_divergences_from_source_run(
            {"risk_constraints_enabled": True},
            {"risk_constraints_enabled": 1},
            known_keys=("risk_constraints_enabled",),
        )

        self.assertEqual(len(divergences), 1)
        self.assertEqual(divergences[0].key, "risk_constraints_enabled")
        self.assertEqual(divergences[0].classification, DIVERGENCE_CHANGED)

    def test_machine_local_keys_never_count_as_divergence(self) -> None:
        # provider_uri / namechange_path 由本机强制覆写,差异无意义——与
        # 预设比较用的是同一套排除。
        self.assertEqual(
            prefill_divergences_from_source_run(
                {"provider_uri": "D:/old", "namechange_path": "old.csv"},
                {"provider_uri": "D:/new", "namechange_path": "new.csv"},
                known_keys=("provider_uri", "namechange_path"),
            ),
            (),
        )

    def test_key_missing_from_the_source_run_is_never_defaulted(self) -> None:
        # 老运行的 schema 更窄。「源运行没记这个键」**不等于**「它当时用的
        # 是本页默认值」——那是替一次没记录的运行编造基线。单列成自己一类,
        # 且 source_value 留空,不许拿 emitted 值反填。
        divergences = prefill_divergences_from_source_run(
            {"mode": "pipeline", "topk": 50},
            {"mode": "pipeline", "topk": 50, "risk_constraints_enabled": True},
            known_keys=("mode", "topk", "risk_constraints_enabled"),
        )

        self.assertEqual(len(divergences), 1)
        self.assertEqual(divergences[0].key, "risk_constraints_enabled")
        self.assertEqual(
            divergences[0].classification, DIVERGENCE_SOURCE_MISSING)
        self.assertFalse(divergences[0].source_present)
        self.assertIsNone(divergences[0].source_value)
        self.assertEqual(divergences[0].emitted_value, True)

    def test_other_mode_keys_are_separated_from_real_value_changes(self) -> None:
        # 源运行是 walk_forward、本次跑 pipeline:overall_start 属于另一个
        # 模式的 schema,本次压根不提交。和「topk 被改了」混在一起会把后者
        # 淹掉——操作人对两者的下一步完全不同。
        divergences = prefill_divergences_from_source_run(
            {"mode": "walk_forward", "topk": 50, "overall_start": "2020-01-01"},
            {"mode": "pipeline", "topk": 30},
            known_keys=("mode", "topk"),
            other_mode_keys=("overall_start", "overall_end"),
        )

        self.assertEqual(
            [d.key
             for d in divergences_of(
                 divergences, DIVERGENCE_MODE_INAPPLICABLE)],
            ["overall_start"],
        )
        # mode 与 topk 两侧都有且值不同 → 真正的值改动,不被那条噪音混入。
        self.assertEqual(
            {d.key for d in divergences_of(divergences, DIVERGENCE_CHANGED)},
            {"mode", "topk"},
        )

    def test_run_scoped_keys_are_their_own_class(self) -> None:
        # output_dir 随那一次运行而生,既不是「值被改了」也不是 schema 缺口。
        divergences = prefill_divergences_from_source_run(
            {"topk": 50, "output_dir": "runs/2026-01-01_abc"},
            {"topk": 50},
            known_keys=("topk",),
        )

        self.assertEqual(len(divergences), 1)
        self.assertEqual(divergences[0].key, "output_dir")
        self.assertEqual(
            divergences[0].classification, DIVERGENCE_RUN_SCOPED)

    def test_divergences_of_filters_by_class(self) -> None:
        divergences = prefill_divergences_from_source_run(
            {"mode": "pipeline", "topk": 50, "overall_start": "2020-01-01"},
            {"mode": "pipeline", "topk": 30, "n_drop": 5},
            known_keys=("mode", "topk", "n_drop"),
            other_mode_keys=("overall_start",),
        )

        self.assertEqual(
            [d.key for d in divergences_of(divergences, DIVERGENCE_CHANGED)],
            ["topk"],
        )
        self.assertEqual(
            [d.key
             for d in divergences_of(divergences, DIVERGENCE_SOURCE_MISSING)],
            ["n_drop"],
        )
        self.assertEqual(
            [d.key
             for d in divergences_of(
                 divergences, DIVERGENCE_MODE_INAPPLICABLE)],
            ["overall_start"],
        )

    def test_legacy_key_is_left_to_the_unsupported_reporter_alone(self) -> None:
        # 一个已删除的历史键既不在本模式 schema、也不在对面模式 schema。
        # 只看 known_keys 会把它标成「属于另一个模式」,而
        # `unsupported_prefill_keys` 同时说它「本页不支持」——操作人拿到
        # 两句自相矛盾的结论。本函数不认领它。
        divergences = prefill_divergences_from_source_run(
            {"mode": "pipeline", "topk": 50, "legacy_toggle": True},
            {"mode": "pipeline", "topk": 50},
            known_keys=("mode", "topk"),
            other_mode_keys=("overall_start", "overall_end"),
        )

        self.assertEqual(divergences, ())
        self.assertEqual(
            unsupported_prefill_keys(
                {"mode": "pipeline", "topk": 50, "legacy_toggle": True},
                {"mode": "pipeline", "topk": 50},
            ),
            ("legacy_toggle",),
        )

    def test_source_mode_joins_the_comparison_baseline(self) -> None:
        # UI 启动的运行把 mode 写进 job.json 而**不是**归档 config.yaml
        # （JobManager.start(config_dict, mode) 分开收）。不折进来的话,把
        # 一次 walk_forward 重跑改成 pipeline 会被说成「逐项一致」。
        baseline = prefill_baseline_with_source_mode(
            {"topk": 50}, "walk_forward")

        self.assertEqual(baseline, {"topk": 50, "mode": "walk_forward"})
        divergences = prefill_divergences_from_source_run(
            baseline, {"mode": "pipeline", "topk": 50},
            known_keys=("mode", "topk"),
        )
        self.assertEqual(
            [(d.key, d.source_value, d.emitted_value)
             for d in divergences_of(divergences, DIVERGENCE_CHANGED)],
            [("mode", "walk_forward", "pipeline")],
        )

    def test_source_yaml_mode_outranks_the_job_ledger(self) -> None:
        # 源 YAML 自带 mode 时以它为准:那是源运行自己记下的,比作业台账的
        # 转述更近一层。
        self.assertEqual(
            prefill_baseline_with_source_mode(
                {"mode": "pipeline"}, "walk_forward"),
            {"mode": "pipeline"},
        )

    def test_absent_source_mode_is_never_invented(self) -> None:
        # 凭空写一个模式 = 替一次没记录模式的运行编造基线。
        self.assertEqual(
            prefill_baseline_with_source_mode({"topk": 50}, ""),
            {"topk": 50},
        )


if __name__ == "__main__":
    unittest.main()
