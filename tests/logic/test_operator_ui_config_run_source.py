"""Source-level regression guards for Streamlit Config & Run wiring."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

# Some CI cells (ubuntu-3.10 / ubuntu-3.12) install streamlit via a step that
# has ``continue-on-error: true`` and may or may not succeed.  Source-level
# tests (read .py as text) don't need streamlit, but the LastNDaysSplitTests
# class imports a function from a page module that loads ``streamlit`` at
# import time.  Skip that class cleanly rather than fail the cell.
try:
    import streamlit as _streamlit  # noqa: F401

    _HAS_STREAMLIT = True
except ImportError:
    _HAS_STREAMLIT = False

# PIPELINE_KEYS / WALK_FORWARD_KEYS are derived from the config dataclasses,
# which transitively import qlib — unavailable in dep-light cells. Guard the
# schema-validity check the same way as the streamlit-backed tests.
try:
    from web.operator_ui.config_forms import (
        PIPELINE_KEYS as _PIPELINE_KEYS,
    )
    from web.operator_ui.config_forms import (
        WALK_FORWARD_KEYS as _WALK_FORWARD_KEYS,
    )

    _HAS_CONFIG_SCHEMAS = True
except Exception:  # noqa: BLE001 - dep-light cell (no qlib): introspection N/A
    _HAS_CONFIG_SCHEMAS = False
    _PIPELINE_KEYS = frozenset()
    _WALK_FORWARD_KEYS = frozenset()


class ConfigRunPageSourceTests(unittest.TestCase):
    def test_training_controls_are_not_inside_streamlit_form(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertNotIn(
            'with st.form("run_form")',
            source,
            "training controls must stay outside st.form so validation and Run disabled state rerender.",
        )

    def test_early_stopping_ui_rejects_zero(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn('"early_stopping_rounds"', source)
        self.assertIn('min_value=1', source)
        self.assertIn(
            'early_stopping_rounds',
            source,
        )

    def test_run_button_is_disabled_by_training_guard_errors(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("validate_pipeline_training_inputs(", source)
        self.assertIn("disabled=(not provider_uri_valid or bool(guard_errors))", source)

    def test_jobs_page_references_job_manager(self) -> None:
        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")

        self.assertIn("list_all_jobs", source)
        page_imports_jobs = "from web.operator_ui.job_io" in source or "JobManager" in source
        self.assertTrue(page_imports_jobs, "jobs.py should import from job_io or JobManager")

    def test_training_dates_use_provider_trading_day_selectors(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("metadata.calendar_dates", source)
        self.assertIn("st.selectbox(", source)
        self.assertIn('"仅可在所选数据源日历内的交易日中选择。"', source)
        self.assertIn("_pipeline_date_defaults(provider_metadata)", source)
        self.assertIn("_walk_forward_date_defaults(provider_metadata)", source)

    def test_config_validation_errors_are_displayed_not_raised_raw(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("except (ValueError, JobManagerError) as exc:", source)
        self.assertIn("st.error(str(exc))", source)

    def test_config_page_consumes_rerun_prefill_without_provider_value_binding(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("prefill_config_yaml", source)
        self.assertIn("yaml.safe_load", source)
        self.assertIn('cr_provider_uri', source)
        self.assertIn("prefill_config_applied_token", source)
        self.assertIn(
            "st.session_state.pop(_REVIEW_PRESET_NAME_STATE, None)", source
        )
        self.assertIn(
            "st.session_state.pop(_REVIEW_PRESET_SNAPSHOT_STATE, None)", source
        )
        self.assertIn("explicitly_applied_preset_name(", source)

    def test_config_page_has_preset_system(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("_preset_options", source)
        self.assertIn("list_preset_names", source)
        self.assertIn("_apply_preset", source)
        self.assertIn("_detect_preset", source)
        self.assertIn('"Custom"', source)

    def test_config_page_initializes_default_preset_fields(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn('"cr_preset_initialized"', source)
        self.assertIn('_apply_preset("Default")', source)
        self.assertIn('value=_cr("instruments", "csi300")', source)
        self.assertIn('value=_cr("feature_handler", "Alpha158")', source)

    def test_runtime_config_excludes_ui_mode_key(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("if submitted:", source)
        self.assertIn('preview_config = {"mode": mode, **config_dict}', source)
        self.assertIn("validate_config_keys(config_dict, known_keys)", source)
        self.assertIn("JobManager.start(config_dict, mode)", source)
        self.assertNotIn("validate_config_keys(preview_config", source)
        self.assertNotIn("JobManager.start(preview_config", source)

    def test_research_configuration_uses_progressive_review_without_a_second_builder(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        labels = (
            "① 研究目标与预设",
            "② 数据范围",
            "③ 策略约束",
            "④ 高级设置",
            "⑤ 提交前复核",
        )
        for label in labels:
            with self.subTest(label=label):
                self.assertIn(label, source)
        self.assertEqual(
            [source.index(label) for label in labels],
            sorted(source.index(label) for label in labels),
        )
        self.assertIn('"④ 高级设置 · 模型与训练", expanded=False', source)
        self.assertIn('"④ 高级设置 · 回测 / 成本模型", expanded=False', source)
        self.assertIn('"④ 高级设置 · 算力", expanded=False', source)
        self.assertIn("build_config_review_sections(preview_config)", source)
        self.assertIn("config_preset_differences(", source)
        self.assertIn("snapshot_preset_for_review(", source)
        self.assertIn("normalization_defaults=_RESET_FIELD_DEFAULTS", source)
        self.assertIn("_REVIEW_PRESET_NAME_STATE", source)
        self.assertIn("_REVIEW_PRESET_SNAPSHOT_STATE", source)
        self.assertIn("st.session_state[_REVIEW_PRESET_NAME_STATE] = preset_name", source)
        self.assertIn("snapshot=_review_snapshot", source)
        self.assertIn("if _review_preset is None:", source)
        self.assertIn(
            "st.session_state.pop(_REVIEW_PRESET_SNAPSHOT_STATE, None)", source
        )
        self.assertIn("复核基线", source)
        self.assertIn("unsupported_prefill_keys(", source)
        # 与被重跑运行的差异必须在提交前摊开,且**四类分开**——把 schema
        # 演进噪音和真实的值改动堆成一句,操作人只会学会忽略整块。钉:调用
        # 带 known_keys（少了它,另一模式的键会被误报成「值被改了」）、四个
        # 分桶取子集、以及「有改动 / 无改动」两条渲染分支都不沉默。
        self.assertIn(
            "prefill_divergences_from_source_run(\n"
            "        _prefill_baseline, preview_config,\n"
            "        known_keys=_review_known_keys,\n"
            "        other_mode_keys=_review_other_mode_keys,\n"
            "    )",
            source,
        )
        # 比较基线含源模式（UI 运行的 mode 只在 job.json 里）,且 `mode`
        # 参与比较,否则切模式后复核区说「逐项一致」。
        self.assertIn(
            "\n    _prefill_baseline = prefill_baseline_with_source_mode(\n",
            source,
        )
        self.assertIn(
            "\n    _review_known_keys = frozenset(known_keys) | {\"mode\"}\n",
            source,
        )
        # 「另一个模式的键」必须是**本页在那个模式下真的会发出**的键。用后端
        # schema 全集会把本页压根不发的字段也说成「切模式即生效」,而
        # unsupported 同时说「本页不支持」——两句自相矛盾。
        self.assertIn(
            "\n    _review_other_mode_keys = (\n"
            "        _WALK_FORWARD_ONLY_EMITTED if mode == \"pipeline\"\n"
            "        else _PIPELINE_ONLY_EMITTED\n"
            "    )\n",
            source,
        )
        # unsupported 也要看合成后的基线,否则 mode 会被它当成不支持的键;
        # 并且必须减掉 other_mode——否则同一个键会同时拿到「切模式即生效」
        # 与「本页不支持」两句互相打架的结论。
        self.assertIn(
            "\n    _unsupported_prefill = unsupported_prefill_keys(\n"
            "        _prefill_baseline, preview_config,\n"
            "        other_mode_keys=_review_other_mode_keys,\n"
            "    )\n",
            source,
        )
        # 一份合法但为空的归档配置必须说话:与「没点按钮」不可分辨是最坏的
        # 一种沉默——操作人有理由怀疑是不是按钮坏了。
        # 判据是**键在不在**，不是它的内容真不真:零字节的归档 config 会让
        # 内容判据把「点了重跑」与「没点」混成一格（codex P2）。这一行原本
        # 就有一段注释写着「只看那个 session 键在不在」，代码却在测真值。
        self.assertIn(
            '\n_HAS_PREFILL_PAYLOAD = "prefill_config_yaml" '
            'in st.session_state\n',
            source,
        )
        self.assertIn(
            "\nif _HAS_PREFILL_PAYLOAD and not PREFILL_CONFIG "
            "and not _PREFILL_ERROR:\n",
            source,
        )
        self.assertIn("是一份**空配置**", source)
        self.assertIn(
            "_changed = divergences_of(_prefill_divergences, "
            "DIVERGENCE_CHANGED)\n",
            source,
        )
        self.assertIn("DIVERGENCE_SOURCE_MISSING)\n", source)
        self.assertIn("DIVERGENCE_MODE_INAPPLICABLE)\n", source)
        self.assertIn("DIVERGENCE_RUN_SCOPED)\n", source)
        # 判据是「**有一份成功解析的载荷**」而不是「解析出几个字段」——
        # 合法空归档下 `_prefill_baseline` 里仍有台账带来的 `mode`，而横幅
        # 已经承诺复核区会逐项列出（codex P2 on #471）。
        self.assertIn("\n    if _HAS_PARSED_PREFILL:\n", source)
        self.assertIn("\n        if _changed:\n", source)
        self.assertIn("\n        else:\n            st.caption(\n", source)
        self.assertIn("逐项一致", source)
        self.assertIn("\n        if _source_missing:\n", source)
        self.assertIn("\n        if _mode_only:\n", source)
        self.assertIn("\n        if _run_scoped:\n", source)
        # 预填的写入**行为**（覆盖而非跳过、只写已知键、只把值不同的记成
        # 覆盖）由运行时测试保证——源码串看不见 session 状态,拿它去钉状态
        # 只会钉出一条随缩进漂移的假守卫。这里只钉接线:页面确实走那个被
        # 真跑过的函数,而且基线含源模式。
        self.assertIn(
            "\n        _prefill_overwritten = _apply_prefill_to_session(\n"
            "            prefill_baseline_with_source_mode(\n",
            source,
        )
        self.assertIn("\n            _PREFILL_APPLICABLE_KEYS,\n", source)
        # 覆盖列表要渲染出来:覆盖不许是静默的。
        self.assertIn("\n    if _prefill_overwritten:\n", source)
        # 预设选择器必须同步成 Custom,否则**预填会被下一帧撤销**:选择器
        # widget 粘着操作人上次选的预设,而预填把字段改成源运行的值让
        # `_detect_preset()` 记 Custom ⇒ 下一次控件触发的重跑里
        # `preset_choice != current_preset` ⇒ `_apply_preset()` 把源运行的
        # 值整片覆盖回去,而横幅照说「已按该次运行覆盖」。
        self.assertIn(
            '        st.session_state["cr_preset_selector"] = '
            "CUSTOM_PRESET_NAME\n"
            '        st.session_state["cr_preset"] = CUSTOM_PRESET_NAME\n',
            source,
        )
        # 摘要必须声明非安全用途:FIPS 受限的构建下不带这个参数会 raise,
        # 点「用此配置重跑」在预填生效之前就把整页打崩。
        self.assertIn(
            "hashlib.md5(str(st.session_state.get('prefill_config_yaml', ''))"
            ".encode('utf-8'), usedforsecurity=False)",
            source,
        )
        # 解析失败要响亮,不许静默返回空 dict 让横幅照说「已预填」。
        self.assertIn('st.session_state["prefill_config_error"] = (', source)
        self.assertIn("\n    except yaml.YAMLError as exc:\n", source)
        self.assertIn("\n    if not isinstance(loaded, dict):\n", source)
        self.assertIn("\nif _PREFILL_ERROR:\n", source)
        self.assertIn("启动研究运行", source)
        self.assertIn("不会发布模型、修改 production serving", source)
        # The review is read-only: it consumes preview_config, while the page
        # keeps one authoritative configuration builder and one start call.
        self.assertEqual(source.count("config_dict: dict[str, Any] = {"), 1)
        self.assertEqual(source.count("job_id = JobManager.start(config_dict, mode)"), 1)

    def test_config_dict_injects_namechange_path_for_both_modes(self) -> None:
        """PR-F (audit E1): the official single-fold AND walk-forward
        backtest paths now hard-require a non-empty ``namechange_path``
        (``require_st_mask=True``). The UI emits a STANDALONE job config
        (no ``extends`` / no loader env-expansion), so the page MUST
        inject the env-defaulted path into ``config_dict`` BEFORE the
        mode split's preview/validation — covering pipeline and
        walk_forward alike — or a UI-launched run RAISES after a full
        train."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("resolve_namechange_path", source)
        self.assertIn(
            'config_dict.setdefault("namechange_path", resolve_namechange_path())',
            source,
        )
        # The injection must sit AFTER both mode branches set known_keys
        # (so both modes are covered) and BEFORE the preview is built.
        inject_at = source.index('config_dict.setdefault("namechange_path"')
        wf_branch_at = source.index("known_keys = WALK_FORWARD_KEYS")
        preview_at = source.index('preview_config = {"mode": mode, **config_dict}')
        self.assertLess(wf_branch_at, inject_at, "inject must follow the mode split")
        self.assertLess(inject_at, preview_at, "inject must precede the preview")

    def test_preset_yaml_files_exist(self) -> None:
        presets_dir = Path("config/presets")
        for name in ("smoke", "default", "production"):
            self.assertTrue(
                (presets_dir / f"{name}.yaml").is_file(),
                f"Missing preset: {name}.yaml",
            )

    def test_yaml_preview_offers_copy_and_diff(self) -> None:
        """The YAML preview pane SHALL surface a Copy button and a
        ``Show diff vs preset`` toggle (TICKET-C polish)."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("📋 复制 YAML", source)
        self.assertIn("cr_copy_yaml_btn", source)
        self.assertIn("cr_show_diff_toggle", source)
        self.assertIn("与预设差异对比", source)
        # Diff is computed via stdlib difflib against the active preset.
        self.assertIn("difflib", source)
        self.assertIn("unified_diff", source)
        self.assertIn("portable_config_for_preset_review", source)
        self.assertIn("_review_preset_name", source)
        self.assertNotIn(
            '_load_preset(st.session_state.get("cr_preset", "Default"))', source,
        )
        # The toast confirms the copy action.
        self.assertIn('st.toast("已复制 YAML', source)

    def test_guard_errors_surface_auto_fix_buttons(self) -> None:
        """When a guard error has a known mechanical resolution, the
        status panel SHALL render a one-click fix alongside it."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("auto_fixes", source)
        # The GPU + non-LGBModel combo is the canonical example registered
        # in this PR; its fix label is documented here so the auto-fix
        # plumbing has at least one concrete attach point.
        self.assertIn("切换为 LGBModel", source)
        self.assertIn("_fix_gpu_model", source)

    def test_pipeline_dates_offer_quick_range_presets(self) -> None:
        """The pipeline date block SHALL surface quick range buttons
        (Full history / Last 5y / Last 3y / Reset to preset)."""

        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("日期范围快捷预设", source)
        self.assertIn("全部历史", source)
        self.assertIn("最近 5 年", source)
        self.assertIn("最近 3 年", source)
        self.assertIn("重置为预设值", source)
        self.assertIn("_last_n_days_split(", source)


@unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed in this CI cell")
class LastNDaysSplitTests(unittest.TestCase):
    def test_returns_none_for_empty_calendar(self) -> None:
        import types

        from web.operator_ui.pages.config_run import _last_n_days_split

        metadata = types.SimpleNamespace(calendar_dates=())
        self.assertIsNone(_last_n_days_split(metadata, 252 * 5))

    def test_returns_none_for_undersized_calendar(self) -> None:
        import types
        from datetime import date as _d

        from web.operator_ui.pages.config_run import _last_n_days_split

        # Below the 50-day floor: return None rather than guess.
        cal = tuple(_d(2026, 1, 1).fromordinal(_d(2026, 1, 1).toordinal() + i) for i in range(20))
        metadata = types.SimpleNamespace(calendar_dates=cal)
        self.assertIsNone(_last_n_days_split(metadata, 252 * 5))

    def test_split_produces_six_monotone_dates(self) -> None:
        import types
        from datetime import date as _d

        from web.operator_ui.pages.config_run import _last_n_days_split

        # Build a 1000-day synthetic calendar (real provider would be a
        # trading-day calendar; the helper doesn't care about gaps).
        base = _d(2020, 1, 1)
        cal = tuple(_d.fromordinal(base.toordinal() + i) for i in range(1000))
        metadata = types.SimpleNamespace(calendar_dates=cal)

        result = _last_n_days_split(metadata, 252 * 5)  # 1260 -> capped to 1000
        self.assertIsNotNone(result)
        assert result is not None  # for type-checker
        ordered = [
            result["train_start"], result["train_end"],
            result["valid_start"], result["valid_end"],
            result["test_start"], result["test_end"],
        ]
        # Monotone non-decreasing.
        # ``strict=False`` because zipping ``[1..6]`` with ``[2..6]`` deliberately
        # has unequal lengths — we want all consecutive pairs.
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            self.assertLessEqual(earlier, later)
        # Train/Valid/Test ranges are non-empty.
        self.assertLess(result["train_start"], result["train_end"])
        self.assertLess(result["valid_start"], result["valid_end"])
        self.assertLess(result["test_start"], result["test_end"])

    def test_split_leaves_embargo_between_segments(self) -> None:
        """Regression for Codex PR6.4 P1: the embargo guard in
        training_guards.py rejects splits with < LABEL_LOOKAHEAD_DAYS=2
        trading days between train_end → valid_start and valid_end →
        test_start. The quick presets must produce ranges that satisfy
        the guard so clicking ``最近 5 年`` doesn't immediately disable
        the Run button.
        """

        import types
        from datetime import date as _d

        from web.operator_ui.pages.config_run import _last_n_days_split
        from web.operator_ui.training_guards import LABEL_LOOKAHEAD_DAYS

        base = _d(2020, 1, 1)
        cal = tuple(_d.fromordinal(base.toordinal() + i) for i in range(1000))
        metadata = types.SimpleNamespace(calendar_dates=cal)
        result = _last_n_days_split(metadata, 252 * 5)
        assert result is not None

        # Count trading days strictly between each boundary pair.
        cal_set = set(cal)
        ts = {key: _d.fromisoformat(result[key]) for key in result}

        def _gap(earlier: _d, later: _d) -> int:
            return sum(1 for day in cal if earlier < day < later)

        # ``cal_set`` is only used to assert each preset date is itself in
        # the calendar — that the helper picked snapped trading days.
        for key, value in ts.items():
            self.assertIn(value, cal_set, f"preset {key} ({value}) is not in calendar")

        self.assertGreaterEqual(
            _gap(ts["train_end"], ts["valid_start"]),
            LABEL_LOOKAHEAD_DAYS,
            f"train_end→valid_start embargo too small: {result}",
        )
        self.assertGreaterEqual(
            _gap(ts["valid_end"], ts["test_start"]),
            LABEL_LOOKAHEAD_DAYS,
            f"valid_end→test_start embargo too small: {result}",
        )


@unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed in this CI cell")
class SixIncreasingIndicesTests(unittest.TestCase):
    """Regression for Codex PR6.4 P1: the long-default preset
    (`_pipeline_date_defaults` → `_six_increasing_indices`) must also
    leave enough room on each segment boundary so the embargo validator
    doesn't block ``全部历史`` clicks."""

    def test_indices_leave_embargo_at_segment_boundaries(self) -> None:
        from web.operator_ui.pages.config_run import _six_increasing_indices
        from web.operator_ui.training_guards import LABEL_LOOKAHEAD_DAYS

        indices = _six_increasing_indices(500)
        # Boundary 1: train_end → valid_start
        self.assertGreaterEqual(
            indices[2] - indices[1] - 1, LABEL_LOOKAHEAD_DAYS,
            f"train_end→valid_start gap too small in {indices}",
        )
        # Boundary 2: valid_end → test_start
        self.assertGreaterEqual(
            indices[4] - indices[3] - 1, LABEL_LOOKAHEAD_DAYS,
            f"valid_end→test_start gap too small in {indices}",
        )
        # Non-boundary pairs still strictly increasing.
        for i in range(5):
            self.assertLess(indices[i], indices[i + 1])
        # Indices fit within [0, last_index].
        self.assertEqual(indices[0], 0)
        self.assertLessEqual(indices[-1], 500)

    def test_returns_compact_layout_when_calendar_too_short(self) -> None:
        """Very short calendar can't satisfy embargos. Helper returns a
        best-effort layout rather than crashing; the embargo validator
        will then surface the real error to the operator."""

        from web.operator_ui.pages.config_run import _six_increasing_indices

        indices = _six_increasing_indices(3)
        self.assertEqual(len(indices), 6)
        # All indices are in [0, last_index] and the layout is monotone
        # non-decreasing (the helper clips rather than synthesising).
        self.assertTrue(all(0 <= i <= 3 for i in indices))


@unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed in this CI cell")
class SelectTradingDayFallbackTests(unittest.TestCase):
    """``_select_trading_day`` SHALL NOT silently snap a configured
    default to the calendar's first entry when the default falls outside
    the provider's calendar.

    The previous ``_option_index`` returned 0 on miss so the page just
    swapped, say, ``train_start=2022-01-01`` for ``calendar[0]=2023-06-12``
    with zero visual signal. Operators chased "why did my run skip 2022?"
    ghosts for days. UI review P1-9 made ``_option_index`` return -1 on
    miss; ``_select_trading_day`` then snaps to index 0 BUT surfaces
    ``st.warning`` so the operator sees the date change.
    """

    def test_option_index_returns_negative_one_on_miss(self) -> None:
        from web.operator_ui.pages.config_run import _option_index

        options = ["2023-01-03", "2023-01-04", "2023-01-05"]
        self.assertEqual(_option_index(options, "2022-12-01"), -1)
        # Hit still returns the real index.
        self.assertEqual(_option_index(options, "2023-01-04"), 1)
        # Empty options also returns -1 (defensive).
        self.assertEqual(_option_index([], "anything"), -1)

    def test_select_trading_day_warns_when_default_outside_calendar(self) -> None:
        """When the default falls outside the calendar, the page MUST
        emit a visible ``st.warning`` mentioning both the missing
        default and the replacement value."""

        import types
        from datetime import date as _d
        from unittest.mock import patch

        from web.operator_ui.pages import config_run

        cal = (
            _d(2023, 6, 12),
            _d(2023, 6, 13),
            _d(2023, 6, 14),
        )
        metadata = types.SimpleNamespace(calendar_dates=cal)

        captured_warnings: list[str] = []
        with patch(
            "streamlit.warning",
            side_effect=lambda msg, *_a, **_kw: captured_warnings.append(msg),
        ), patch(
            "streamlit.selectbox",
            side_effect=lambda label, options, **kw: options[kw.get("index", 0)],
        ):
            result = config_run._select_trading_day(
                "train_start",
                default="2022-01-01",
                metadata=metadata,
                state_key="cr_dt_train_start",
            )

        # Snapped to calendar[0].
        self.assertEqual(result, "2023-06-12")
        # Exactly one warning, mentioning both the bad default and
        # the replacement.
        self.assertEqual(len(captured_warnings), 1)
        warning = captured_warnings[0]
        self.assertIn("train_start", warning)
        self.assertIn("2022-01-01", warning)
        self.assertIn("2023-06-12", warning)

    def test_select_trading_day_does_not_warn_when_default_is_in_calendar(self) -> None:
        """Hit path stays silent — the warning is for the silent-snap
        case only, not a generic "you used a preset" reminder."""

        import types
        from datetime import date as _d
        from unittest.mock import patch

        from web.operator_ui.pages import config_run

        cal = (_d(2023, 6, 12), _d(2023, 6, 13), _d(2023, 6, 14))
        metadata = types.SimpleNamespace(calendar_dates=cal)

        captured_warnings: list[str] = []
        with patch(
            "streamlit.warning",
            side_effect=lambda msg, *_a, **_kw: captured_warnings.append(msg),
        ), patch(
            "streamlit.selectbox",
            side_effect=lambda label, options, **kw: options[kw.get("index", 0)],
        ):
            result = config_run._select_trading_day(
                "train_start",
                default="2023-06-13",
                metadata=metadata,
                state_key="cr_dt_train_start",
            )

        self.assertEqual(result, "2023-06-13")
        self.assertEqual(captured_warnings, [])

    def test_select_trading_day_no_calendar_falls_back_to_text_input(self) -> None:
        """When the provider exposes no calendar at all (no metadata or
        empty calendar), the helper degrades to ``st.text_input`` and
        does NOT warn — the snap-warning is specifically about
        out-of-calendar defaults, not unconfigured providers."""

        import types
        from unittest.mock import patch

        from web.operator_ui.pages import config_run

        metadata = types.SimpleNamespace(calendar_dates=())

        captured_warnings: list[str] = []
        with patch(
            "streamlit.warning",
            side_effect=lambda msg, *_a, **_kw: captured_warnings.append(msg),
        ), patch(
            "streamlit.text_input",
            side_effect=lambda label, value, **_kw: value,
        ):
            result = config_run._select_trading_day(
                "train_start",
                default="2022-01-01",
                metadata=metadata,
                state_key="cr_dt_train_start",
            )

        self.assertEqual(result, "2022-01-01")
        self.assertEqual(captured_warnings, [])


class AutoFixWidgetKeyStabilityTests(unittest.TestCase):
    """UI review P2-10: the auto-fix button's widget key used
    ``abs(hash(err)) % 10_000_000``. Python's ``hash()`` of a str
    varies per process (PYTHONHASHSEED), so a server restart re-keyed
    the button and orphaned any session_state tied to the old key. The
    key now derives from a stable ``hashlib.md5`` content digest."""

    def test_auto_fix_key_uses_stable_content_hash_not_builtin_hash(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")

        self.assertIn("import hashlib", source)
        self.assertIn("hashlib.md5(", source)
        # The process-varying builtin-hash key form must be gone.
        self.assertNotIn("abs(hash(err))", source)
        self.assertNotIn("hash(err) %", source)
        # md5 here is a non-security content digest — flagged so a
        # security linter / FIPS build doesn't choke.
        self.assertIn("usedforsecurity=False", source)


class RenderFieldFootgunDocTests(unittest.TestCase):
    """UI review P2-13: ``render_field`` emits ``control_html`` verbatim.
    Today all call sites pass static literals so there's no live XSS,
    but the docstring MUST warn so a future caller interpolating
    operator / artifact data escapes it first."""

    def test_render_field_docstring_warns_about_unescaped_control_html(self) -> None:
        source = Path("web/operator_ui/components.py").read_text(encoding="utf-8")

        func_start = source.index("def render_field(")
        # Scope to the function's docstring region.
        body = source[func_start:func_start + 1200]
        self.assertIn("verbatim", body)
        self.assertIn("XSS footgun", body)
        self.assertIn("P2-13", body)


@unittest.skipUnless(_HAS_STREAMLIT, "streamlit not installed in this CI cell")
class EstimateCalibrationTests(unittest.TestCase):
    """UI review P2-6: the duration estimate uses an empirical
    seconds-per-work-unit rate from recent jobs when available, falling
    back to the hardcoded throughput constants otherwise."""

    def test_work_units_scale_with_config_drivers(self) -> None:
        from web.operator_ui.pages.config_run import _pipeline_work_units

        small = {"instruments": "csi300", "num_boost_round": 1000,
                 "train_start": "2024-01-01", "train_end": "2024-12-31"}
        big = {"instruments": "all", "num_boost_round": 2000,
               "train_start": "2020-01-01", "train_end": "2024-12-31"}
        self.assertGreater(_pipeline_work_units(big), _pipeline_work_units(small))

    def test_calibration_returns_median_rate(self) -> None:
        from web.operator_ui.pages.config_run import (
            _calibration_seconds_per_unit,
            _pipeline_work_units,
        )

        cfg = {"instruments": "csi300", "num_boost_round": 1000,
               "train_start": "2024-01-01", "train_end": "2024-12-31"}
        units = _pipeline_work_units(cfg)
        # Three samples with rates 2, 4, 6 sec/unit → median 4.
        samples = [(cfg, units * 2), (cfg, units * 4), (cfg, units * 6)]
        rate = _calibration_seconds_per_unit(samples)
        self.assertIsNotNone(rate)
        assert rate is not None
        self.assertAlmostEqual(rate, 4.0, places=6)

    def test_calibration_none_for_empty_or_invalid_samples(self) -> None:
        from web.operator_ui.pages.config_run import _calibration_seconds_per_unit

        self.assertIsNone(_calibration_seconds_per_unit([]))
        # Non-positive durations are dropped.
        self.assertIsNone(_calibration_seconds_per_unit([({"x": 1}, 0.0)]))

    def test_estimate_uses_calibration_when_provided(self) -> None:
        from web.operator_ui.pages.config_run import (
            _estimate_duration,
            _pipeline_work_units,
        )

        cfg = {"instruments": "csi300", "num_boost_round": 1000,
               "train_start": "2024-01-01", "train_end": "2024-12-31"}
        units = _pipeline_work_units(cfg)
        # Calibrate to exactly 1 hour: rate so that units*rate = 3600s.
        rate = 3600.0 / units
        out = _estimate_duration(cfg, seconds_per_unit=rate)
        self.assertEqual(out, "约 1 小时 0 分")

    def test_estimate_falls_back_to_formula_without_calibration(self) -> None:
        from web.operator_ui.pages.config_run import _estimate_duration

        cfg = {"instruments": "csi300", "compute_device": "cpu",
               "num_boost_round": 1000,
               "train_start": "2024-01-01", "train_end": "2024-12-31"}
        out = _estimate_duration(cfg, seconds_per_unit=None)
        self.assertTrue(out.startswith("约 "))

    def test_config_page_wires_calibration_into_estimate(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(encoding="utf-8")
        self.assertIn("_gather_calibration_seconds_per_unit()", source)
        self.assertIn("seconds_per_unit=_calibration_rate", source)


class JobsCleanupUiSourceTests(unittest.TestCase):
    """UI review P2-11: the jobs page exposes a one-click bulk cleanup
    section gated behind an explicit confirmation."""

    def test_jobs_page_wires_cleanup_section(self) -> None:
        source = Path("web/operator_ui/pages/jobs.py").read_text(encoding="utf-8")
        self.assertIn("🧹 清理旧作业", source)
        self.assertIn("jobs_eligible_for_cleanup(", source)
        # Two-step: confirm checkbox gates the delete button.
        self.assertIn('key="jobs_cleanup_confirm"', source)
        self.assertIn("disabled=not confirm", source)
        self.assertIn("JobManager.delete(run_id)", source)


class WalkForwardLogFilterSourceTests(unittest.TestCase):
    """UI review P2-12: the walk-forward log tab gains the same search +
    severity filter the pipeline results log tab already had."""

    def test_walk_forward_log_tab_has_search_and_level_filter(self) -> None:
        source = Path("web/operator_ui/pages/walk_forward.py").read_text(encoding="utf-8")
        self.assertIn("from web.operator_ui.result_view_helpers import", source)
        self.assertIn("filter_log_text", source)
        self.assertIn('key="wf_log_search"', source)
        self.assertIn('key="wf_log_levels"', source)
        # The plain unfiltered ``st.code(text …）`` dump is gone.
        self.assertNotIn('st.code(text or "（空）"', source)


class ProviderUriPrefillTests(unittest.TestCase):
    """provider_uri must prefill the canonical default (config.yaml
    ${QUANT_PROVIDER_URI:-…}) like the 数据检视 page, and stop nudging operators
    at the legacy NON-PIT bundle via the placeholder."""

    def setUp(self) -> None:
        self.source = Path(
            "web/operator_ui/pages/config_run.py"
        ).read_text(encoding="utf-8")

    def test_imports_default_provider_resolver(self) -> None:
        self.assertIn(
            "from web.operator_ui.bundle_health import "
            "resolve_default_provider_uri",
            self.source,
        )

    def test_provider_uri_prefilled_from_resolved_default(self) -> None:
        self.assertIn(
            '_cr("provider_uri", resolve_default_provider_uri()', self.source
        )

    def test_legacy_non_pit_placeholder_gone(self) -> None:
        # Exact old placeholder (the legacy non-PIT bundle) must be removed;
        # the new one references the PIT bundle / QUANT_PROVIDER_URI.
        self.assertNotIn('placeholder="D:/qlib_data/my_cn_data"', self.source)
        self.assertIn("QUANT_PROVIDER_URI", self.source)


class PresetSaveStripsMachineLocalPathsTests(unittest.TestCase):
    """Saving a preset must NOT bake machine-local paths (provider_uri,
    namechange_path) into the YAML — the tracked built-ins omit them, and a
    saved inspection-bundle provider_uri gets the preset rejected at launch."""

    def setUp(self) -> None:
        self.source = Path(
            "web/operator_ui/pages/config_run.py"
        ).read_text(encoding="utf-8")

    def test_save_excludes_machine_local_paths(self) -> None:
        self.assertIn(
            'if k not in ("provider_uri", "namechange_path")', self.source
        )

    def test_save_does_not_dump_raw_preview_config(self) -> None:
        # The verbatim dump (which baked provider_uri) must be gone from save.
        self.assertNotIn("yaml.dump(preview_config,", self.source)


class WalkForwardLaunchParityTests(unittest.TestCase):
    """The walk-forward guard branch must run the universe/benchmark mismatch
    warning the pipeline path runs (instruments=all vs a major index inflates
    "excess vs benchmark"). UI-audit follow-up.

    (WF-date prefill history: routing the dates through ``_cr`` regressed
    provider-calendar tracking — codex P2 on #300 — because ``_cr`` SEEDS the
    provider-derived default into session and then sticks to it, freezing a
    first-render no-calendar fallback. #471 restores prefill through
    ``_prefilled_trading_day``, which only READS: with no prefill present it
    writes nothing, so the live default keeps recomputing every rerun. Runtime
    coverage for both directions lives in
    ``tests/logic/test_config_run_prefill_runtime.py``.)
    """

    def setUp(self) -> None:
        self.source = Path(
            "web/operator_ui/pages/config_run.py"
        ).read_text(encoding="utf-8")

    def test_shared_emitted_matches_the_shared_config_literal(self) -> None:
        """``_SHARED_EMITTED`` == 页面 ``config_dict = {...}`` 字面量的键。

        这份分叉的后果与两个 ``*_ONLY_EMITTED`` 一样是**说错话而不报错**:
        漏一个键,那个字段的预填就再也进不来(而横幅照说「已预填」);多一个
        键,一个本页从不提交的字段会被写进 session,下次重跑再被报成「被覆
        盖」——而复核区同时说「本次不会携带它」。
        """
        from web.operator_ui.pages.config_run import _SHARED_EMITTED

        tree = ast.parse(self.source)
        literal = next(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "config_dict"
            and isinstance(node.value, ast.Dict)
        )
        declared = {
            key.value
            for key in literal.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }

        self.assertEqual(declared, set(_SHARED_EMITTED))

    def test_page_emitted_keys_cover_the_setdefault_fields(self) -> None:
        # `namechange_path` 没有控件,靠 setdefault 补上——但它**确实**随配置
        # 发出。漏掉它,重跑一次归档配置时它的值就进不了本页状态。
        from web.operator_ui.pages.config_run import _PAGE_EMITTED_KEYS

        self.assertIn("namechange_path", _PAGE_EMITTED_KEYS)
        self.assertIn("mode", _PAGE_EMITTED_KEYS)
        for node in ast.walk(ast.parse(self.source)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "config_dict"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                self.assertIn(node.args[0].value, _PAGE_EMITTED_KEYS)

    def test_prefill_applies_only_to_fields_the_page_emits(self) -> None:
        """预填写入的键集合 ⊆ 本页真会提交的字段。

        用后端 schema 全集的话,``cr_run_factor_analysis`` 这种本页没有控件、
        永不提交的字段也会被写进 session,下次重跑另一个值时被报成「被覆盖」
        ——而复核区同时说「本次不会携带它」(codex P2 on #471 r5,同一个根因
        的第三种形态)。
        """
        from web.operator_ui.config_forms import PIPELINE_KEYS, WALK_FORWARD_KEYS
        from web.operator_ui.pages.config_run import (
            _PAGE_EMITTED_KEYS,
            _PREFILL_APPLICABLE_KEYS,
        )

        self.assertTrue(_PREFILL_APPLICABLE_KEYS <= _PAGE_EMITTED_KEYS)
        # 后端 schema 里本页不发的字段一个也不许进来。
        backend_only = (
            (set(PIPELINE_KEYS) | set(WALK_FORWARD_KEYS)) - _PAGE_EMITTED_KEYS
        )
        self.assertTrue(
            backend_only, "后端 schema 应当含本页不发的字段，否则这条钉是空的")
        self.assertEqual(_PREFILL_APPLICABLE_KEYS & backend_only, set())

    def test_prefill_only_writes_fields_the_page_reads_back(self) -> None:
        """预填写进去的每个字段，都必须被提交它的控件读回。

        这是本 change 的 spec 自己写下的要求，也是同一个根因的**第四种**
        形态：写一个本页从不读的键，值进得了 session 却到不了发出的配置，
        而下一次重跑另一份归档配置时它会被如实报成「被覆盖」——一条关于
        「哪个值会生效」的假消息。

        判据是**构造性**的：从源码算出「本页读回的键」，再与
        ``_PAGE_EMITTED_KEYS`` 求差；差集必须正好等于
        ``_EMITTED_WITHOUT_READBACK``。手写第四份名单只会漂——前三次都是
        这样漂的。
        """
        from web.operator_ui.pages.config_run import (
            _EMITTED_WITHOUT_READBACK,
            _PAGE_EMITTED_KEYS,
            _PREFILL_APPLICABLE_KEYS,
        )

        tree = ast.parse(self.source)
        readback: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # `_cr("<key>", ...)` 与 `_prefilled_trading_day("<key>", ...)`
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"_cr", "_prefilled_trading_day"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                readback.add(node.args[0].value)
            # 直接绑 session 的控件:`key="cr_<key>"`
            for keyword in node.keywords:
                if (
                    keyword.arg == "key"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                    and keyword.value.value.startswith("cr_")
                ):
                    readback.add(keyword.value.value[len("cr_"):])

        self.assertTrue(readback, "从源码里一个读回键也没解析出来——守卫是空的")
        self.assertEqual(
            _PAGE_EMITTED_KEYS - readback, set(_EMITTED_WITHOUT_READBACK),
            "本页发出却不读回的字段变了：要么给它接上控件，要么写进"
            " _EMITTED_WITHOUT_READBACK 并说清为什么",
        )
        # 预填只写读得回来的那些。
        self.assertTrue(_PREFILL_APPLICABLE_KEYS <= readback)
        self.assertNotIn("namechange_path", _PREFILL_APPLICABLE_KEYS)

    def test_run_scoped_keys_are_absent_from_what_the_page_emits(self) -> None:
        """run-scoped 键**不该出现**在本页发出的字段里。

        此前这里靠 ``_PAGE_EMITTED_KEYS - _RUN_SCOPED_PREFILL_KEYS`` 兜底。
        重构之后那道减法成了 no-op（三份 ``*_EMITTED`` 常量本就不含
        ``output_dir``），而变异实测能原样逃逸——no-op 的兜底恰恰会掩盖「有人
        把 ``output_dir`` 写进 ``_SHARED_EMITTED``」这种错误，让它在别处以
        「第二次重跑报一条假的被覆盖」的形式冒出来。守卫响亮地钉在这里。
        """
        from web.operator_ui.pages._config_run_helpers import (
            _RUN_SCOPED_PREFILL_KEYS,
        )
        from web.operator_ui.pages.config_run import (
            _PAGE_EMITTED_KEYS,
            _PREFILL_APPLICABLE_KEYS,
        )

        self.assertEqual(_PAGE_EMITTED_KEYS & _RUN_SCOPED_PREFILL_KEYS, set())
        self.assertEqual(
            _PREFILL_APPLICABLE_KEYS & _RUN_SCOPED_PREFILL_KEYS, set())

    def test_mode_only_emitted_key_sets_match_what_the_page_emits(
        self,
    ) -> None:
        """两个 ``*_ONLY_EMITTED`` 常量 == 页面两个模式分支真正 update 的键。

        这两份分叉时的后果是**说错话而不报错**:复核区会宣称某个字段「属于
        另一个模式、切过去就生效」,而本页在那个模式下压根不发它;或者反过
        来漏掉一个真该单列的字段,把它混进「值被改了」淹掉真差异。两边都
        照常提交,没有任何东西会红。

        所以取页面里那两个 ``config_dict.update({...})`` 字面量的键**解析**着
        比,不在测试里抄一份名单——抄的那份跟着谁漂都不会被发现。
        """
        from web.operator_ui.pages.config_run import (
            _PIPELINE_ONLY_EMITTED,
            _WALK_FORWARD_ONLY_EMITTED,
        )

        tree = ast.parse(self.source)
        branch = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "mode"
            and any(
                isinstance(comparator, ast.Constant)
                and comparator.value == "pipeline"
                for comparator in node.test.comparators
            )
            and any(
                isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "known_keys"
                    for target in statement.targets
                )
                for statement in node.body
            )
        )

        def _updated_keys(body: list[ast.stmt]) -> set[str]:
            call = next(
                node
                for statement in body
                for node in ast.walk(statement)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "config_dict"
            )
            literal = call.args[0]
            assert isinstance(literal, ast.Dict)
            return {
                key.value
                for key in literal.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }

        self.assertEqual(_updated_keys(branch.body), set(_PIPELINE_ONLY_EMITTED))
        self.assertEqual(
            _updated_keys(branch.orelse), set(_WALK_FORWARD_ONLY_EMITTED))

    def test_mode_only_sets_hold_no_key_the_page_never_emits(self) -> None:
        # 反向:两个常量里的每个键都必须真的在后端 schema 里(不然本页发了
        # 一个后端不收的字段),且**不**在共享段里(共享段两模式都发,不该被
        # 说成「属于另一个模式」)。
        from web.operator_ui.config_forms import PIPELINE_KEYS, WALK_FORWARD_KEYS
        from web.operator_ui.pages.config_run import (
            _PIPELINE_ONLY_EMITTED,
            _WALK_FORWARD_ONLY_EMITTED,
        )

        self.assertTrue(set(_PIPELINE_ONLY_EMITTED) <= set(PIPELINE_KEYS))
        self.assertTrue(
            set(_WALK_FORWARD_ONLY_EMITTED) <= set(WALK_FORWARD_KEYS))
        # 模式专属 ⇒ 不在对面模式的 schema 里。
        self.assertEqual(
            set(_PIPELINE_ONLY_EMITTED) & set(WALK_FORWARD_KEYS), set())
        self.assertEqual(
            set(_WALK_FORWARD_ONLY_EMITTED) & set(PIPELINE_KEYS), set())

    def test_wf_dates_honour_prefill_over_the_live_default(self) -> None:
        # overall_start/overall_end 是滚动验证窗口的两个**定义性**字段。
        # 预填把它们写进 session,控件不读的话,重跑跑的区间与源运行不同,
        # 而复核区看不出来(两侧都是控件产出的 live default)——codex P1
        # on #471。钉调用形态整行:只钉函数名的话,把 default= 换回裸的
        # live default 能原样逃逸。
        self.assertIn(
            '                default=_prefilled_trading_day(\n'
            '                    "overall_start",\n'
            '                    walk_forward_date_defaults["overall_start"]),'
            '\n',
            self.source,
        )
        self.assertIn(
            '                default=_prefilled_trading_day(\n'
            '                    "overall_end",\n'
            '                    walk_forward_date_defaults["overall_end"]),\n',
            self.source,
        )

    def test_wf_dates_still_do_not_seed_the_live_default(self) -> None:
        # #300 的病根是 `_cr` **写**:它把 provider 相关的 default 种进
        # session 并从此粘住。`_prefilled_trading_day` 只读。
        self.assertNotIn('_cr("overall_start"', self.source)
        self.assertNotIn('_cr("overall_end"', self.source)
        # 「函数体里没有写 session」要**解析**着问,不是按文本切:按
        # `\ndef ` 切会一路切到文件末尾的模块级代码(那里当然有赋值),
        # 守卫于是恒红或恒绿地失去意义。
        function = next(
            node
            for node in ast.parse(self.source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_prefilled_trading_day"
        )
        def _is_session_state(node: ast.expr) -> bool:
            return (
                isinstance(node, ast.Attribute)
                and node.attr == "session_state"
                and isinstance(node.value, ast.Name)
                and node.value.id == "st"
            )

        writes: list[str] = []
        for node in ast.walk(function):
            # `st.session_state[...] = ...`（含增量与带注解赋值）
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and _is_session_state(
                        target.value):
                    writes.append(f"assign@{node.lineno}")
            # `st.session_state.pop(...)` 之类的原地修改
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"pop", "update", "setdefault", "clear"}
                and _is_session_state(node.func.value)
            ):
                writes.append(f"{node.func.attr}@{node.lineno}")
        self.assertEqual(
            writes, [],
            "_prefilled_trading_day 必须只读 session:任何写入都会把 provider"
            " 相关的 live default 种住,复现 #300 的回滚原因",
        )

    def test_wf_branch_runs_universe_benchmark_alignment(self) -> None:
        self.assertIn("_validate_universe_benchmark_alignment(", self.source)
        self.assertIn(
            "instruments, benchmark_code, guard_warnings", self.source
        )


class FeatureHandlerGuardTests(unittest.TestCase):
    """feature_handler is a free-text input; an unregistered handler — notably
    MinedFactor, which the UI never binds — would fail deep in
    FeatureDatasetBuilder after a full handler init. A pre-submit guard must
    block it up front, using the live registry as the source of truth."""

    def setUp(self) -> None:
        self.source = Path(
            "web/operator_ui/pages/config_run.py"
        ).read_text(encoding="utf-8")

    def test_imports_supported_handlers_registry(self) -> None:
        self.assertIn(
            "from src.data.feature_dataset_builder import "
            "list_supported_feature_handlers",
            self.source,
        )

    def test_guard_blocks_unregistered_feature_handler(self) -> None:
        self.assertIn("list_supported_feature_handlers()", self.source)
        self.assertIn(
            "feature_handler and feature_handler not in _supported_handlers",
            self.source,
        )

    def test_feature_handler_rechecked_on_submit(self) -> None:
        # The submit path must re-check too: Streamlit can submit from a stale
        # enabled-button frame before the render-time guard disables Run
        # (codex P2 on #303).
        self.assertIn(
            "feature_handler and feature_handler not in _final_handlers",
            self.source,
        )

    def test_minedfactor_not_in_default_registry(self) -> None:
        # Premise the guard relies on: MinedFactor is NOT registered in a fresh
        # process (only run_walk_forward.py binds it), so the guard blocks it;
        # Alpha158 is, so it stays launchable.
        from src.data.feature_dataset_builder import (
            list_supported_feature_handlers,
        )
        handlers = list_supported_feature_handlers()
        self.assertNotIn("MinedFactor", handlers)
        self.assertIn("Alpha158", handlers)


class CostModelFieldsTests(unittest.TestCase):
    """Backtest / cost-model knobs (adjust_mode, limit_threshold, commission,
    slippage, init_cash, min_cost, seed) are surfaced in an advanced expander
    and threaded into the job config. Each must be a valid key in BOTH config
    schemas, or the shared config_dict is rejected by validate_config_keys."""

    _COST_KEYS = (
        "adjust_mode", "limit_threshold", "commission_rate",
        "slippage_bps", "min_cost", "init_cash", "seed",
    )

    def setUp(self) -> None:
        self.source = Path(
            "web/operator_ui/pages/config_run.py"
        ).read_text(encoding="utf-8")

    def test_cost_model_expander_present(self) -> None:
        self.assertIn("回测 / 成本模型", self.source)

    def test_adjust_mode_uses_supported_modes(self) -> None:
        self.assertIn("SUPPORTED_ADJUST_MODES", self.source)
        self.assertIn('key="cr_adjust_mode"', self.source)

    def test_config_dict_threads_cost_fields(self) -> None:
        for key in self._COST_KEYS:
            self.assertIn(f'"{key}":', self.source)

    @unittest.skipUnless(
        _HAS_CONFIG_SCHEMAS, "config schemas unavailable (no qlib)"
    )
    def test_cost_keys_valid_in_both_config_schemas(self) -> None:
        for key in self._COST_KEYS:
            self.assertIn(key, _PIPELINE_KEYS, f"{key} not in PIPELINE_KEYS")
            self.assertIn(
                key, _WALK_FORWARD_KEYS, f"{key} not in WALK_FORWARD_KEYS"
            )

    def test_presets_normalize_missing_cost_keys(self) -> None:
        # A preset that omits the cost keys (older / custom, saved before these
        # fields existed) must still reset them: _apply_preset fills from
        # _COST_FIELD_DEFAULTS and _detect_preset treats the omitted keys as
        # those defaults, so switching never leaves stale advanced values under
        # a clean-looking preset (codex P2 round 2 on #308).
        #
        # The reset map GREW (csi800 guard triple) — the pin follows the
        # property, not the old spelling: both families must flow through
        # ONE map that apply and detect share, so they can never diverge.
        self.assertIn("_COST_FIELD_DEFAULTS", self.source)
        self.assertIn("_GUARD_FIELD_DEFAULTS", self.source)
        self.assertIn("if key not in preset:", self.source)
        self.assertIn(
            "_RESET_FIELD_DEFAULTS: dict[str, Any] = {\n"
            "    **_COST_FIELD_DEFAULTS,\n"
            "    **_GUARD_FIELD_DEFAULTS,\n"
            "}",
            self.source,
            "the reset map must be the union of both families",
        )
        self.assertIn("for key, default in _RESET_FIELD_DEFAULTS.items():", self.source)
        self.assertIn("{**_RESET_FIELD_DEFAULTS, **preset}", self.source)
        # Neither family may keep a private reset path: a second loop over
        # one family alone is exactly how apply and detect drift apart.
        self.assertNotIn("for key, default in _COST_FIELD_DEFAULTS.items():", self.source)
        self.assertNotIn("{**_COST_FIELD_DEFAULTS, **preset}", self.source)

    def test_csi800_guard_triple_is_emitted_and_refused_loudly(self) -> None:
        # The page used to emit instruments=csi800 WITHOUT the guard triple,
        # so the Default preset (csi800) produced a config the backend
        # refuses to construct — while the page said "✓ 配置有效 / 作业已启动".
        for key in (
            "attribution_sleeve_grouping",
            "risk_constraints_enabled",
            "risk_constraints_calibration",
        ):
            with self.subTest(key=key):
                self.assertIn(f'"{key}": {key},', self.source)
                self.assertIn(f'key="cr_{key}"', self.source)
        # The verdict is delegated to the canonical validator (a UI copy of
        # the rule is the drift that produced the bug) and runs on BOTH the
        # render guard and the submit recheck (stale-frame defense).
        self.assertIn("validate_csi800_guard_triple", self.source)
        self.assertGreaterEqual(
            self.source.count("validate_csi800_guard_triple("), 2,
            "guard must run on the render path AND the submit recheck",
        )

    @unittest.skipUnless(
        _HAS_CONFIG_SCHEMAS, "config schemas unavailable (no qlib)"
    )
    def test_guard_keys_valid_in_both_config_schemas(self) -> None:
        # They ride in the SHARED config_dict (before the mode split), so
        # both schemas must accept them or validate_config_keys rejects.
        for key in (
            "attribution_sleeve_grouping",
            "risk_constraints_enabled",
            "risk_constraints_calibration",
        ):
            self.assertIn(key, _PIPELINE_KEYS, f"{key} not in PIPELINE_KEYS")
            self.assertIn(
                key, _WALK_FORWARD_KEYS, f"{key} not in WALK_FORWARD_KEYS"
            )

    def test_cost_widgets_use_single_source_defaults(self) -> None:
        # Widget defaults reference _COST_FIELD_DEFAULTS (no literal drift vs the
        # preset-reset defaults).
        for key in self._COST_KEYS:
            self.assertIn(f'_COST_FIELD_DEFAULTS["{key}"]', self.source)

    def test_form_guards_cost_field_ranges(self) -> None:
        # Out-of-range values are blocked in the form, not deferred to backend
        # config construction (codex P2 on #308). Enforced on BOTH the render
        # guard and the submit-path recheck.
        self.assertIn("0.0 < float(limit_threshold) <= 0.25", self.source)
        self.assertIn("float(init_cash) <= 0", self.source)
        self.assertGreaterEqual(
            self.source.count("0.0 < float(limit_threshold) <= 0.25"), 2
        )
        # commission_rate / slippage_bps upper bounds mirror
        # CanonicalExchangeCostModel, on both render + submit (codex P2 rd 2).
        self.assertIn("float(commission_rate) > COMMISSION_RATE_MAX", self.source)
        self.assertIn("float(slippage_bps) > SLIPPAGE_BPS_MAX", self.source)
        self.assertGreaterEqual(
            self.source.count("float(commission_rate) > COMMISSION_RATE_MAX"), 2
        )

    def test_invalid_adjust_mode_not_silently_coerced(self) -> None:
        # An unsupported adjust_mode (hand-edited preset / prefill) is kept
        # visible and selected (not coerced to index 0) and blocked by a guard on
        # both the render and submit paths (codex P2 round 3 on #308).
        self.assertNotIn(
            "if adjust_default in SUPPORTED_ADJUST_MODES else 0", self.source
        )
        self.assertIn("adjust_mode not in SUPPORTED_ADJUST_MODES", self.source)
        self.assertGreaterEqual(
            self.source.count("adjust_mode not in SUPPORTED_ADJUST_MODES"), 2
        )


class RepeatedRerunActionRearmsPrefillTests(unittest.TestCase):
    """对**同一个运行**再点一次「用此配置重跑」，预填必须重新生效。

    令牌原来只由「源运行 + 配置内容」构成。操作人预填之后改了几个字段、
    回结果页对同一个运行再点一次，令牌不变 ⇒ 应用分支被跳过 ⇒ 他的改动
    原样留着，而横幅照说「已按该次运行覆盖」——启动的实验与他明确重选的
    那次运行不一致（codex P1 on #471）。

    这里**真跑**令牌表达式（从页面 AST 抽出来求值），不查源码串：要证明的
    是「令牌随动作变、不随重绘变」，源码串证明不了——把 nonce 拼进一个从
    没被求值的分支，串守卫照样命中。
    """

    def setUp(self) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        assigns = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "prefill_token"
                    for t in node.targets)
        ]
        self.assertEqual(len(assigns), 1, "令牌应当只在一处构造")
        self.expr = assigns[0].value

    def _token(self, *, job: str, yaml_text: str, action: str) -> str:
        import hashlib as _hashlib
        from types import SimpleNamespace

        state = {
            "prefill_config_yaml": yaml_text,
            "prefill_config_action": action,
        }
        namespace = {
            "source_job": job,
            "hashlib": _hashlib,
            "st": SimpleNamespace(session_state=state),
        }
        code = compile(ast.Expression(self.expr), "<token>", "eval")
        return str(eval(code, namespace))  # noqa: S307 - 求值的是本仓页面自己的表达式

    def test_a_second_press_on_the_same_run_produces_a_new_token(self) -> None:
        first = self._token(job="job-1", yaml_text="topk: 50", action="aaa")
        second = self._token(job="job-1", yaml_text="topk: 50", action="bbb")

        self.assertNotEqual(
            first, second,
            "同一个运行、同一份配置，再按一次必须换令牌——否则预填不会重新生效",
        )

    def test_an_ordinary_rerender_keeps_the_token_stable(self) -> None:
        # 幂等性：普通重绘不经过按钮回调，nonce 不变 ⇒ 同一次预填只应用一次。
        first = self._token(job="job-1", yaml_text="topk: 50", action="aaa")
        again = self._token(job="job-1", yaml_text="topk: 50", action="aaa")

        self.assertEqual(first, again)

    def test_the_payload_still_participates(self) -> None:
        # nonce 之外仍然带上配置内容:万一将来有第二个写入方忘了铸 nonce,
        # 内容变了照样能重新武装。
        first = self._token(job="job-1", yaml_text="topk: 50", action="aaa")
        other = self._token(job="job-1", yaml_text="topk: 20", action="aaa")

        self.assertNotEqual(first, other)

    def test_the_button_branch_mints_a_fresh_action_nonce(self) -> None:
        # 另一端:nonce 必须在**按钮分支内**铸，不是每帧铸（每帧铸会让每一次
        # 重绘都重新覆盖操作人的编辑）。
        source = Path("web/operator_ui/pages/_results_render.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)

        branches = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and any(isinstance(c, ast.Constant) and c.value == "用此配置重跑"
                    for c in ast.walk(node.test))
        ]
        self.assertEqual(len(branches), 1, "「用此配置重跑」应当只有一处")

        assigned = {
            ast.unparse(target)
            for node in ast.walk(branches[0])
            if isinstance(node, ast.Assign)
            for target in node.targets
        }
        self.assertIn("st.session_state['prefill_config_action']", assigned)

        minted = {
            node.func.id for node in ast.walk(branches[0])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("uuid4", minted, "动作身份必须是新铸的,不是复用的值")

        # 分支**之外**不许再有第二处写它——每帧写就毁掉幂等性。
        outside = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(ast.unparse(t) == "st.session_state['prefill_config_action']"
                    for t in node.targets)
        ]
        self.assertEqual(len(outside), 1, "动作身份只该在按钮分支里写一次")



class RerunActionReachableFromBothEnginesTests(unittest.TestCase):
    """预填状态得先**产得出来**，规格里的场景才谈得上可达。

    「用此配置重跑」原来只长在 `_render_header_actions` 里，而那个函数只被
    `_render_pipeline_dashboard` 调用。一份正常的滚动验证结果（有
    `walk_forward_report.json`、没有根级 `pipeline_report.json`）走的是
    `_render_walk_forward_summary` 那一支——于是本 change 为「源运行是
    walk_forward」写下的窗口恢复与跨模式重跑场景，在那一侧**全都不可达**
    （codex P1 on #471）。

    这里钉的是**路由**：按钮的实现只有一份，两条分派路径都调它。
    """

    def setUp(self) -> None:
        self.render = Path(
            "web/operator_ui/pages/_results_render.py").read_text(
                encoding="utf-8")
        self.page = Path("web/operator_ui/pages/results.py").read_text(
            encoding="utf-8")

    def test_the_rerun_button_has_exactly_one_implementation(self) -> None:
        # 两份实现里只要有一份忘了铸动作 nonce、或忘了写
        # `prefill_config_source_mode`，症状都是「预填看起来没生效」。
        self.assertEqual(
            self.render.count('st.button("用此配置重跑"'), 1,
            "按钮只该有一处实现",
        )
        for other in ("web/operator_ui/pages/results.py",):
            self.assertNotIn(
                '用此配置重跑', Path(other).read_text(encoding="utf-8"),
                f"{other} 不该自己再画一个按钮",
            )

    def test_both_dispatch_branches_render_the_rerun_action(self) -> None:
        # 用 AST 找模块级分派的那条 if/elif 链，逐支确认调用。
        tree = ast.parse(self.page)
        chains = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            # ast.unparse 会把字符串统一成单引号——按引号写死的匹配会静默
            # 找不到，测试于是变成「零覆盖但绿」。这里去掉引号再比。
            and "mode == 'pipeline'" in ast.unparse(node.test)
        ]
        self.assertEqual(len(chains), 1, "结果页的引擎分派应当只有一处")
        chain = chains[0]

        def calls(nodes: list[ast.stmt]) -> set[str]:
            found: set[str] = set()
            for stmt in nodes:
                for node in ast.walk(stmt):
                    if isinstance(node, ast.Call) and isinstance(
                            node.func, ast.Name):
                        found.add(node.func.id)
            return found

        pipeline_calls = calls(chain.body)
        self.assertIn("_render_pipeline_dashboard", pipeline_calls)

        wf_branch = chain.orelse
        self.assertTrue(wf_branch, "应当有 walk_forward 分支")
        wf_calls = calls(wf_branch)
        self.assertIn(
            "_render_rerun_action", wf_calls,
            "滚动验证分支必须画出重跑入口——否则本 change 的跨模式场景不可达",
        )

    def test_the_button_is_gated_on_the_file_existing_not_its_size(
        self,
    ) -> None:
        """按钮的禁用判据是「归档 config **在不在**」，不是「它有没有内容」。

        一份存在但**零字节**的归档会让 `_read_config` 返回 `b""`;用内容当
        判据就把按钮永久禁掉、且一个字也不说——而空 YAML 的顶层不是映射，
        本页早已承诺这种形态要被响亮报出（codex P2 on #471）。
        """
        import ast

        source = Path(
            "web/operator_ui/pages/_results_render.py").read_text(
                encoding="utf-8")
        tree = ast.parse(source)
        fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_render_rerun_action"
        )
        buttons = [
            node for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "button"
        ]
        self.assertEqual(len(buttons), 1)
        disabled = {k.arg: k.value for k in buttons[0].keywords}.get("disabled")
        self.assertIsNotNone(disabled, "按钮必须有禁用判据")
        self.assertEqual(ast.unparse(disabled), "not config_present")

    def test_the_pipeline_path_reaches_it_through_the_action_bar(self) -> None:
        # pipeline 那一侧仍然走 `_render_header_actions`（它还带三个导出
        # 按钮）；钉住那个函数**委派**给同一个实现，而不是自己再写一遍。
        tree = ast.parse(self.render)
        header = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_render_header_actions"
        )
        delegates = {
            node.func.id for node in ast.walk(header)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("_render_rerun_action", delegates)

        dashboard = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_render_pipeline_dashboard"
        )
        dash_calls = {
            node.func.id for node in ast.walk(dashboard)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("_render_header_actions", dash_calls)

    def test_the_walk_forward_branch_renders_it_before_the_report_split(
        self,
    ) -> None:
        # 有报告与没报告两种滚动验证结果**都**要有入口:没有报告的那一支
        # 恰恰是「这次跑挂了，想改改参数重跑」最常见的时刻。
        #
        # 用 AST 钉**位置关系**，不钉调用的字面拼写:上一版把整行连同参数
        # 一起钉进串里，给 `_render_rerun_action` 加一个参数就当场失配——
        # 而它要钉的「在报告分叉之前渲染」这件事根本没变（#474 同款教训）。
        import ast

        tree = ast.parse(self.page)
        chain = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and "mode == 'pipeline'" in ast.unparse(node.test)
        )
        wf_branch = chain.orelse
        self.assertTrue(wf_branch, "应当有 walk_forward 分支")

        call_lines = [
            node.lineno
            for stmt in wf_branch for node in ast.walk(stmt)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_render_rerun_action"
        ]
        self.assertEqual(len(call_lines), 1, "滚动验证分支应当只画一次入口")

        split_lines = [
            node.lineno
            for stmt in wf_branch for node in ast.walk(stmt)
            # 只要**内层**那个 `if wf_report:`——外层 elif 的判据里也含
            # `wf_report`(`mode == 'walk_forward' or wf_report`),按子串匹配
            # 会把它一起收进来，比较就成了「在自己之前」这种恒假命题。
            if isinstance(node, ast.If) and ast.unparse(node.test) == "wf_report"
        ]
        self.assertTrue(split_lines, "找不到 `if wf_report:` 分叉")
        self.assertLess(
            call_lines[0], min(split_lines),
            "入口必须在报告分叉**之前**——否则没有报告的那一支就没有入口",
        )



class PrefillSuppliedWiringTests(unittest.TestCase):
    """「这次载荷带了哪些日期字段」——推导正确，且每个控件都拿对了自己的那个。

    AppTest 那一组证的是**绑定函数**在拿到 `supplied=False` 时不改写控件；
    它注入自己的旗标，所以页面这一侧的接线不在它的覆盖里（变异实测:把
    `prefill_supplied` 写死成 True、或把推导算成整个 applicable 集合，
    AppTest 全绿）。这一组补的就是那一段。
    """

    def _page(self) -> str:
        return Path("web/operator_ui/pages/config_run.py").read_text(
            encoding="utf-8")

    def test_the_supplied_set_is_the_payload_intersected_with_known_keys(
        self,
    ) -> None:
        # **真求值**那个赋值:算成「整个 applicable 集合」会让每个日期控件都
        # 以为自己被预填了，于是空载荷/解析失败时照样改写控件。
        import ast

        tree = ast.parse(self._page())
        node = next(
            n for n in tree.body
            if isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == "_PREFILL_SUPPLIED"
        )
        code = compile(ast.Expression(node.value), "<supplied>", "eval")

        applicable = frozenset({"overall_start", "overall_end", "train_start"})
        cases = (
            ({"overall_start": "2020-01-02", "not_a_field": 1},
             {"overall_start"}),
            ({}, set()),
            ({"overall_start": "x", "overall_end": "y"},
             {"overall_start", "overall_end"}),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload):
                got = eval(  # noqa: S307 - 求值的是本页自己的表达式
                    code,
                    {"PREFILL_CONFIG": payload,
                     "_PREFILL_APPLICABLE_KEYS": applicable},
                )
                self.assertEqual(set(got), expected)

    def test_every_date_widget_asks_about_its_own_field(self) -> None:
        # 每个调用点的 `prefill_supplied` 问的必须是**它自己那个字段**。
        # 写死成 True（或抄错字段名）会让空载荷时那个控件被强行改写。
        import ast

        tree = ast.parse(self._page())
        seen: list[tuple[str, str]] = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_select_trading_day"):
                continue
            kw = {k.arg: k.value for k in node.keywords}
            self.assertIn("state_key", kw)
            self.assertIn(
                "prefill_supplied", kw,
                "每个日期控件都要说明这次载荷带没带它自己那个字段",
            )
            state_key = ast.literal_eval(kw["state_key"])
            field = str(state_key).removeprefix("cr_dt_")
            self.assertEqual(
                ast.unparse(kw["prefill_supplied"]),
                f"'{field}' in _PREFILL_SUPPLIED",
                f"{state_key} 问错了字段",
            )
            seen.append((state_key, field))

        self.assertEqual(len(seen), 8, f"日期控件应当有八个,实际 {seen}")
        self.assertEqual(len(set(seen)), 8, "有重复的 state_key")



class ValidEmptyPayloadStillCarriesTheModeTests(unittest.TestCase):
    """合法空 YAML 也是一份**成功解析**的载荷。

    源运行的 `mode` 写在 `job.json` 而**不是**归档 config.yaml 里
    （`JobManager.start(config_dict, mode)` 把两者分开收），所以结果页单独
    把它带过来。用 `if PREFILL_CONFIG:` 当应用判据，重跑一次空归档的
    walk_forward 运行时页面会停在当前的 pipeline 上，模式对比也整个不出
    ——而模式正是本次提交与那次运行最大的一处不同（codex P2 on #471）。
    """

    def test_the_apply_branch_keys_off_a_parsed_payload_not_its_size(
        self,
    ) -> None:
        source = Path("web/operator_ui/pages/config_run.py").read_text(
            encoding="utf-8")
        # 钉**条件整行**。
        self.assertIn("\nif _HAS_PARSED_PREFILL:\n", source)
        self.assertNotIn("\nif PREFILL_CONFIG:\n", source)

    def test_one_predicate_governs_all_three_prefill_decisions(self) -> None:
        """应用分支 / 预设初始化 / 复核区必须由**同一个**判据管。

        在三处各写一遍 ``_HAS_PREFILL_PAYLOAD and not _PREFILL_ERROR`` 就会
        漏——本 PR 上已经漏过两次:先是应用分支还在用「解析出几个字段」，改对
        之后预设初始化那一处又把台账带来的模式打回 pipeline（codex P2 ×2）。
        抽成一个具名常量，三处都引用它，这条钉住那件事。
        """
        import ast

        source = Path("web/operator_ui/pages/config_run.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)

        assigns = [
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(x, ast.Name) and x.id == "_HAS_PARSED_PREFILL"
                    for x in node.targets)
        ]
        self.assertEqual(len(assigns), 1, "共享判据应当只定义一处")
        self.assertEqual(
            ast.unparse(assigns[0].value),
            "_HAS_PREFILL_PAYLOAD and (not _PREFILL_ERROR)",
        )

        uses = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "_HAS_PARSED_PREFILL"
        )
        self.assertEqual(uses, 4, "定义 1 次 + 三处引用")
        # 预设初始化那一处**取反**用它——漏掉它就是 codex 第二次点的那格。
        self.assertIn("    if not _HAS_PARSED_PREFILL:\n", source)

    def test_an_empty_mapping_still_yields_the_source_mode(self) -> None:
        # 真跑那个合成函数:空映射 + 台账带来的模式 ⇒ 基线里有 mode，
        # 于是 `_apply_prefill_to_session` 会把引擎切过去。
        from web.operator_ui.pages._config_run_helpers import (
            prefill_baseline_with_source_mode,
        )

        self.assertEqual(
            prefill_baseline_with_source_mode({}, "walk_forward"),
            {"mode": "walk_forward"},
        )
        # 台账也没记模式时不凭空合成。
        self.assertEqual(prefill_baseline_with_source_mode({}, ""), {})

    def test_the_empty_config_notice_does_not_deny_the_carried_mode(
        self,
    ) -> None:
        # 提示语原本说「本次没有任何字段可预填」——模式被带过来之后那句就
        # 不准了。改成「归档里没有任何字段」，并在有模式时明说它仍会带过来。
        source = Path("web/operator_ui/pages/config_run.py").read_text(
            encoding="utf-8")
        self.assertIn("归档里", source)
        self.assertIn("仍会被带过来", source)



if __name__ == "__main__":
    unittest.main()
