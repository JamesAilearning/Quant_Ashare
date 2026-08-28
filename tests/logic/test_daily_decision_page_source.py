"""Source-contract guards for the 今日推荐 page (A2, add-daily-decision-page).

The page's hard boundaries — read-only except journal appends, no job/training
triggers, WARN-never-default banner, registration + documentation — are pinned
at the source level (the repo's UI-page test idiom), plus runtime tests for the
pure helpers.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


def _rendered_strings(source: str) -> tuple[str, ...]:
    """页面真正**渲染出去**的字符串字面量（传给 ``st.*`` 的那些）。

    用 AST 而不是全文串查：这一页大量**否定**执行口径的词（「不表示买入、
    卖出」「请勿据此下单」），全文禁词会把那些正确的免责声明也判红，而真正
    要防的是有人把执行语义**渲染**出来。注释、docstring、变量名都不在这里。

    f-string 的字面段也收（``JoinedStr`` 的 ``Constant`` 部分）——一句
    ``f"目标仓位 {n} 股"`` 的危险部分正是那些字面段。
    """
    collected: list[str] = []

    def _harvest(node: ast.expr) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            collected.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    collected.append(part.value)
        elif isinstance(node, ast.BinOp):
            _harvest(node.left)
            _harvest(node.right)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for element in node.elts:
                _harvest(element)
        elif isinstance(node, ast.Dict):
            for value in node.values:
                _harvest(value)

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_st = (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "st"
        )
        if not is_st:
            continue
        for argument in node.args:
            _harvest(argument)
        for keyword in node.keywords:
            if keyword.arg in {"label", "help", "body", "text", "title"}:
                _harvest(keyword.value)
    return tuple(collected)


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

    def test_the_nominal_baseline_stays_a_read_only_comparison(self) -> None:
        """名义持仓基准段的红线（操作人明令）：只读对照，不越「一键下单只差
        复制粘贴」那条线。

        这一段是这一页第一次出现「应当持有什么」的表述，也因此是最容易往
        执行工具方向滑的地方。红线钉在这里，而不是靠写文档提醒。
        """
        # 一、不接收手输持仓：页面不得出现任何能收持仓的输入控件。
        for input_api in ("st.text_area", "st.number_input", "st.data_editor",
                          "st.file_uploader"):
            self.assertNotIn(input_api, self.page, input_api)
        # 二、不给导出/复制：一份可粘贴的清单就是「只差复制粘贴」的那一步。
        for handoff in ("st.download_button", "execCommand",
                        "navigator.clipboard"):
            self.assertNotIn(handoff, self.page, handoff)
        # 三、执行口径的词不得出现在**渲染出去的文字**里。
        #
        # 刻意不做全文串禁：这一页本来就大量**否定**这些词（「不表示买入、
        # 卖出」「请勿据此下单」），那些正是要保留的。所以**解析**页面，只取
        # 真正传给 `st.*` 的字符串字面量来查——注释与否定说明不在其中，而
        # 一旦有人真的渲染出「差分单」「目标仓位」，它就落在这里。
        rendered = _rendered_strings(self.page)
        self.assertTrue(rendered, "一个渲染字符串都没解析出来——守卫是空的")
        # 名单刻意**窄**：只收「除非真的把功能做出来、否则没有理由出现」的词。
        # 像「调仓指令」「下单」这类，这一页的免责声明本来就要用它们的否定式
        # （「不产生任何调仓指令」），禁掉只会逼出更弱的免责声明。真正的守卫
        # 是上面那两条 affordance 禁令。
        for verb in ("差分单", "缓冲带", "目标仓位", "股数", "手数"):
            for text in rendered:
                self.assertNotIn(verb, text, f"{verb!r} 出现在渲染文字里")
        # 明说它是只读对照，且明说不知道真实持仓。
        self.assertIn("只读对照", self.page)
        self.assertIn("不知道你的实际账户持仓", self.page)

    def test_the_baseline_search_is_wired_and_bounded(self) -> None:
        # 判据整行：只钉函数名的话，把 `found` 分支熄火能原样逃逸。
        self.assertIn(
            "_baseline = find_nominal_baseline(\n"
            "    _artifacts, read_payload=_read_baseline_payload, "
            "as_of=_selected_date,\n"
            ")\n",
            self.page,
        )
        # 三种终局各自有分支，且**互斥**：找到 / 不可知 / 翻完了都没有。
        self.assertIn(
            "\nif _baseline.found and not _baseline_unreadable:\n", self.page)
        self.assertIn("\nelif _baseline_unreadable:\n", self.page)
        self.assertIn("\nelif _baseline.unknowable:\n", self.page)
        self.assertIn("\nif _baseline.skipped:\n", self.page)
        self.assertIn("\nif _baseline.limit_reached:\n", self.page)
        # 读盘走本页同一道输出目录守卫，不另开一条读路径。
        self.assertIn("read_json_artifact(path, artifact_name=path.name)",
                      self.page)
        # 「不可知」与「确实没有」必须分开说：前者表示回溯**停在**一份回答
        # 不了自己的工件上，继续翻出来的清单可能已被它取代。
        self.assertIn("名义持仓基准**不可知**", self.page)
        self.assertIn("回溯到底也没遇到再平衡日", self.page)
        self.assertIn("不等于「没有持仓」", self.page)
        # 损坏的名单**不是**空名单：退成 () 会让页面接着说「共 0 只」，
        # 把一份损坏工件渲染成一个合法的空仓位。
        self.assertIn("_baseline_unreadable = str(_roster_exc)", self.page)
        self.assertIn("**不能**当作名义持仓基准", self.page)
        self.assertNotIn("_baseline_roster = ()\n    st.info", self.page)

    def test_the_stop_explanation_does_not_pick_one_cause_for_both(
        self,
    ) -> None:
        # 「这一份回答不了它自己是不是再平衡日」对**缺口**那一种是假话——缺口
        # 停下时那一份恰恰是一个**经过校验的 HOLD**，问题在它与更早那份之间
        # 那几天。一句话盖住两种成因，就是对其中一种撒谎（#472 学到的同一课，
        # #475 第三轮再次适用）。
        #
        # 钉**条件整行**：钉分支里的字面量会被「条件熄火」变异逃走。
        self.assertIn(
            "    if _blocked.reason == BASELINE_BLOCK_HISTORY_GAP:\n",
            self.page,
        )
        # 缺口那一支说的是「那一天 vs 那一段」，不是「这一份回答不了自己」。
        gap_at = self.page.index("BASELINE_BLOCK_HISTORY_GAP:")
        else_at = self.page.index("    else:\n", gap_at)
        gap_branch = self.page[gap_at:else_at]
        self.assertIn("只证明了**那一天**没换手", gap_branch)
        self.assertNotIn("回答不了它自己是不是再平衡日", gap_branch)

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

    def test_review_progress_is_human_only_and_respects_hold_boundary(self) -> None:
        self.assertIn("summarise_daily_review_progress", self.page)
        self.assertIn("人工审阅进度", self.page)
        self.assertIn("不表示买入、卖出、持仓或订单已执行", self.page)
        self.assertIn("HOLD 日不显示人工审阅完成度", self.page)
        self.assertIn("不计入上方审阅进度", self.page)

    def test_decision_row_lookup_uses_the_same_normalized_candidate_code(self) -> None:
        self.assertIn(
            'str(r.get("代码") or "").strip() == _sel_code', self.page
        )

    def test_candidates_render_before_journal_failures_and_audit_keeps_history(self) -> None:
        self.assertIn("_candidate_table_slot = st.empty()", self.page)
        self.assertLess(
            self.page.index("with _candidate_table_slot:"),
            self.page.index("_journal_file = journal_path()"),
        )
        self.assertIn("for entry in _journal.entries", self.page)
        self.assertNotIn(
            "for (t_date, _code), entry in sorted(_journal.effective.items())",
            self.page,
        )

    def test_invalid_candidate_keys_disable_projection_without_hiding_journal_audit(self) -> None:
        """Candidate ambiguity must not turn a valid journal into invisible history."""
        candidate_start = self.page.index("_codes = validate_review_candidate_codes(")
        candidate_section = self.page[
            candidate_start : self.page.index("if _hold.is_hold:", candidate_start)
        ]
        self.assertIn("_candidate_codes_valid = False", candidate_section)
        self.assertNotIn("st.stop()", candidate_section)
        self.assertIn("_codes = ()", candidate_section)

        summary_start = self.page.index("with _review_summary_slot:")
        summary_section = self.page[
            summary_start : self.page.index("with _candidate_table_slot:", summary_start)
        ]
        self.assertIn("elif not _candidate_codes_valid:", summary_section)
        self.assertIn("_review_progress = None", summary_section)
        self.assertLess(
            self.page.index("_journal = read_journal()"),
            self.page.index("_today_entries = ["),
        )


class RegistrationAndDocsTests(unittest.TestCase):
    def test_page_registered_in_daily_decision_group_with_icon(self) -> None:
        app = _APP.read_text(encoding="utf-8")
        self.assertIn('daily_decision.py"), title="日度信号与人工决策"', app)
        self.assertIn('"日度信号与人工决策": "\\U0001f4dd"', app)

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

    def test_cost_reference_uses_the_certified_round_trip(self) -> None:
        # The old 30 bps literal predated the csi800 N5 certification
        # (20 bps one-way conservative) and understated the true cost by
        # roughly half — every row's anchor was optimistic. The value is
        # now ASSEMBLED from the certified components, so it cannot rot
        # independently of them.
        from web.operator_ui.pages._daily_decision_helpers import (
            ROUND_TRIP_COST,
            cost_reference,
        )
        self.assertAlmostEqual(ROUND_TRIP_COST, 0.0055)
        self.assertAlmostEqual(cost_reference(0.0123), 0.0068)

    def test_round_trip_tracks_the_certified_profile_not_a_literal(self) -> None:
        # Move-the-source linkage: the slippage term is READ from the
        # certified guard profile, so re-pointing the profile moves the
        # UI anchor with it. A restated literal would silently disagree.
        from unittest import mock

        import web.operator_ui.pages._daily_decision_helpers as helpers
        from scripts.eval_profiles import EVAL_PROFILES

        self.assertAlmostEqual(
            helpers._CERTIFIED_SLIPPAGE_BPS,
            float(EVAL_PROFILES["csi800_n5"]["slippage_bps"]),
        )
        moved = {**EVAL_PROFILES, "csi800_n5": {
            **EVAL_PROFILES["csi800_n5"], "slippage_bps": 33.0,
        }}
        import importlib

        try:
            with mock.patch.dict(
                "scripts.eval_profiles.EVAL_PROFILES", moved, clear=True
            ):
                reloaded = importlib.reload(helpers)
                self.assertAlmostEqual(reloaded._CERTIFIED_SLIPPAGE_BPS, 33.0)
                # open + close each carry one slippage leg.
                self.assertAlmostEqual(
                    reloaded.ROUND_TRIP_COST, 0.0005 * 2 + 0.0005 + 0.0033 * 2
                )
        finally:
            # 恢复必须在 patch **退出之后**——在 patch 内重载会把 33.0 固化
            # 进模块常量并泄漏给后续用例（本文件里就有一个断言 20.0 的用例
            # 因此在批量运行时失败、单独运行时通过）。
            importlib.reload(helpers)
        self.assertAlmostEqual(
            helpers._CERTIFIED_SLIPPAGE_BPS,
            float(EVAL_PROFILES["csi800_n5"]["slippage_bps"]),
            "reload must restore the real profile value",
        )

    def test_duplicated_cost_constants_match_their_canonical_sources(self) -> None:
        # commission / stamp are duplicated here on purpose (the contract
        # module pulls qlib into a production-facing page), so CI — not a
        # rotting literal — is what keeps them honest. Same treatment
        # update_status.py gets for the writer's constants.
        from web.operator_ui.pages._daily_decision_helpers import (
            _COMMISSION_RATE,
            _STAMP_TAX_BPS,
        )
        try:
            from src.core.canonical_backtest_contract import (
                CN_STAMP_TAX_SCHEDULE_DEFAULT,
            )
            from src.core.pipeline import PipelineConfig
            from src.core.walk_forward.config import WalkForwardConfig
        except ImportError as exc:  # pragma: no cover - dep-light cell
            # 只在**确认 qlib 缺席**时跳过。本测试是那两个刻意复制的常量
            # 唯一的防漂移机制——宽泛地吞掉任何异常,会让一个 import 期的
            # NameError/误删模块把它静默摘掉而 CI 报绿(codex #443 r2);
            # 而 `import qlib` 探测同样会吞掉 qlib 自己 __init__ 抛的
            # ImportError,所以用 find_spec 只判存在性、不执行包体(r3)。
            import importlib.util

            if importlib.util.find_spec("qlib") is None:
                self.skipTest("qlib absent (dep-light cell)")
            raise AssertionError(
                f"qlib is present but the canonical modules failed to "
                f"import — this is a regression, not a missing dependency: "
                f"{exc!r}"
            ) from exc
        canonical_commission = PipelineConfig.__dataclass_fields__[
            "commission_rate"
        ].default
        self.assertAlmostEqual(
            _COMMISSION_RATE, float(canonical_commission),
            "commission drifted from the canonical dataclass default",
        )
        # Both engines must agree, or "the canonical default" is ambiguous.
        self.assertAlmostEqual(
            float(canonical_commission),
            float(
                WalkForwardConfig.__dataclass_fields__["commission_rate"].default
            ),
        )
        self.assertAlmostEqual(
            _STAMP_TAX_BPS, float(CN_STAMP_TAX_SCHEDULE_DEFAULT[-1].bps),
            "stamp tax drifted from the canonical CN schedule",
        )

    def test_entry_caption_does_not_negate_the_artifact_contract(self) -> None:
        # codex #443 r1: spec 明写 rebalance_day=true 时「本列表是可执行的
        # T+1 入场清单」。页面纠正的应当只是**时点**误读（按明早开盘价
        # 买入），不能笼统否定可执行性——那会让该执行的清单被当成不该
        # 执行。可执行性由 rebalance_day / HOLD 横幅单独承载。
        src = _PAGE.read_text(encoding="utf-8")
        self.assertIn("是已收盘会话", src)
        self.assertIn("明早开盘按市价买入", src)
        self.assertIn("是否构成入场指令", src)
        # 旧的笼统措辞不得残留。
        self.assertNotIn("不是\n    「明早买入」", src)
        self.assertNotIn("」——每次必读这一行", src)

    def test_missing_entry_date_is_refused_not_certified(self) -> None:
        # codex #443 r2: 缺 entry_date 的工件若走进那条 caption，会渲染出
        # 「entry — 是已收盘会话」——把一份违约的数据当成可信引导来背书。
        src = _PAGE.read_text(encoding="utf-8")
        self.assertIn("工件契约被违反", src)
        # 断言必须在 caption **之前**，否则先背书再校验等于没校验。
        guard_at = src.index("_entry_date = _payload.get(\"entry_date\")")
        caption_at = src.index("是已收盘会话**")
        self.assertLess(guard_at, caption_at)
        # 且 caption 只能读已校验过的局部变量，不能再从 payload 里取。
        self.assertNotIn(
            "**entry {_payload.get('entry_date', '—')} 是已收盘会话**", src
        )

    def test_entry_timing_requires_strict_forward_iso_dates(self) -> None:
        from web.operator_ui.pages._daily_decision_helpers import (
            artifact_entry_timing_is_valid,
        )

        assert artifact_entry_timing_is_valid(
            {"as_of_date": "2026-08-21", "entry_date": "2026-08-24"}
        ) is True
        for payload in (
            {"as_of_date": "2026-08-21", "entry_date": "2026-99-99"},
            {"as_of_date": "2026-08-21", "entry_date": "tomorrow"},
            {"as_of_date": "2026-08-21", "entry_date": "20260824"},
            {"as_of_date": "2026-08-21", "entry_date": " 2026-08-24"},
            {"as_of_date": "2026-08-21", "entry_date": "2026-08-21"},
            {"as_of_date": "2026-08-21", "entry_date": "2026-08-20"},
            {"as_of_date": "not-a-date", "entry_date": "2026-08-24"},
        ):
            with self.subTest(payload=payload):
                assert artifact_entry_timing_is_valid(payload) is False

        src = _PAGE.read_text(encoding="utf-8")
        self.assertIn("artifact_entry_timing_is_valid(_payload)", src)
        self.assertIn(
            "_artifact_contract_valid = _entry_date_is_valid and _artifact_schema_supported",
            src,
        )

    def test_slippage_in_caption_is_derived_not_restated(self) -> None:
        # codex #443 r1: 常量与列名都随 profile 走，而文案里写死的
        # 「20 bps」不会——profile 一挪就三处对不上，正是本 PR 要消灭的
        # 那种 drift 又长回来。
        src = _PAGE.read_text(encoding="utf-8")
        self.assertIn("CERTIFIED_SLIPPAGE_BPS", src)
        self.assertNotIn("(20 bps 单边滑点", src)
        from scripts.eval_profiles import EVAL_PROFILES
        from web.operator_ui.pages._daily_decision_helpers import (
            CERTIFIED_SLIPPAGE_BPS,
        )

        self.assertAlmostEqual(
            CERTIFIED_SLIPPAGE_BPS,
            float(EVAL_PROFILES["csi800_n5"]["slippage_bps"]),
        )

    def test_cost_column_header_is_derived_from_the_constant(self) -> None:
        # The old header hardcoded "30bps" next to a value that could move —
        # header and subtrahend must come from one source or the table lies.
        from web.operator_ui.pages._daily_decision_helpers import (
            COST_REFERENCE_COLUMN,
            ROUND_TRIP_COST,
        )
        self.assertIn(f"{ROUND_TRIP_COST * 1e4:.0f}bps", COST_REFERENCE_COLUMN)
        src = (
            _ROOT / "web" / "operator_ui" / "pages"
            / "_daily_decision_helpers.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("评分−30bps", src, "stale literal header must be gone")

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

    def test_empty_artifact_state_stops_in_streamlit_without_hiding_errors(
            self) -> None:
        """The runner's stop signal, not SystemExit, owns empty-state flow.

        The harness executes the page's actual empty-state AST with the
        control-flow signal that Streamlit raises for ``st.stop``.  A
        hand-written ``SystemExit`` made a bare Python import succeed before
        later module errors could run.  The isolated page smoke below executes
        the complete Streamlit module without contaminating this test process
        (codex P2 on #457).
        """
        import ast
        import subprocess
        import sys
        from unittest.mock import Mock

        class _StreamlitStop(Exception):
            pass

        class _StreamlitHarness:
            def stop(self) -> None:
                raise _StreamlitStop()

        source = _PAGE.read_text(encoding="utf-8")
        empty_start = source.index("if not _artifacts:")
        empty_section = source[empty_start : source.index("_date_options =", empty_start)]
        self.assertIn("st.stop()", empty_section)
        self.assertIn("暂无日度信号工件", empty_section)
        self.assertNotIn("raise SystemExit", empty_section)

        page_tree = ast.parse(source, filename=str(_PAGE))
        empty_state = next(
            node for node in page_tree.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.op, ast.Not)
            and isinstance(node.test.operand, ast.Name)
            and node.test.operand.id == "_artifacts"
        )
        render_empty = Mock()
        namespace = {
            "_artifacts": [],
            "render_empty_state": render_empty,
            "st": _StreamlitHarness(),
        }
        with self.assertRaises(_StreamlitStop):
            exec(
                compile(
                    ast.Module(body=[empty_state], type_ignores=[]),
                    str(_PAGE),
                    "exec",
                ),
                namespace,
            )
        render_empty.assert_called_once()

        smoke = (
            "from streamlit.testing.v1 import AppTest\n"
            f"app = AppTest.from_file({str(_PAGE)!r})\n"
            "app.run(timeout=30)\n"
            "assert not app.exception, app.exception\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", smoke],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            result.returncode,
            0,
            "Streamlit 页面烟测失败：" + result.stderr,
        )

    def test_empty_candidate_review_progress_renders_zero_metrics(self) -> None:
        """An empty valid signal still exposes every review-progress metric."""
        import ast

        from web.operator_ui.pages._daily_review_progress_helpers import (
            DailyReviewProgress,
        )

        class _Column:
            def __init__(self) -> None:
                self.metrics: list[tuple[str, int]] = []

            def metric(self, label: str, value: int) -> None:
                self.metrics.append((label, value))

        class _StreamlitHarness:
            def __init__(self) -> None:
                self.infos: list[str] = []
                self.columns_created: list[_Column] = []

            def subheader(self, _label: str) -> None:
                pass

            def info(self, message: str) -> None:
                self.infos.append(message)

            def columns(self, count: int) -> list[_Column]:
                columns = [_Column() for _ in range(count)]
                self.columns_created.extend(columns)
                return columns

            def caption(self, _message: str) -> None:
                pass

        tree = ast.parse(_PAGE.read_text(encoding="utf-8"), filename=str(_PAGE))
        renderer = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_render_review_progress"
        )
        harness = _StreamlitHarness()
        namespace = {"st": harness, "DailyReviewProgress": DailyReviewProgress}
        exec(
            compile(ast.Module(body=[renderer], type_ignores=[]), str(_PAGE), "exec"),
            namespace,
        )
        namespace["_render_review_progress"](
            DailyReviewProgress(
                trade_date="2026-08-20",
                candidates=(),
                candidate_count=0,
                reviewed_count=0,
                unreviewed_count=0,
                adopt_count=0,
                reject_count=0,
                watch_count=0,
                latest_reviewed_at=None,
            )
        )

        self.assertEqual(
            [metric for column in harness.columns_created for metric in column.metrics],
            [
                ("候选", 0), ("已审阅", 0), ("未审阅", 0),
                ("人工采纳", 0), ("人工拒绝", 0), ("人工观望", 0),
            ],
        )
        self.assertEqual(harness.infos, ["当前有效信号没有候选；各项人工审阅统计均为 0。"])

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
        # Header comes from the constant, so this reads the same key the
        # page renders even after a cost-convention change.
        from web.operator_ui.pages._daily_decision_helpers import (
            COST_REFERENCE_COLUMN,
            cost_reference,
        )
        self.assertAlmostEqual(
            float(rows[0][COST_REFERENCE_COLUMN]), cost_reference(0.0123),
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
        # The documented default is a WINDOWS path, so the host's own
        # absoluteness rule is pinned to Windows here — otherwise on the
        # POSIX legs it reads as a foreign spelling, `resolve_incumbent`
        # refuses it before loading (correctly, codex #431 r31) and this
        # test would be asserting the platform rather than the rule it is
        # about (codex #431 r31 / same class as W35).
        import ntpath
        import os
        from unittest.mock import patch

        # Patch where the resolver LIVES (web.operator_ui.incumbent), not
        # where 今日推荐 re-exports it from: the resolver moved to package
        # level so 生产运维 asks the same code, and a page-local patch would
        # no longer intercept the call it is meant to observe.
        from web.operator_ui import incumbent as H
        with patch.dict(os.environ, {H.ENV_ENSEMBLE_MANIFEST: ""}, clear=False):
            with patch.object(H, "_host_is_fully_qualified", ntpath.isabs), \
                    patch.object(H, "load_ensemble_manifest_identity") as fake:
                fake.return_value = H.IncumbentIdentity(kind="ensemble")
                H.resolve_incumbent()
            fake.assert_called_once_with(H.DEFAULT_ENSEMBLE_MANIFEST)
            # …and on a host where that spelling is NOT usable it still must
            # not degrade to the single-model shape — the actual invariant.
            import posixpath
            with patch.object(H, "_host_is_fully_qualified", posixpath.isabs):
                self.assertEqual("unresolvable", H.resolve_incumbent().kind)

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

    def test_only_verified_and_contract_valid_artifacts_allow_review_progress_projection(self) -> None:
        from web.operator_ui.pages import _daily_decision_helpers as helpers

        verdicts = {
            value
            for name, value in vars(helpers).items()
            if name.startswith("VERDICT_")
        }
        self.assertEqual(
            {
                verdict
                for verdict in verdicts
                if helpers.review_progress_is_available(
                    verdict=verdict, artifact_contract_valid=True
                )
            },
            {helpers.VERDICT_MATCHES_INCUMBENT, helpers.VERDICT_SINGLE_SHA_OK},
        )
        assert helpers.review_progress_is_available(
            verdict=helpers.VERDICT_MATCHES_INCUMBENT, artifact_contract_valid=False,
        ) is False
        for payload in (
            {},
            {"artifact_schema_version": False},
            {"artifact_schema_version": "2"},
            {"artifact_schema_version": 1},
            {"artifact_schema_version": 3},
        ):
            with self.subTest(payload=payload):
                assert helpers.artifact_schema_is_supported(payload) is False
                assert helpers.review_progress_is_available(
                    verdict=helpers.VERDICT_MATCHES_INCUMBENT,
                    artifact_contract_valid=helpers.artifact_schema_is_supported(payload),
                ) is False
        assert helpers.artifact_schema_is_supported({"artifact_schema_version": 2})
        review_start = self.page.index("with _review_summary_slot:")
        review_section = self.page[
            review_start
            : self.page.index("with _candidate_table_slot:", review_start)
        ]
        self.assertIn("if not review_progress_is_available(", review_section)
        self.assertIn("artifact_contract_valid=_artifact_contract_valid", review_section)
        self.assertIn("_review_progress = None", review_section)

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

    def test_the_shipped_spec_does_not_claim_unset_means_single(self) -> None:
        # Repointed at archive time: the proposal is now frozen history that
        # cannot regress, while THIS is the contract a future edit can
        # contradict — and the rule made it into the shipped spec, so the
        # guard follows it there rather than pinning a path the archive
        # renames.
        spec = (_ROOT / "openspec" / "specs" / "v2-daily-decision-page"
                / "spec.md").read_text(encoding="utf-8")
        self.assertIn("MUST NOT 推断为单模型", spec)
        self.assertIn("MUST NOT 因变量缺席就断定生产为单模型形态", spec)
        self.assertNotIn("单模型（未设该变量）", spec)
