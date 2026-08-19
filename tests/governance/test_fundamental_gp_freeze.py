"""基本面 GP 战役冻结件的钉子：plan ↔ preset ↔ 代码三方一致。

预注册协议是**被代码读取的机器件**。三方任一漂移都必须红：
* plan 改了而 preset 没改 → 跑的不是签署的实验；
* preset 改了而 plan 没改 → 签署的协议不再描述实际的跑；
* 代码（覆盖率地板、终端注册表、算子基线）改了而两者没跟 → 协议
  的证据基础被抽掉。

窗口铁律单独一节：GP 唯一可见窗是 IS，OOS / holdout / forbidden 段
绝不进 preset。
"""
from __future__ import annotations

import dataclasses
import hashlib
import unittest
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_PLAN_REL = "docs/prereg/fundamental_gp_v1.yaml"
_PRESET_REL = "config/factor_mining/fundamental_gp_v1.yaml"
_LEDGER_REL = "docs/prereg/fundamental_gp_ledger.yaml"

_PLAN = yaml.safe_load((_ROOT / _PLAN_REL).read_text(encoding="utf-8"))
_PRESET = yaml.safe_load((_ROOT / _PRESET_REL).read_text(encoding="utf-8"))
_LEDGER = yaml.safe_load((_ROOT / _LEDGER_REL).read_text(encoding="utf-8"))


class FundamentalGPFreezePins(unittest.TestCase):

    # --- 身份 ---------------------------------------------------------

    def test_protocol_identity(self) -> None:
        self.assertEqual("fundamental_gp_v1", _PLAN["protocol_id"])
        self.assertEqual(_PLAN["protocol_id"], _LEDGER["protocol_id"])
        self.assertIs(False, _PLAN["holdout_unblinded"])

    # --- 窗口铁律 -----------------------------------------------------

    def test_the_preset_window_is_exactly_the_is_window(self) -> None:
        """GP 唯一可见窗 = IS。preset 的窗必须逐字等于 plan 的 IS 段。"""
        w = _PLAN["windows"]
        data = _PRESET["data"]
        self.assertEqual(w["is_start"], data["start_date"])
        self.assertEqual(w["is_end"], data["end_date"])

    def test_the_preset_never_reaches_oos_holdout_or_forbidden(self) -> None:
        """独立于上一条的**方向性**断言：即便有人同时改了两处，只要
        preset 的尾巴伸进 OOS/holdout/forbidden，这条仍然红。"""
        import pandas as pd

        w = _PLAN["windows"]
        end = pd.Timestamp(_PRESET["data"]["end_date"])
        self.assertLess(end, pd.Timestamp(w["oos_start"]))
        self.assertLess(end, pd.Timestamp(f"{w['holdout_year']}-01-01"))
        self.assertLess(end, pd.Timestamp(w["forbidden_from"]))

    def test_windows_are_ordered_and_contiguous(self) -> None:
        import pandas as pd

        w = _PLAN["windows"]
        is_end = pd.Timestamp(w["is_end"])
        oos_start = pd.Timestamp(w["oos_start"])
        oos_end = pd.Timestamp(w["oos_end"])
        self.assertLess(is_end, oos_start)
        self.assertLess(oos_start, oos_end)
        self.assertLess(oos_end, pd.Timestamp(f"{w['holdout_year']}-01-01"))
        self.assertLess(pd.Timestamp(f"{w['holdout_year']}-12-31"),
                        pd.Timestamp(w["forbidden_from"]))

    # --- 宇宙与签收名单 -----------------------------------------------

    def test_universe_and_signed_exclusions_match(self) -> None:
        self.assertEqual(_PLAN["universe"]["instruments"],
                         _PRESET["data"]["universe_name"])
        tickers = list(_PRESET["data"]["financial_exclusions"])
        self.assertEqual(_PLAN["universe"]["ex_financials_count"],
                         len(tickers))
        self.assertEqual(len(tickers), len(set(tickers)))
        digest = hashlib.sha256(
            "\n".join(sorted(tickers)).encode("utf-8")).hexdigest()
        self.assertEqual(_PLAN["universe"]["ex_financials_sha256"], digest,
                         "签收名单被改动而未重签：摘要不符。")

    def test_the_exclusion_list_is_the_one_the_operator_signed(self) -> None:
        """与链路验证 preset 是**同一份**名单 —— 两个 preset 各自漂移
        会让两次跑的宇宙不同而无人察觉。"""
        link = yaml.safe_load(
            (_ROOT / "config/factor_mining/fundamental_link_check.yaml")
            .read_text(encoding="utf-8"))
        self.assertEqual(sorted(link["data"]["financial_exclusions"]),
                         sorted(_PRESET["data"]["financial_exclusions"]))

    # --- 终端集：与实测覆盖率地板一致 ---------------------------------

    def test_declared_fields_clear_the_measured_coverage_floor(self) -> None:
        """每个入选字段的 CSI800 实测地板必须 ≥ coverage_min；唯二例外
        是 coalesce 对（单独 0.34/0.12，合并后 0.97）—— 例外本身也被
        钉住，不能悄悄扩大。"""
        from src.factor_mining.fitness import FitnessConfig
        from src.research.financial_pit_coverage_floors import (
            CSI800_ADV_CONTRACT_COALESCE_FLOOR,
            CSI800_COVERAGE_FLOORS,
        )

        coverage_min = FitnessConfig().coverage_min
        self.assertEqual(coverage_min, _PLAN["fitness"]["coverage_min"])
        pair = set(_PLAN["terminals"]["coalesce_pair"])
        for field in _PLAN["terminals"]["charter_fields"]:
            floor = CSI800_COVERAGE_FLOORS[field]
            if field in pair:
                self.assertLess(floor, coverage_min, field)   # 例外确属例外
            else:
                self.assertGreaterEqual(floor, coverage_min, field)
        self.assertGreaterEqual(CSI800_ADV_CONTRACT_COALESCE_FLOOR,
                                _PLAN["terminals"]["coalesce_floor"])

    def test_excluded_fields_are_excluded_for_the_recorded_reason(self) -> None:
        from src.factor_mining.fitness import FitnessConfig
        from src.research.financial_pit_coverage_floors import (
            CSI800_COVERAGE_FLOORS,
        )

        coverage_min = FitnessConfig().coverage_min
        for field, why in _PLAN["terminals"]["excluded"].items():
            self.assertNotIn(field, _PLAN["terminals"]["charter_fields"])
            self.assertEqual(CSI800_COVERAGE_FLOORS[field],
                             why["csi800_floor"], field)
            self.assertLess(why["csi800_floor"], coverage_min, field)

    def test_every_charter_field_exists_in_the_store_contract(self) -> None:
        from src.data.tushare.financial_statements import DATA_FIELDS

        charter = {f for fields in DATA_FIELDS.values() for f in fields}
        declared = set(_PLAN["terminals"]["charter_fields"])
        excluded = set(_PLAN["terminals"]["excluded"])
        self.assertTrue(declared <= charter, declared - charter)
        # 声明 + 排除 = charter 全集：新增字段不会被静默忽略。
        self.assertEqual(charter, declared | excluded)

    def test_preset_fields_and_terminals_match_the_plan(self) -> None:
        from src.factor_mining.grammar import FeatureRegistry

        fields = list(_PRESET["data"]["fundamental_fields"])
        self.assertEqual(sorted(_PLAN["terminals"]["charter_fields"]),
                         sorted(fields))
        expected = set()
        for f in fields:
            expected.add(f"${f}")
            expected.add(f"${f}{FeatureRegistry.PRIOR_SUFFIX}")
        declared = set(_PRESET["gp"]["allowed_terminals"])
        self.assertEqual(expected, declared)
        self.assertEqual(_PLAN["terminals"]["n_terminals"], len(declared))
        self.assertTrue(
            declared <= set(FeatureRegistry.ALL_REGISTERED),
            declared - set(FeatureRegistry.ALL_REGISTERED))

    def test_the_search_space_carries_no_price_volume_terminal(self) -> None:
        """协议冻结的是财报侧搜索空间。量价终端在**面板**里（覆盖率
        分母与 forward-return 几何需要），但绝不在采样白名单里。"""
        from src.factor_mining.grammar import FeatureRegistry

        declared = set(_PRESET["gp"]["allowed_terminals"])
        self.assertFalse(declared & set(FeatureRegistry.V1),
                         declared & set(FeatureRegistry.V1))

    # --- 算子集 -------------------------------------------------------

    def test_operator_whitelist_is_baseline_plus_recorded_amendments(self) -> None:
        from src.factor_mining.grammar import REGISTRY, V1_OPERATORS

        declared = list(_PRESET["gp"]["allowed_operators"])
        self.assertEqual(len(declared), len(set(declared)))
        self.assertIs(True, _PLAN["operators_baseline_28"])
        expected = set(V1_OPERATORS) | set(_PLAN["operators_amended"])
        self.assertEqual(expected, set(declared))
        self.assertEqual(28, len(V1_OPERATORS))
        # 每个"修订入册"的算子必须真的在注册表里（否则协议在描述一个
        # 不存在的搜索空间）。
        registered = {op.name for op in REGISTRY.all_operators()}
        for op in _PLAN["operators_amended"]:
            self.assertIn(op, registered, op)
        self.assertNotIn("ts_cov", declared)
        self.assertIs(True, _PLAN["ts_cov_excluded"])

    # --- 度量与 fitness ------------------------------------------------

    def test_metric_and_fitness_weights_match(self) -> None:
        self.assertEqual(_PLAN["metric"]["forward_return_price"],
                         _PRESET["data"]["forward_return_price"])
        self.assertEqual(_PLAN["metric"]["forward_horizon"],
                         _PRESET["data"]["forward_horizon"])
        for key, value in _PLAN["fitness"].items():
            if key == "complexity_bias_acknowledged":
                continue
            self.assertEqual(value, _PRESET["fitness"][key], key)

    def test_orthogonality_is_off_and_no_baseline_is_bound(self) -> None:
        """本役关闭正交惩罚；配了 baseline 却不用（或反之）都是矛盾配置。"""
        self.assertEqual(0.0, _PLAN["fitness"]["w_orthogonality"])
        self.assertEqual(0.0, _PRESET["fitness"]["w_orthogonality"])
        self.assertEqual("", _PRESET["data"].get("baseline_preds_path", ""))

    # --- 判据 ---------------------------------------------------------

    def test_the_primary_criterion_is_net_excess_and_turnover(self) -> None:
        """操作人 2026-08-17 批准：主判据必须含净超额 + 换手，不能只看
        IC。这条钉住"批准过的东西还在"。"""
        gate = _PLAN["adjudication"]["gate_F_B"]
        self.assertEqual("paired_daily_net_excess", gate["primary_metric"])
        self.assertIn("turnover_ratio_max", gate)
        self.assertGreater(gate["turnover_ratio_max"], 0.0)
        # 换手口径必须是单边年化，与 CSI800 veto③ 同一纯函数族。
        # 换手口径升级为结构件：分子/分母各自命名真实生产者，钉子核实
        # 这两个函数**真的存在**（早先只断言字符串里含 "one_way"，而那
        # 个字符串同时声称分子分母同一个纯函数 —— 实际不是，分子量的是
        # 因子分值的逐日 |Δ|，分母量的是组合权重的 ½·Σ|Δw|）。
        td = gate["turnover_definition"]
        self.assertEqual("max_over_survivors", td["pool_aggregation"])
        self.assertIs(True, td["units_differ_intentionally"])
        self.assertEqual(238, td["annualization_days"])
        for side in ("numerator_producer", "denominator_producer"):
            with self.subTest(side=side):
                rel, _, symbol = td[side].partition("::")
                src = (_ROOT / rel).read_text(encoding="utf-8")
                self.assertIn(f"def {symbol}(", src)
        # 「同配置参照」必须含 rebalance 三字段：对日频参照比周频臂会让
        # 比率无意义地趋零（N5 战役修订过这条）。
        for key in ("rebalance_cadence_days", "rebalance_phase",
                    "rebalance_anchor"):
            self.assertIn(key, gate["shared_between_arms"], key)

    def test_the_state_table_covers_every_outcome(self) -> None:
        """状态必须互斥且穷尽 —— 少一态就有结果无处安放（静默丢弃）。"""
        quad = _PLAN["adjudication"]["gate_F_B"]["state_table"]
        self.assertEqual(
            {"ruler_indistinguishable", "net_negative",
             "survivors_low_turnover", "significant_high_turnover",
             "insufficient_evidence"},
            set(quad))
        actions = {name: body["action"] for name, body in quad.items()}
        self.assertEqual("reject", actions["net_negative"])
        self.assertEqual("reject", actions["ruler_indistinguishable"])
        self.assertEqual("promote_gate", actions["survivors_low_turnover"])
        self.assertEqual("operator_decision",
                         actions["significant_high_turnover"])
        self.assertEqual("no_verdict", actions["insufficient_evidence"])

    def test_gate_state_names_never_collide_with_verdict_names(self) -> None:
        """门内状态名与战役层裁决语**不得同名**：clean_negative 在裁决语
        里指"因子层就没信号"（F_A 无幸存者），若 F_B 的"净超额<=0"也叫
        这个名字，裁决器会把两种相反的幸存者状态记成同一个结论
        （codex #446 r2 P1）。"""
        quad = set(_PLAN["adjudication"]["gate_F_B"]["state_table"])
        verdicts = set(_PLAN["verdict_rules"])
        self.assertFalse({q.upper() for q in quad} & verdicts,
                         {q.upper() for q in quad} & verdicts)
        self.assertNotIn("clean_negative", quad)

    def test_the_verdict_table_aggregates_both_gates(self) -> None:
        """战役层裁决语必须覆盖两门结果的每个组合，且各自写明 when。"""
        verdicts = _PLAN["verdict_rules"]
        self.assertEqual(
            {"CLEAN_NEGATIVE", "SIGNAL_WITHOUT_NET", "INDISTINGUISHABLE",
             "WIN", "OPERATOR_DECISION", "REFUSE", "NO_VERDICT"},
            set(verdicts))
        for name, body in verdicts.items():
            self.assertIn("when", body, name)
            self.assertIn("means", body, name)
        # 有信号但不赚钱与因子层无信号**不是**同一个结论。
        self.assertNotEqual(verdicts["CLEAN_NEGATIVE"]["when"],
                            verdicts["SIGNAL_WITHOUT_NET"]["when"])

    def test_both_gates_declare_their_state_tables(self) -> None:
        """聚合表引用的状态必须在门里**被定义过**：F_A 原先只有
        per_trial_min_n_days 而无状态表，聚合表却引用 sparse_only ——
        未来的裁决器只能自行发明状态转移，或把"全族稀疏"当成"无幸存者"
        产出 CLEAN_NEGATIVE（codex #446 r4 P1）。"""
        adj = _PLAN["adjudication"]
        fa = adj["gate_F_A"]["states"]
        fb = adj["gate_F_B"]["state_table"]
        self.assertEqual({"has_survivors", "no_survivors", "sparse_only"},
                         set(fa))
        for name, body in fa.items():
            self.assertIn("when", body, name)
            self.assertIn("action", body, name)
        self.assertEqual("no_verdict", fa["sparse_only"]["action"])
        self.assertFalse(set(fa) & set(fb), set(fa) & set(fb))

    def test_the_aggregation_table_references_only_defined_states(self) -> None:
        """聚合表里每个 `F_x = state` 引用都必须指向已定义状态，且每个
        状态都至少被一个裁决语覆盖 —— 两个方向都查，悬空引用与孤儿
        状态都是裁决器要自行发明的空白。"""
        import re

        adj = _PLAN["adjudication"]
        fa = set(adj["gate_F_A"]["states"])
        fb = set(adj["gate_F_B"]["state_table"])
        refs = set()
        for body in _PLAN["verdict_rules"].values():
            for m in re.finditer(r"F_([AB]) = (\w+)", body["when"]):
                refs.add((m.group(1), m.group(2)))
        undefined = [(g, n) for g, n in refs
                     if (g == "A" and n not in fa) or (g == "B" and n not in fb)]
        self.assertFalse(undefined, undefined)
        self.assertFalse(fa - {n for g, n in refs if g == "A"})
        self.assertFalse(fb - {n for g, n in refs if g == "B"})

    def test_no_gate_state_name_collides_with_a_verdict_name(self) -> None:
        """分层命名是**绝对规则**（两门都查）：门内状态与战役层裁决语
        永不同名，两门之间也不重名。"""
        adj = _PLAN["adjudication"]
        states = set(adj["gate_F_A"]["states"]) | set(
            adj["gate_F_B"]["state_table"])
        verdicts = set(_PLAN["verdict_rules"])
        self.assertFalse({s.upper() for s in states} & verdicts,
                         {s.upper() for s in states} & verdicts)

    def test_gate_f_b_is_pool_level_so_exactly_one_state_can_hold(self) -> None:
        """F_B 必须是**池级**判据：逐候选跑配对会让多个幸存者落进不同
        分支，聚合表同时匹配 SIGNAL_WITHOUT_NET 与 WIN（codex #446 r5
        P1）。池级 = 一次配对 = 恰好一个状态；而且晋升本就是池级的
        （promote 组装 survivor_pool 整体写生产版本），逐候选挑"最好的"
        等于在裁决时选样。"""
        gate = _PLAN["adjudication"]["gate_F_B"]
        self.assertEqual("survivor_pool_as_one_arm", gate["evaluation_unit"])
        self.assertEqual("gate_F_A = has_survivors", gate["only_if"])

    def test_the_falsifier_separates_direction_from_thesis(self) -> None:
        """三类必须分明：数据契约失败 / 方向阴性 / 论点证伪。"""
        fals = _PLAN["falsifier"]
        self.assertEqual({"setup_failure_refuse", "directional_negative",
                          "thesis_falsified_not_direction"}, set(fals))
        thesis = fals["thesis_falsified_not_direction"]
        self.assertEqual(["F_B = significant_high_turnover"],
                         thesis["states"])
        self.assertTrue(thesis["required_follow_up"])
        self.assertEqual(
            ["F_A = no_survivors", "F_B = net_negative"],
            fals["directional_negative"]["states"])

    def test_the_falsifier_references_states_instead_of_restating_them(
            self) -> None:
        """falsifier 只能**引用**状态名，不得重述条件 —— 重述必漂移：
        r6 把状态表改成裁尺驱动后，这里残留的点估计描述让"CI 跨零 +
        高换手"同时被判 INDISTINGUISHABLE 与论点证伪（codex #446 r7 P1）。
        引用的状态必须都已定义。"""
        adj = _PLAN["adjudication"]
        defined = {f"F_A = {n}" for n in adj["gate_F_A"]["states"]}
        defined |= {f"F_B = {n}" for n in adj["gate_F_B"]["state_table"]}
        for key in ("directional_negative", "thesis_falsified_not_direction"):
            body = _PLAN["falsifier"][key]
            self.assertIn("states", body, key)
            self.assertNotIn("conditions", body, key)   # 不得重述
            for ref in body["states"]:
                self.assertIn(ref, defined, ref)

    def test_promotion_predicates_are_ruler_driven_not_point_estimate(self) -> None:
        """裁尺的红线是 "never a point-estimate winner"：CI 跨零即
        indistinguishable。谓词只看点估计 > 0 会让抽样噪声把池推进
        晋升门（codex #446 r6 P1）。"""
        gate = _PLAN["adjudication"]["gate_F_B"]
        self.assertIn("comparison.py", gate["ruler_verdict_source"])
        table = gate["state_table"]
        for name in ("ruler_indistinguishable", "net_negative",
                     "survivors_low_turnover", "significant_high_turnover"):
            self.assertIn("ruler_verdict ==", table[name]["when"], name)
        # 晋升态必须要求裁尺判 treatment_better，不接受点估计。
        self.assertIn("treatment_better",
                      table["survivors_low_turnover"]["when"])
        for name, body in table.items():
            self.assertNotIn("net_excess_annualized >", body["when"], name)

    def test_setup_failures_never_become_a_directional_negative(self) -> None:
        """数据契约破了 = 实验没做成，不构成反对本方向的证据 —— 否则
        一个坏掉的实验能关掉一条研究方向（codex #446 r6 P1）。"""
        fals = _PLAN["falsifier"]
        self.assertIn("setup_failure_refuse", fals)
        self.assertEqual("REFUSE", fals["setup_failure_refuse"]["verdict"])
        setup = fals["setup_failure_refuse"]["conditions"]
        for marker in ("PIT", "覆盖", "指纹"):
            self.assertIn(marker, setup, marker)
        # 方向阴性侧只引用状态名，物理上不可能含数据契约条件。
        self.assertNotIn("conditions", fals["directional_negative"])
        self.assertIn("REFUSE", _PLAN["verdict_rules"])

    def test_pool_construction_is_frozen_against_the_code(self) -> None:
        """池的构成必须完全确定：阈值/扫描顺序/相关窗任一未冻结，裁决器
        都能在看到 OOS 后保留不同子集，从而移动配对净超额乃至 F_B 裁决
        （codex #446 r8 P1）。逐项与代码现行语义对账。"""
        from src.factor_mining.validator import ValidationCriteria

        pc = _PLAN["adjudication"]["gate_F_B"]["pool_construction"]
        default = ValidationCriteria(is_oos_split_date="2023-01-01")
        self.assertEqual(default.max_pool_correlation,
                         pc["max_pool_correlation"])
        self.assertEqual(["fitness_desc", "canonical_expr_sha256_asc"],
                         pc["ordering"])
        self.assertEqual("refuse", pc["evaluation_failure_policy"])
        # tie-break 绝不能是 Python 加盐 hash（跨进程不稳定）。
        self.assertNotIn("expr_hash", pc["ordering"])
        self.assertEqual("full_panel", pc["correlation_window"])
        self.assertEqual("joint_obs_mask", pc["pairwise_obs"])
        self.assertIs(True, pc["manual_edits_forbidden"])
        self.assertIn("filter_correlated", pc["filter"])

    def test_the_state_count_lives_in_exactly_one_place(self) -> None:
        """状态数只写在 state_count 一个结构字段里，正文与台账一律不
        重述 —— 重述必漂移（ledger r7、协议正文 r8 各漂移过一次）。
        与 falsifier 改为「引用而非重述」是同一条原则。

        钉子做成**绝对规则**（正文零裸计数），不带"这个是裁尺三态所以
        豁免"之类的例外：带例外的规则正是前几轮反复漏掉的原因。
        """
        import re

        gate = _PLAN["adjudication"]["gate_F_B"]
        self.assertEqual(len(gate["state_table"]), gate["state_count"])
        for rel in (_PLAN_REL, _LEDGER_REL):
            text = (_ROOT / rel).read_text(encoding="utf-8")
            stale = [m.group(0) + text[m.end():m.end() + 8]
                     for m in re.finditer(r"[一二三四五六七八九]态", text)]
            self.assertFalse(stale, f"{rel} 正文出现裸的状态计数: {stale}")

    def test_no_prose_re_enumerates_the_state_names(self) -> None:
        """注释里不得重复枚举状态名 —— 枚举会漏（新增
        ruler_indistinguishable 后旧枚举就漏了它，读者据此实现聚合会
        把该结果当未定义，codex #446 r11 P2）。状态名只在 states /
        state_table 两个结构键下出现。与 falsifier「引用而非重述」、
        状态数 state_count 单点记载同一条原则。
        """
        adj = _PLAN["adjudication"]
        names = set(adj["gate_F_A"]["states"]) | set(
            adj["gate_F_B"]["state_table"])
        text = (_ROOT / _PLAN_REL).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue          # 只查散文/注释，不查结构定义与谓词
            hits = {n for n in names if n in stripped}
            self.assertLessEqual(
                len(hits), 1,
                f"注释重复枚举了状态名 {sorted(hits)}: {stripped}")

    def test_plan_and_ledger_agree_on_the_signature(self) -> None:
        """plan 说已签而台账说待签 = 仓库同时暴露两个矛盾治理状态
        （codex #446 r2 P1）。两边双向对账。"""
        entry = next(e for e in _LEDGER["entries"]
                     if e.get("kind") == "protocol_signature")
        self.assertEqual("signed", entry["status"])
        self.assertEqual(_PLAN["signed_at_pr"], entry["signed_at_pr"])
        self.assertTrue(entry["result"])
        for e in _LEDGER["entries"]:
            self.assertNotEqual("pending_signature", e.get("status"), e["id"])

    def test_fwer_thresholds_are_the_a_share_calibrated_ones(self) -> None:
        gate = _PLAN["adjudication"]["gate_F_A"]
        self.assertEqual("one_sided_max_t", gate["statistic"])
        self.assertEqual(2.85, gate["hard_floor_t"])
        self.assertEqual(10000, gate["n_boot"])
        self.assertEqual(21, gate["block_len_days"])

    def test_promotion_is_manual_and_the_consumer_is_not_wired(self) -> None:
        """生产物化消费者未接线是**当前事实**，写进协议；接线后此值翻
        true 与那个 change 同批 —— 这条钉子会提醒改。"""
        self.assertIs(True, _PLAN["promotion"]["manual_only"])
        self.assertIs(False, _PLAN["promotion"]["production_consumer_wired"])

    # --- 实测锚点必须可回溯 -------------------------------------------

    def test_link_check_evidence_is_internally_consistent(self) -> None:
        """协议里的实测数字与台账 E001 记的必须是同一批。"""
        ev = _PLAN["link_check_evidence"]
        self.assertEqual([485, 964], ev["panel_shape"])
        for name in ("C1_GPA", "asset_growth", "C3_cash_based_OP"):
            self.assertIn(name, ev["coverage_observed"])
            self.assertIn(name, ev["turnover_daily_observed"])
            self.assertIn(name, ev["rank_ic_mean_observed"])
            self.assertGreater(ev["coverage_observed"][name], 0.9)
        entry = next(e for e in _LEDGER["entries"] if e["id"] == "E001")
        self.assertEqual("completed", entry["status"])

    def test_the_protocol_carries_a_concrete_signature(self) -> None:
        """signed_at_pr 必须是**具体 PR 号**。

        原写法容许 null，于是"已 merge 但未签署"这个状态永远是绿的 ——
        而门会拒绝该协议下的每一次点火，CI 却看不出任何问题
        （codex #446 r1 P1）。签署即 merge，PR 号在开 PR 时就已知。
        """
        signed = _PLAN["signed_at_pr"]
        self.assertIsInstance(signed, int, signed)
        self.assertGreater(signed, 0, signed)

    def test_the_quad_state_predicates_are_mutually_exclusive(self) -> None:
        """互斥不能靠"优先级"的口头约定：足量前提必须出现在前三态的
        谓词里，否则 100 天的负净超额同时匹配 clean_negative 与
        no_verdict，裁决器可以任选（codex #446 r1 P1）。"""
        gate = _PLAN["adjudication"]["gate_F_B"]
        self.assertIn("sufficiency", gate)
        quad = gate["state_table"]
        for name in ("ruler_indistinguishable", "net_negative",
                     "survivors_low_turnover", "significant_high_turnover"):
            self.assertIn("sufficiency AND", quad[name]["when"], name)
        self.assertEqual("NOT sufficiency",
                         quad["insufficient_evidence"]["when"])


    # --- 生效值冻结（经真 loader，不是 YAML↔YAML）---------------------
    #
    # 此前所有钉子比的都是 plan↔preset 的 YAML 字典，或 plan↔dataclass
    # **缺省值** —— 没有一个比的是"这次跑真正生效的值"。于是凡是钉子没
    # 想到去看的地方，冻结就不存在：preset 加一行 `ic_term: abs_rank_ic`
    # 换掉繁殖判据的公式形状、改 `seed`、把 `pool_top_k` 从 200 压到 10
    # 直接缩小 FWER 家族，35 个钉子全绿（codex #446 自审）。
    #
    # 修在正确层次：钉子调用**运行时用的同一个函数**
    # （campaign 脚本的 assert_mining_config_matches_protocol），钉与
    # 运行时因此不可能分家；再配变异证人，防止钉子本身空转。

    def test_the_effective_config_passes_the_runtime_protocol_check(
            self) -> None:
        """真 loader 加载战役 preset → 运行时协议校验必须放行。"""
        from scripts.research.fundamental_gp_campaign import assert_mining_config_matches_protocol, load_frozen_plan
        from src.factor_mining.miner import load_config

        cfg = load_config(_ROOT / _PRESET_REL)
        assert_mining_config_matches_protocol(cfg, load_frozen_plan())

    def test_the_runtime_check_refuses_every_unfrozen_drift(self) -> None:
        """变异证人：钉子守的每一类漂移都必须真的变红。

        没有这一条，上一条可能是"校验函数什么都不查"也照样绿 ——
        本 PR 正是因为漏搬一个证人才让白名单分支零覆盖。
        """
        import shutil
        import tempfile

        from scripts.research.fundamental_gp_campaign import (
            ProtocolViolation,
            assert_mining_config_matches_protocol,
            load_frozen_plan,
        )
        from src.factor_mining.miner import load_config

        plan = load_frozen_plan()
        drifts = {
            # (丙) preset 单方面加一个协议没点名的键
            "ic_term": lambda r: r["fitness"].update(ic_term="abs_rank_ic"),
            "min_names_per_day": lambda r: r["fitness"].update(
                min_names_per_day=300),
            # (乙) 搜索预算 / 随机性 / 家族规模
            "seed": lambda r: r["gp"].update(seed=7),
            "population_size": lambda r: r["gp"].update(population_size=40),
            "pool_top_k": lambda r: r.update(pool_top_k=10),
            # 窗口 / 宇宙 / 排除集 / 终端 / 算子
            "window": lambda r: r["data"].update(end_date="2023-06-30"),
            "universe": lambda r: r["data"].update(universe_name="csi300"),
            "exclusions": lambda r: r["data"]["financial_exclusions"].pop(),
            "terminals": lambda r: r["gp"].update(
                allowed_terminals=r["gp"]["allowed_terminals"][:-2]),
            "operators": lambda r: r["gp"].update(
                allowed_operators=[o for o in r["gp"]["allowed_operators"]
                                   if o != "coalesce"]),
        }
        for name, mutate in drifts.items():
            with self.subTest(drift=name), tempfile.TemporaryDirectory() as td:
                dst = Path(td) / "preset.yaml"
                shutil.copy(_ROOT / _PRESET_REL, dst)
                raw = yaml.safe_load(dst.read_text(encoding="utf-8"))
                mutate(raw)
                dst.write_text(yaml.safe_dump(raw, allow_unicode=True),
                               encoding="utf-8")
                with self.assertRaises(ProtocolViolation):
                    assert_mining_config_matches_protocol(
                        load_config(dst), plan)

    def test_the_preset_is_bound_to_the_protocol(self) -> None:
        """preset 必须声明 protocol_id —— 没有它，campaign 的 mine 只会
        打一条"本次 run 不受协议保护"的警告然后照跑。"""
        from scripts.research.fundamental_gp_campaign import PROTOCOL_ID

        self.assertEqual(PROTOCOL_ID, _PRESET.get("protocol_id"))
        self.assertEqual(PROTOCOL_ID, _PLAN["protocol_id"])

    def test_every_campaign_subcommand_consumes_the_frozen_protocol(
            self) -> None:
        """协议自称"被代码读取的机器件"——四个子命令必须真的读它。

        此前全仓唯一的读者是本测试文件，即一个零运行时校验的断言。
        """
        import inspect

        from scripts.research import fundamental_gp_campaign as camp

        wired = {
            "_cmd_mine": ("load_frozen_plan",
                          "assert_mining_config_matches_protocol"),
            "_cmd_starter_check": ("load_frozen_plan",),
            "_cmd_record_baseline": ("assert_evaluation_endpoint",),
            "_cmd_promote": ("assert_promotion_criteria_frozen",
                             "assert_promotion_window"),
        }
        # 协议头声称的是**四个**子命令 —— 声明与接线的条数必须相等，
        # 否则就是本 PR 正在修的那一类"协议断言了没做的事"。
        self.assertEqual(4, len(wired))
        for fn_name, must_calls in wired.items():
            src = inspect.getsource(getattr(camp, fn_name))
            for must_call in must_calls:
                with self.subTest(subcommand=fn_name, call=must_call):
                    self.assertIn(must_call, src)
        # run 绑定：**从 argparse 推导**哪些子命令吃 --run，而不是在这里
        # 手写一张表。前三轮 codex 挑的都是同一类"守卫只装了部分子命令"
        # （score_expression 漏、promote 窗口纪律漏、run 绑定漏了
        # record-baseline 与 promote）—— 手写表就是那个病的载体：新增
        # 一个子命令时没人记得来改它。推导则让新子命令自动落网。
        self._assert_every_run_subcommand_binds_to_the_protocol()

    def _assert_every_run_subcommand_binds_to_the_protocol(self) -> None:
        import argparse
        import inspect

        from scripts.research import fundamental_gp_campaign as camp

        parser_holder: dict[str, argparse.ArgumentParser] = {}
        real_init = argparse.ArgumentParser.parse_args

        def _capture(self_parser, *a, **kw):  # noqa: ANN001
            parser_holder["p"] = self_parser
            raise SystemExit(0)

        argparse.ArgumentParser.parse_args = _capture  # type: ignore[method-assign]
        try:
            with self.assertRaises(SystemExit):
                camp._parse_args(["mine", "--config", "x"])
        finally:
            argparse.ArgumentParser.parse_args = real_init  # type: ignore[method-assign]

        sub = next(a for a in parser_holder["p"]._actions
                   if isinstance(a, argparse._SubParsersAction))
        run_based = {
            name: p.get_default("func")
            for name, p in sub.choices.items()
            if any("--run" in (act.option_strings or [])
                   for act in p._actions)
        }
        self.assertTrue(run_based, "没有推导出任何吃 --run 的子命令")
        for name, fn in run_based.items():
            with self.subTest(run_subcommand=name):
                src = inspect.getsource(fn)
                self.assertTrue(
                    "assert_run_matches_protocol" in src
                    or "run_protocol_binding" in src,
                    f"{name} 吃 --run 却不把 run 快照对协议 —— 一个用了"
                    "别的终端/算子/预算/IS 窗的 run 能借本战役的签署外壳"
                    "走完这一步。")
        # 子串匹配骗得过：把调用行注释掉、标识符留在注释里，钉照绿
        # （codex #446 自审 r2 P1）。所以再加一层**行为**验证：真的
        # 调用子命令，喂一份违反协议的输入，要求它拒。
        self._assert_subcommands_actually_refuse()

    def _assert_subcommands_actually_refuse(self) -> None:
        """行为层证人：注释骗不过它，只有真的调用了守卫才会拒。"""
        import shutil
        import tempfile

        from scripts.research.fundamental_gp_campaign import ProtocolViolation, main

        with tempfile.TemporaryDirectory() as td:
            # mine：喂一份协议绑定但 seed 漂移的 preset —— 必须拒。
            bad = Path(td) / "bad_preset.yaml"
            shutil.copy(_ROOT / _PRESET_REL, bad)
            raw = yaml.safe_load(bad.read_text(encoding="utf-8"))
            raw["gp"]["seed"] = 7
            bad.write_text(yaml.safe_dump(raw, allow_unicode=True),
                           encoding="utf-8")
            with self.subTest(subcommand="mine", behaviour="refuses"):
                with self.assertRaises(ProtocolViolation):
                    main(["mine", "--config", str(bad)])
            # promote：喂一份覆盖了冻结判据的晋升配置 —— 必须以 rc=1
            # 受控退出（而不是 traceback、也不是照跑）。
            promo = Path(td) / "promo.yaml"
            promo.write_text(
                yaml.safe_dump({"criteria": {"min_oos_ir": 0.0}}),
                encoding="utf-8")
            with self.subTest(subcommand="promote", behaviour="refuses"):
                rc = main(["promote", "--run", td, "--to", "vX",
                           "--config", str(promo)])
                self.assertEqual(1, rc)
            # record-baseline：把验证窗拉进盲态 holdout 年 —— 必须拒。
            with self.subTest(subcommand="record-baseline",
                              behaviour="refuses"):
                with self.assertRaises(ProtocolViolation):
                    main(["record-baseline", "--run", td, "--end-date",
                          f"{_PLAN['windows']['holdout_year']}-06-30",
                          "--out", str(Path(td) / "b.json")])

    def test_the_promotion_criteria_cannot_be_overridden(self) -> None:
        """晋升配置的 criteria 段偏离冻结值必须被拒。

        池是在 OOS 验证**之后**才组装的，可覆盖的阈值等于把"看到结果
        再决定保留哪些因子"留着 —— 恰是 pool_construction 开篇声称已经
        关掉的自由度。此前只钉了 ValidationCriteria 的 dataclass 缺省。
        """
        import tempfile

        from scripts.research.fundamental_gp_campaign import (
            ProtocolViolation,
            assert_promotion_criteria_frozen,
            load_frozen_plan,
        )

        plan = load_frozen_plan()
        pool_cfg = _PLAN["adjudication"]["gate_F_B"]["pool_construction"]
        frozen = pool_cfg["promotion_criteria_frozen"]
        # 与代码交叉对账：否则这张表是**自证**的 —— 钉子从 plan 读它再
        # 比 plan，把 min_oos_ir 从 0.3 改成 0.0 全绿，而运行时守卫执行
        # 的正是漂移后的那个值（codex #446 自审 r2 P2）。
        from src.factor_mining.validator import ValidationCriteria
        default = ValidationCriteria(is_oos_split_date="2023-01-01")
        for key, want in frozen.items():
            with self.subTest(criterion=key):
                self.assertEqual(getattr(default, key), want)
        # 单点：表里不得再抄一份 max_pool_correlation（本轮 (甲) 自立的
        # "门槛单点"规则；运行时守卫从 pool_construction 那一处读）。
        self.assertNotIn("max_pool_correlation", frozen)
        self.assertIn("max_pool_correlation", pool_cfg)
        with tempfile.TemporaryDirectory() as td:
            ok = Path(td) / "ok.yaml"
            ok.write_text(yaml.safe_dump({"criteria": dict(frozen)}),
                          encoding="utf-8")
            assert_promotion_criteria_frozen(ok, plan)      # 一致 → 放行
            for key in frozen:
                with self.subTest(criterion=key):
                    bad = Path(td) / "bad.yaml"
                    drifted = dict(frozen)
                    drifted[key] = frozen[key] + 1
                    bad.write_text(yaml.safe_dump({"criteria": drifted}),
                                   encoding="utf-8")
                    with self.assertRaises(ProtocolViolation):
                        assert_promotion_criteria_frozen(bad, plan)

    def test_the_holdout_and_forbidden_windows_are_machine_guarded(
            self) -> None:
        """promote._check_pit_window 只有下界没有上界，所以一条合法 CLI
        就能把盲态 holdout 年、以及与生产重叠的 forbidden 段拉进验证。"""
        from scripts.research.fundamental_gp_campaign import (
            ProtocolViolation,
            assert_window_discipline,
            load_frozen_plan,
        )

        plan = load_frozen_plan()
        w = _PLAN["windows"]
        assert_window_discipline(w["oos_end"], plan)        # OOS 尾 → 放行
        for end in (f"{w['holdout_year']}-01-01",
                    f"{w['holdout_year']}-12-31",
                    w["forbidden_from"]):
            with self.subTest(end=end), self.assertRaises(ProtocolViolation):
                assert_window_discipline(end, plan)

    # --- 换手判据：分子/分母 + 门槛单点 --------------------------------

    def test_the_turnover_ratio_names_its_numerator_and_denominator(
            self) -> None:
        """只写 "turnover_ratio <= 门槛" 而不说谁除以谁，留下两种互斥
        读法，且各自废掉一个状态：读成臂比臂则 survivors_low_turnover
        （通往 WIN 的唯一路径）结构性不可达；读成信号比组合则
        significant_high_turnover 永不触发。裁决器可在看到 OOS 之后
        任选 —— 正是预注册要消灭的自由度（codex #446 自审 P1）。"""
        ratio = _PLAN["adjudication"]["gate_F_B"]["turnover_ratio"]
        self.assertEqual("survivor_pool_signal_turnover_one_way_annualized_238",
                         ratio["numerator"])
        self.assertEqual(
            "incumbent_production_sleeve_turnover_one_way_annualized_238",
            ratio["denominator"])
        self.assertIn("cadence_note", ratio)

    def test_the_turnover_threshold_lives_in_exactly_one_place(self) -> None:
        """门槛只写一处，谓词引用字段名而非重述数字。

        与 state_count 单点记载、falsifier「引用而非重述」同一条原则：
        协议自己立了这条绝对规则，却对同样会漂移、且直接决定 WIN vs
        OPERATOR_DECISION 的换手门槛做了三处复述、零钉子。
        """
        import re

        gate = _PLAN["adjudication"]["gate_F_B"]
        threshold = gate["turnover_ratio_max"]
        for state in ("survivors_low_turnover", "significant_high_turnover"):
            with self.subTest(state=state):
                when = gate["state_table"][state]["when"]
                self.assertIn("turnover_ratio_max", when)
                self.assertNotIn(str(threshold), when)
        # F_B 区内该数字只出现一次（无例外规则：带例外的规则正是前几轮
        # 反复漏掉的原因）。
        text = (_ROOT / _PLAN_REL).read_text(encoding="utf-8")
        start = text.index("  gate_F_B:")
        end = text.index("verdict_rules:")
        hits = re.findall(re.escape(str(threshold)), text[start:end])
        self.assertEqual(1, len(hits),
                         f"F_B 区内 {threshold} 出现 {len(hits)} 次")

    # --- FWER 家族口径：plan ↔ ledger ---------------------------------

    def test_plan_and_ledger_agree_on_the_short_window_trials(self) -> None:
        """一个 n_days 低于门槛的候选**是**被 OOS 评估过的：按台账进 N、
        按 plan 旧写法"出族"，同一批候选得到两个 N，而 N 决定自举门槛。
        归一取保守一侧（留在族内、不具备幸存者资格）。"""
        policy = _PLAN["adjudication"]["gate_F_A"]["short_window_trial_policy"]
        self.assertEqual("in_family_not_eligible", policy)
        self.assertIn(policy, _LEDGER["effective_trials_rule"])

    # --- 协议不得断言未接线的机器 --------------------------------------

    def test_the_protocol_does_not_claim_an_unwired_gate(self) -> None:
        """协议此前以现在时断言"门 / 评估器 / 裁决器在启动时加载并校验
        本文件"，而同包台账的 open_items 写着门属后继 PR —— 正文与自己
        的台账互相打脸。改为分列已接线 / 尚未接线，并与台账对账。"""
        header = (_ROOT / _PLAN_REL).read_text(encoding="utf-8")[:3000]
        self.assertIn("尚未接线", header)
        self.assertIn("fundamental_prereg_gate.py", header)
        # 台账仍把它记在 open_items 里 —— 两处必须同时成立。
        opens = yaml.safe_dump(_LEDGER.get("open_items"), allow_unicode=True)
        self.assertIn("fundamental_prereg_gate", opens)


    def test_load_frozen_plan_refuses_a_broken_or_unblinded_protocol(
            self) -> None:
        """load_frozen_plan 里除 protocol_id 外的三条 fail-loud 语义此前
        零证人：holdout 盲态互锁、必需段完整、文件存在。其中盲态互锁是
        "揭盲之后任何战役步骤都不许再跑"的唯一机器实现（codex #446 自审
        r2 P2）。"""
        import shutil
        import tempfile

        from scripts.research.fundamental_gp_campaign import ProtocolViolation, load_frozen_plan

        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "plan.yaml"
            shutil.copy(_ROOT / _PLAN_REL, good)
            load_frozen_plan(good)                       # 未改 → 放行
            cases = {
                "unblinded": lambda d: d.update(holdout_unblinded=True),
                "wrong_id": lambda d: d.update(protocol_id="something_else"),
                "missing_search": lambda d: d.pop("search"),
                "missing_windows": lambda d: d.pop("windows"),
                "missing_universe": lambda d: d.pop("universe"),
            }
            for name, mutate in cases.items():
                with self.subTest(case=name):
                    bad = Path(td) / f"{name}.yaml"
                    raw = yaml.safe_load(good.read_text(encoding="utf-8"))
                    mutate(raw)
                    bad.write_text(yaml.safe_dump(raw, allow_unicode=True),
                                   encoding="utf-8")
                    with self.assertRaises(ProtocolViolation):
                        load_frozen_plan(bad)
            with self.subTest(case="missing_file"):
                with self.assertRaises(ProtocolViolation):
                    load_frozen_plan(Path(td) / "nope.yaml")

    def test_starter_check_binds_by_config_match_not_a_self_declared_field(
            self) -> None:
        """starter-check 的绑定判据必须是"快照记录的配置能否通过协议
        校验"，不能是快照里的自述字段 —— miner 的 config_dump 是一份
        写死的键表，不含 protocol_id，任何靠它做条件的分支都是死代码，
        每个合规 run 都会被自己的工具宣布为非战役证据（codex #446 自审
        r2 P2：第一版就栽在这里）。
        """
        import inspect

        from scripts.research import fundamental_gp_campaign as camp
        from src.factor_mining.miner import MinerConfig

        # 快照写出方从不写 protocol_id —— 这就是第一版的死因，钉死它，
        # 免得将来有人又照那个字段写条件。
        miner_src = (_ROOT / "src/factor_mining/miner.py").read_text(
            encoding="utf-8")
        dump_at = miner_src.index("config_dump = {")
        self.assertNotIn(
            "protocol_id", miner_src[dump_at:dump_at + 1200],
            "run 快照现在会写 protocol_id 了 —— 绑定判据可以简化，"
            "但要先决定它是否进摘要，否则那是不可验证的自述。")
        self.assertNotIn("protocol_id",
                         [f.name for f in dataclasses.fields(MinerConfig)])
        # 绑定判据必须是配置对账（经共用入口 run_protocol_binding，
        # 它内部委托 assert_mining_config_matches_protocol）。
        src = inspect.getsource(camp._cmd_starter_check)
        self.assertIn("run_protocol_binding", src)
        self.assertNotIn('raw_snapshot.get("protocol_id")', src)
        self.assertIn("assert_mining_config_matches_protocol",
                      inspect.getsource(camp.run_protocol_binding))
        # 判定必须**落进报告**，否则报告落盘后与合规 run 形状相同、
        # 无从分辨（codex #446 r13 P2）。
        self.assertIn("protocol_binding", src)

    def test_the_evaluation_endpoint_must_equal_the_frozen_oos_end(
            self) -> None:
        """只做"没越界"会留下选样自由度：record-baseline 与 promote 都
        能用一个截短的 OOS 窗，于是可以在看过若干截短窗之后挑最好看的
        那个去授权裁决（codex #446 r12 P1）。"""
        import tempfile

        from scripts.research.fundamental_gp_campaign import (
            ProtocolViolation,
            assert_evaluation_endpoint,
            assert_promotion_window,
            load_frozen_plan,
        )

        plan = load_frozen_plan()
        oos_end = _PLAN["windows"]["oos_end"]
        assert_evaluation_endpoint(oos_end, plan)          # 等值 → 放行
        for bad in ("2023-06-30", "2024-06-30",
                    f"{_PLAN['windows']['holdout_year']}-06-30",
                    _PLAN["windows"]["forbidden_from"]):
            with self.subTest(end=bad), self.assertRaises(ProtocolViolation):
                assert_evaluation_endpoint(bad, plan)
        # promote 这一路此前完全没有窗口纪律（守卫只装在 record-baseline
        # 上），而 promote._check_pit_window 没有上界。
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "ok.yaml"
            good.write_text(
                yaml.safe_dump({"validation": {"end_date": oos_end}}),
                encoding="utf-8")
            assert_promotion_window(good, plan)
            for label, doc in (
                ("holdout", {"validation": {"end_date": "2025-12-31"}}),
                ("forbidden", {"validation": {"end_date": "2026-06-30"}}),
                ("truncated", {"validation": {"end_date": "2023-06-30"}}),
                ("absent", {"criteria": {}}),
            ):
                with self.subTest(case=label):
                    bad = Path(td) / f"{label}.yaml"
                    bad.write_text(yaml.safe_dump(doc), encoding="utf-8")
                    with self.assertRaises(ProtocolViolation):
                        assert_promotion_window(bad, plan)

    def test_run_binding_verifies_the_mining_digests_before_the_values(
            self) -> None:
        """绑定必须**摘要先行**：先证明快照自挖矿以来没被改过，再谈它
        是否等于协议。

        反过来（只比值不比摘要）等于把绑定建在一段可编辑的文本上 ——
        把一个外来 run 的 config.yaml 改成冻结值，它的池明明是在别的
        搜索下育出来的，却能通过绑定走到晋升（codex #446 r14 P1）。
        与本包对工厂身份立的规矩同源：身份取自防篡改内容，不取自自述。
        """
        import inspect

        from scripts.research import fundamental_gp_campaign as camp

        src = inspect.getsource(camp.run_protocol_binding)
        # 两侧摘要都要，且都在值比对之前。
        self.assertIn("search_definition_sha256", src)
        self.assertIn("_load_run_data_config", src)
        self.assertLess(src.index("search_definition_sha256"),
                        src.index("assert_mining_config_matches_protocol"),
                        "search 摘要必须在值比对之前")
        self.assertLess(src.index("_load_run_data_config"),
                        src.index("assert_mining_config_matches_protocol"),
                        "data 摘要必须在值比对之前")
        # 行为断言而非文档串断言：真的篡改一次快照，要求绑定拒。
        # （断言 docstring 正是本 PR 一路在修的弱钉反模式。）
        import tempfile

        from scripts.research.fundamental_gp_campaign import load_frozen_plan, run_protocol_binding

        plan = load_frozen_plan()
        preset = yaml.safe_load(
            (_ROOT / _PRESET_REL).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            run_dir.mkdir()
            # 一份"值全等于冻结协议、但没有任何挖矿摘要"的伪快照 ——
            # 只比值的绑定会放行它。
            forged = {"data": dict(preset["data"]),
                      "gp": dict(preset["gp"]),
                      "fitness": dict(preset["fitness"]),
                      "pool_top_k": preset["pool_top_k"],
                      "run_id": "forged"}
            (run_dir / "config.yaml").write_text(
                yaml.safe_dump(forged, allow_unicode=True), encoding="utf-8")
            binding = run_protocol_binding(run_dir, plan)
            self.assertFalse(
                binding["matches"],
                "值全等于协议但无挖矿摘要的伪快照通过了绑定 —— 绑定建在"
                "可编辑文本上，外来 run 能借签署外壳走到晋升。")

    def test_the_terminal_whitelist_is_registry_checked_at_construction(
            self) -> None:
        """与 allowed_operators 对称：注册表校验在 __init__。

        放在惰性解析里会被 mutate_subtree / mutate_point 的
        `except (GrammarError, ValueError): return expr` 吞掉，把配置
        错误变成"变异 no-op"（codex #446 r14 P2）。
        """
        from src.factor_mining.fitness import FitnessConfig
        from src.factor_mining.gp_engine import GPConfig, GPEngine
        from src.factor_mining.grammar import GrammarError

        for field, bad in (("allowed_terminals", ("$revenue", "$typo")),
                           ("allowed_operators", ("cs_rank", "typo_op"))):
            with self.subTest(field=field):
                with self.assertRaises(GrammarError):
                    GPEngine(GPConfig(**{field: bad}), FitnessConfig())

    def test_pool_truncation_is_verified_against_the_pool_artifact(
            self) -> None:
        """``pool_top_k`` 是快照里唯一没有摘要背书的语义字段。

        ``search_definition_sha256`` 只覆盖 gp + fitness，
        ``data_definition_sha256`` 只覆盖 data，而它挂在 MinerConfig 上。
        改它两个摘要都不响，随后的值比对比的又正是改过的那个值 ——
        一个只存了少数候选的池就能冒充"最多 200 的冻结族"，而族的大小
        正是 gate_F_A 自举 max-t 门槛的分母（codex #446 r15 P1）。
        只能由池文件本身作证。
        """
        import tempfile

        from scripts.research.fundamental_gp_campaign import (
            ProtocolViolation,
            _minerconfig_from_snapshot,
            _verify_pool_truncation,
        )
        from src.factor_mining.expression import parse_expression
        from src.factor_mining.factor_pool import FactorPool, PoolEntry

        preset = yaml.safe_load(
            (_ROOT / _PRESET_REL).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            pool = FactorPool()
            for i, term in enumerate(
                    ("$revenue", "$revenue__prior", "$total_assets")):
                expr = parse_expression(f"cs_rank({term})")
                pool.add(PoolEntry(
                    expr=expr, fitness=float(i), ic_mean=0.0, ic_std=1.0,
                    ir=0.0, rank_ic_mean=0.0, rank_ic_std=1.0, rank_ir=0.0,
                    turnover_daily=0.0, coverage=1.0, n_obs_per_day_min=1,
                    expr_size=2, expr_hash=hash(expr)))
            pool.save(run_dir)
            base = {"data": dict(preset["data"]), "gp": dict(preset["gp"]),
                    "fitness": dict(preset["fitness"]),
                    "full_pool_size_pre_truncation": 9,
                    "saved_pool_size": 3, "pool_top_k": 3}
            # 自洽：saved == 实际条数 == min(全池, K)
            _verify_pool_truncation(
                run_dir, base, _minerconfig_from_snapshot(base))
            drifts = {
                # 把 K 改大冒充更大的族 —— min(9, 200) = 9 != 3
                "pool_top_k_inflated": {"pool_top_k": 200},
                # 谎报存了更多 —— 与池文件实际条数不符
                "saved_size_lie": {"saved_pool_size": 9},
                # 尺寸记录缺失 —— 截断规则无从证明
                "sizes_absent": {"saved_pool_size": None},
            }
            for name, patch in drifts.items():
                with self.subTest(drift=name):
                    raw = dict(base)
                    for k, v in patch.items():
                        if v is None:
                            raw.pop(k, None)
                        else:
                            raw[k] = v
                    with self.assertRaises(ProtocolViolation):
                        _verify_pool_truncation(
                            run_dir, raw, _minerconfig_from_snapshot(raw))
            # 并且必须**接在** run_protocol_binding 上：只钉函数行为、
            # 不钉它被调用，是本 PR 一路在修的弱钉形态（实测把调用点
            # 拆掉，上面那些子测试照绿）。这里造一份**两个摘要都自洽、
            # 只有截断不自洽**的伪快照，让它只能被截断核验拦下。
            from scripts.research.fundamental_gp_campaign import load_frozen_plan, run_protocol_binding
            from src.factor_mining.miner import data_definition_sha256, search_definition_sha256

            forged = dict(base)
            forged["pool_top_k"] = 200          # 冒充冻结族
            cfg = _minerconfig_from_snapshot(forged)
            forged["data_definition_sha256"] = data_definition_sha256(
                cfg.data)
            forged["search_definition_sha256"] = search_definition_sha256(
                cfg.gp, cfg.fitness)
            (run_dir / "config.yaml").write_text(
                yaml.safe_dump(forged, allow_unicode=True), encoding="utf-8")
            binding = run_protocol_binding(run_dir, load_frozen_plan())
            self.assertFalse(
                binding["matches"],
                "摘要自洽但截断规则不自洽的伪快照通过了绑定 —— 截断核验"
                "没有接在 run_protocol_binding 上。")
            self.assertIn("pool_top_k", str(binding["reason"]))

    def test_the_protocol_does_not_overstate_tamper_evidence(self) -> None:
        """冻结包不得声称它没有的强度。

        run 目录里的每一件都是伪造者可一并改写的：两个既有摘要的比对
        方式是"记录值 vs 就地重算"，两者都住在 run 目录内，实测改值 +
        重算写回即可通过；`pool_top_k` 与 `full_pool_size_pre_truncation`
        更是不在任何摘要覆盖内（codex #446 r15/r16）。协议必须把这个
        上限写明，并与台账 O3 双向对账 —— 与 promotion.production_
        consumer_wired、open_items O1/O2 同款"未接线如实记账"处理。
        """
        plan_text = (_ROOT / _PLAN_REL).read_text(encoding="utf-8")
        self.assertIn("成体系伪造", plan_text)
        self.assertIn("不声称", plan_text)
        opens = yaml.safe_dump(_LEDGER["open_items"], allow_unicode=True)
        self.assertIn("O3", opens)
        self.assertIn("run_id", opens)
        # 两个未被摘要覆盖的字段必须被点名，否则读者会以为它们受保护。
        for field in ("pool_top_k", "full_pool_size_pre_truncation"):
            with self.subTest(field=field):
                self.assertIn(field, plan_text)
                self.assertIn(field, opens)

    def test_an_unrecognized_protocol_id_is_refused_not_downgraded(
            self) -> None:
        """protocol_id 是**三态**，不是两态。

        缺席 = 链路验证之类的非战役批次，照跑并告警；在场但不认识 =
        配置写错了，拒。此前第三态被并进第二态，于是一个 typo 的
        protocol_id 只收到一条"未受协议保护"的警告然后照跑 —— 而 run
        快照本就不写 protocol_id、`run_protocol_binding` 只比生效值，
        那个 run 事后仍可能被判 matches: true 当作战役证据：挖矿说
        "没受管"、绑定说"合规"，两条路径互相打脸（codex #446 r17 P1）。

        与本仓对 typo 的一贯处置一致：typo 的终端名拒、typo 的算子名
        拒，typo 的协议名没有理由被静默降级成"无协议"。
        """
        import shutil
        import tempfile

        from scripts.research import fundamental_gp_campaign as camp

        real_mining = camp.run_mining
        real_factory = camp.build_panel_factory
        camp.run_mining = lambda cfg, **kw: type(  # type: ignore[assignment]
            "R", (), {"run_id": "stub", "pool": []})()
        camp.build_panel_factory = lambda: None  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as td:
                cfg = Path(td) / "c.yaml"
                # 在场但不认识 → 拒
                shutil.copy(_ROOT / _PRESET_REL, cfg)
                raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
                raw["protocol_id"] = "fundamental_gp_v1_typo"
                cfg.write_text(yaml.safe_dump(raw, allow_unicode=True),
                               encoding="utf-8")
                with self.subTest(state="present_but_unknown"):
                    with self.assertRaises(camp.ProtocolViolation):
                        camp.main(["mine", "--config", str(cfg)])
                # 缺席 → 照跑（链路验证批次必须仍然可用）
                shutil.copy(
                    _ROOT / "config/factor_mining/fundamental_link_check.yaml",
                    cfg)
                with self.subTest(state="absent"):
                    self.assertEqual(0, camp.main(
                        ["mine", "--config", str(cfg)]))
        finally:
            camp.run_mining = real_mining  # type: ignore[assignment]
            camp.build_panel_factory = real_factory  # type: ignore[assignment]

if __name__ == "__main__":
    unittest.main()
