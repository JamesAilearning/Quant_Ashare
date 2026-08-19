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


def _make_campaign_run(run_dir: Path, *, governed: bool = True,
                       pool_size: int = 3, full_size: int = 3,
                       top_k: int | None = None) -> None:
    """在 ``run_dir`` 里造一个**完整合法**的战役 run。

    值等于冻结协议、两个摘要自洽、池文件与尺寸记录自洽、治理记录说
    受管 —— 也就是说它能通过 ``run_protocol_binding`` 的每一道检查。

    有了它，每条钉才能只被它声称的那一道拦下。此前两条钉是空转的：
    伪造的 run 连 sidecar 都没有，于是不论被测的那道检查在不在，都会
    在更早的一道上被拒 —— 变异实测 M-i / M-j 全绿才暴露出来
    （与本 PR 一路在修的弱钉是同一个形态）。
    """
    import json

    from scripts.research.fundamental_gp_campaign import CAMPAIGN_BINDING_FILE, PROTOCOL_ID, _minerconfig_from_snapshot
    from src.factor_mining.expression import parse_expression
    from src.factor_mining.factor_pool import FactorPool, PoolEntry
    from src.factor_mining.miner import data_definition_sha256, search_definition_sha256

    preset = yaml.safe_load((_ROOT / _PRESET_REL).read_text(encoding="utf-8"))
    terms = list(preset["gp"]["allowed_terminals"])
    pool = FactorPool()
    for i in range(pool_size):
        expr = parse_expression(f"cs_rank({terms[i % len(terms)]})")
        pool.add(PoolEntry(
            expr=expr, fitness=float(-i), ic_mean=0.0, ic_std=1.0, ir=0.0,
            rank_ic_mean=0.0, rank_ic_std=1.0, rank_ir=0.0,
            turnover_daily=0.0, coverage=1.0, n_obs_per_day_min=1,
            expr_size=2, expr_hash=hash(expr) + i))
    pool.save(run_dir)
    snapshot = {
        "data": dict(preset["data"]),
        "gp": dict(preset["gp"]),
        "fitness": dict(preset["fitness"]),
        "pool_top_k": preset["pool_top_k"] if top_k is None else top_k,
        "run_id": "fixture",
        "saved_pool_size": pool_size,
        "full_pool_size_pre_truncation": full_size,
    }
    cfg = _minerconfig_from_snapshot(snapshot)
    snapshot["data_definition_sha256"] = data_definition_sha256(cfg.data)
    snapshot["search_definition_sha256"] = search_definition_sha256(
        cfg.gp, cfg.fitness)
    (run_dir / "config.yaml").write_text(
        yaml.safe_dump(snapshot, allow_unicode=True), encoding="utf-8")
    (run_dir / CAMPAIGN_BINDING_FILE).write_text(
        json.dumps({"protocol_id": PROTOCOL_ID if governed else None,
                    "governed": governed}), encoding="utf-8")


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
            # 协议由 main 在分派前加载，子命令经 `_plan_of(args)` 取用
            # —— 不再各自 load_frozen_plan（那是"子命令内次序"问题的
            # 来源，见 test_the_plan_is_loaded_before_dispatch）。
            "_cmd_mine": ("_plan_of",
                          "assert_mining_config_matches_protocol"),
            "_cmd_starter_check": ("_plan_of",),
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

        from scripts.research.fundamental_gp_campaign import main

        with tempfile.TemporaryDirectory() as td:
            # mine：喂一份协议绑定但 seed 漂移的 preset —— 必须拒。
            bad = Path(td) / "bad_preset.yaml"
            shutil.copy(_ROOT / _PRESET_REL, bad)
            raw = yaml.safe_load(bad.read_text(encoding="utf-8"))
            raw["gp"]["seed"] = 7
            bad.write_text(yaml.safe_dump(raw, allow_unicode=True),
                           encoding="utf-8")
            with self.subTest(subcommand="mine", behaviour="refuses"):
                self.assertEqual(1, main(["mine", "--config", str(bad)]))
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
                self.assertEqual(1, main(
                    ["record-baseline", "--run", td, "--end-date",
                     f"{_PLAN['windows']['holdout_year']}-06-30",
                     "--out", str(Path(td) / "b.json")]))

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
            # 治理记录写成"受管"，好让这条用例真的落到截断核验上，
            # 而不是被前一道 sidecar 检查拦下。
            import json as _json

            from scripts.research.fundamental_gp_campaign import CAMPAIGN_BINDING_FILE, PROTOCOL_ID
            (run_dir / CAMPAIGN_BINDING_FILE).write_text(
                _json.dumps({"protocol_id": PROTOCOL_ID, "governed": True}),
                encoding="utf-8")
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
        stub_out = Path(tempfile.mkdtemp())
        camp.run_mining = lambda cfg, **kw: type(  # type: ignore[assignment]
            "R", (), {"run_id": "stub", "pool": [],
                      "output_dir": stub_out})()
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
                    # CLI 边界把协议拒绝变成**受控非零退出**，不是
                    # traceback（codex #446 r21 P2）。
                    self.assertEqual(1, camp.main(
                        ["mine", "--config", str(cfg)]))
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

    def test_mining_time_governance_is_recorded_not_re_derived(self) -> None:
        """治理判定由挖矿**记下**，下游读它，不从值重推。

        值重推分不清"当初受协议管"与"碰巧值一样"：把 protocol_id 从战役
        preset 里删掉，mine 会如实打出"本 run 不受协议保护"然后照跑，而
        事后按值重推却判 matches: true —— 同一个 run 两个答案
        （codex #446 r17/r18 各命中三态里的一态；根因是这个状态从来没被
        记下来过）。
        """
        import json
        import tempfile

        from scripts.research.fundamental_gp_campaign import (
            CAMPAIGN_BINDING_FILE,
            PROTOCOL_ID,
            load_frozen_plan,
            run_protocol_binding,
        )

        plan = load_frozen_plan()
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            # **完整合法**的战役 run：值等于协议、摘要自洽、池自洽。
            # 于是唯一还能拒它的就是治理记录本身 —— 这条钉才有鉴别力。
            _make_campaign_run(run_dir, governed=True)
            self.assertTrue(
                run_protocol_binding(run_dir, plan)["matches"],
                "fixture 本身就不合规，后面的对照没有意义")
            for label, record in (
                ("ungoverned", {"protocol_id": None, "governed": False}),
                ("foreign", {"protocol_id": "other_v1", "governed": True}),
                # 关键鉴别用例：protocol_id **正确**，只有 governed 是
                # 畸形值。真值判断会把非空字符串 "false" 当成受管
                # （codex #446 r19 P2）—— 必须 `is True`。
                ("governed_is_string_false",
                 {"protocol_id": PROTOCOL_ID, "governed": "false"}),
                ("governed_is_string_true",
                 {"protocol_id": PROTOCOL_ID, "governed": "true"}),
                ("governed_is_one",
                 {"protocol_id": PROTOCOL_ID, "governed": 1}),
                ("governed_absent", {"protocol_id": PROTOCOL_ID}),
            ):
                with self.subTest(record=label):
                    (run_dir / CAMPAIGN_BINDING_FILE).write_text(
                        json.dumps(record), encoding="utf-8")
                    binding = run_protocol_binding(run_dir, plan)
                    self.assertFalse(
                        binding["matches"],
                        f"{label}: 值匹配就改判了 —— 治理判定又被重推。")
            # 完全没有记录也不行（存量 run / 非本入口挖出的 run）。
            with self.subTest(record="absent"):
                (run_dir / CAMPAIGN_BINDING_FILE).unlink()
                self.assertFalse(
                    run_protocol_binding(run_dir, plan)["matches"])

    def test_run_protocol_binding_never_raises(self) -> None:
        """契约说"不抛，返回机器可读判定"，就必须真的不抛。

        枚举异常类型必然漏 —— 实测漏过 yaml.ParserError 与
        FileNotFoundError，两者都会让 starter-check / record-baseline /
        promote 以 traceback 收场（codex #446 r18 P2）。语义上任何无法
        确立匹配的失败就是"不匹配"。
        """
        import tempfile

        from scripts.research.fundamental_gp_campaign import (
            CAMPAIGN_BINDING_FILE,
            load_frozen_plan,
            run_protocol_binding,
        )

        plan = load_frozen_plan()
        with tempfile.TemporaryDirectory() as td:
            cases = {
                "empty_dir": lambda d: None,
                "bad_yaml": lambda d: (d / "config.yaml").write_text(
                    "data: [unclosed\n", encoding="utf-8"),
                "bad_json_record": lambda d: (
                    d / CAMPAIGN_BINDING_FILE).write_text(
                        "{not json", encoding="utf-8"),
                "config_is_a_list": lambda d: (d / "config.yaml").write_text(
                    "- a\n- b\n", encoding="utf-8"),
                "record_is_a_list": lambda d: (
                    d / CAMPAIGN_BINDING_FILE).write_text(
                        "[1, 2]", encoding="utf-8"),
                "corrupt_pool": lambda d: (
                    d / "factor_pool.parquet").write_bytes(b"not parquet"),
                # 下面两个先造成完整合法 run，再单点破坏 —— 这样才会
                # **越过** sidecar / 摘要那几道，真正打到未枚举的异常上。
                "valid_run_corrupt_pool": lambda d: (
                    _make_campaign_run(d),
                    (d / "factor_pool.parquet").write_bytes(b"not parquet"),
                ),
                "valid_run_pool_deleted": lambda d: (
                    _make_campaign_run(d),
                    (d / "factor_pool.parquet").unlink(),
                ),
            }
            for name, setup in cases.items():
                with self.subTest(case=name):
                    d = Path(td) / name
                    d.mkdir()
                    setup(d)
                    binding = run_protocol_binding(d, plan)   # 必须不抛
                    self.assertFalse(binding["matches"])
                    self.assertIsInstance(binding["reason"], str)

    def test_the_protocol_layer_raises_only_protocol_violation(self) -> None:
        """协议层的公开 assert_* 只抛 ProtocolViolation。

        调用方因此只需 catch 一个类型，不必跟着被读文件的格式去枚举
        yaml.YAMLError / json.JSONDecodeError / OSError —— 枚举异常类型
        与枚举入口是同一个病。函数集合**从模块推导**，新增一个
        assert_* 自动落网（这一条本轮是我自己扫出来的，codex 只点名了
        run 绑定那一处）。
        """
        import inspect
        import tempfile

        from scripts.research import fundamental_gp_campaign as camp

        def _drifted_miner_config(tmp: Path):
            """一份 seed 漂移的战役配置 —— 必须被协议层拒。"""
            from src.factor_mining.miner import load_config

            dst = tmp / "drifted.yaml"
            raw = yaml.safe_load(
                (_ROOT / _PRESET_REL).read_text(encoding="utf-8"))
            raw["gp"]["seed"] = 7
            dst.write_text(yaml.safe_dump(raw, allow_unicode=True),
                           encoding="utf-8")
            return load_config(dst)

        asserts = {
            name: fn for name, fn in vars(camp).items()
            if name.startswith("assert_") and inspect.isfunction(fn)
        }
        self.assertTrue(asserts, "没有推导出任何协议层 assert_* 函数")
        plan = camp.load_frozen_plan()
        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / "broken.yaml"
            broken.write_text("criteria: [unclosed\n", encoding="utf-8")
            empty_run = Path(td) / "run"
            empty_run.mkdir()
            # 每个 assert_* 都喂它签名吃得下的坏输入。
            feeds = {
                "assert_promotion_criteria_frozen": (broken, plan),
                "assert_promotion_window": (broken, plan),
                "assert_run_matches_protocol": (empty_run, plan),
                "assert_evaluation_endpoint": ("not-a-date", plan),
                "assert_window_discipline": ("not-a-date", plan),
                "assert_mining_config_matches_protocol": (
                    _drifted_miner_config(Path(td)), plan),
            }
            for name, fn in sorted(asserts.items()):
                with self.subTest(fn=name):
                    self.assertIn(
                        name, feeds,
                        f"{name} 是新增的协议层 assert_*，请在本钉里给它"
                        "一份坏输入 —— 否则它抛什么类型无人守。")
                    try:
                        fn(*feeds[name])
                    except camp.ProtocolViolation:
                        pass
                    except Exception as exc:  # noqa: BLE001
                        self.fail(
                            f"{name} 抛了 {type(exc).__name__} 而非 "
                            "ProtocolViolation —— 调用方无从受控处理。")
                    else:
                        self.fail(f"{name} 对坏输入没有拒绝。")

    def test_no_unguarded_deserialisation_in_the_protocol_layer(self) -> None:
        """协议层的每一次反序列化都必须经守卫读取器。

        上一轮我按"修过的那个形状"去 grep，自称扫完整类，实际漏了五处
        （``load_frozen_plan`` 自己、治理 sidecar、fundamental_binding、
        starter-check 的 run 快照、``_cmd_mine`` 的 --config）——codex
        r19 命中其中一处。grep 扫不干净，**结构**扫得干净：解析模块
        AST，除两个守卫读取器自身外不得出现裸的 ``yaml.safe_load`` /
        ``json.loads``。新增一处读取自动落网。
        """
        import ast

        from scripts.research import fundamental_gp_campaign as camp

        source = Path(camp.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        readers = {"_read_yaml_mapping", "_read_json_mapping"}
        banned = {("yaml", "safe_load"), ("json", "load"), ("json", "loads")}
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                fn = call.func
                if not isinstance(fn, ast.Attribute):
                    continue
                mod = getattr(fn.value, "id", None)
                if (mod, fn.attr) in banned and node.name not in readers:
                    offenders.append(
                        f"{node.name}:{call.lineno} {mod}.{fn.attr}")
        self.assertFalse(
            offenders,
            "协议层出现未经守卫的反序列化 —— 它抛的不是 "
            f"ProtocolViolation，调用方无从受控处理: {offenders}")
        for name in sorted(readers):
            with self.subTest(reader=name):
                self.assertTrue(hasattr(camp, name))

    def test_a_missing_plan_section_is_a_controlled_refusal(self) -> None:
        """协议缺任何一段（任意深度）都必须是 ProtocolViolation。

        此前 ``load_frozen_plan`` 手写"必需段"清单，而消费方还读
        ``metric`` / ``operators_baseline_28`` / ``operators_amended``
        —— 缺了抛裸 ``KeyError``，promote 以 traceback 收场
        （codex #446 r20 P2）。手写清单与手写入口表、手写异常元组是同一
        个病。现在用 ``_FrozenPlanMapping.__missing__``，清单已删除。
        """
        import tempfile

        from scripts.research import fundamental_gp_campaign as camp
        from src.factor_mining.miner import load_config

        base = yaml.safe_load(
            (_ROOT / _PLAN_REL).read_text(encoding="utf-8"))
        cfg = load_config(_ROOT / _PRESET_REL)
        with tempfile.TemporaryDirectory() as td:
            for key in ("metric", "operators_baseline_28",
                        "operators_amended", "universe", "terminals",
                        "windows", "fitness", "search"):
                with self.subTest(section=key):
                    drifted = dict(base)
                    drifted.pop(key)
                    f = Path(td) / "p.yaml"
                    f.write_text(
                        yaml.safe_dump(drifted, allow_unicode=True),
                        encoding="utf-8")
                    # 断言覆盖**整条链**：fail-fast 清单先拒也算，
                    # __missing__ 后拒也算 —— 两层的共同承诺是
                    # "缺段永远是 ProtocolViolation，永远不是 KeyError"。
                    with self.assertRaises(camp.ProtocolViolation):
                        plan = camp.load_frozen_plan(f)
                        camp.assert_mining_config_matches_protocol(cfg, plan)
            # __missing__ 本身：绕开 fail-fast 清单，直接钉兜底那一层。
            with self.subTest(section="__missing__ directly"):
                empty = camp._wrap_plan({"a": {"b": {}}})
                with self.assertRaises(camp.ProtocolViolation):
                    empty["nope"]
                with self.assertRaises(camp.ProtocolViolation):
                    empty["a"]["b"]["deep_nope"]
            # 嵌套段同样受保护（递归包装）。
            with self.subTest(section="adjudication.gate_F_B"):
                drifted = dict(base)
                drifted["adjudication"] = dict(drifted["adjudication"])
                drifted["adjudication"].pop("gate_F_B")
                f = Path(td) / "nested.yaml"
                f.write_text(yaml.safe_dump(drifted, allow_unicode=True),
                             encoding="utf-8")
                plan = camp.load_frozen_plan(f)
                with self.assertRaises(camp.ProtocolViolation):
                    camp.assert_promotion_criteria_frozen(
                        Path(td) / "missing.yaml", plan)

    def test_every_consumed_plan_key_exists_in_the_frozen_plan(self) -> None:
        """从 AST **推导**消费方实际读了哪些顶层键，钉住协议真的都有。

        运行时靠 ``__missing__`` 把缺段变成受控拒绝；这条钉管的是另一半
        —— 我们自己那份签署包不能缺段。键集合由代码结构推导，新增一个
        ``plan["..."]`` 消费点自动落网，不必回来改任何表。
        """
        import ast

        from scripts.research import fundamental_gp_campaign as camp

        tree = ast.parse(
            Path(camp.__file__).read_text(encoding="utf-8"))
        consumed = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "plan"
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                consumed.add(node.slice.value)
        self.assertTrue(consumed, "没有从 AST 推导出任何 plan[...] 消费点")
        missing = sorted(consumed - set(_PLAN))
        self.assertFalse(
            missing,
            f"消费方读这些顶层键，而冻结协议里没有: {missing}")

    def test_the_protocol_marker_is_read_from_the_effective_config(
            self) -> None:
        """协议标记必须从**生效后**（解析 extends）的映射读。

        ``load_config`` 走 ``load_yaml_with_inheritance``；标记若从子文件
        直接读，父文件声明的 ``protocol_id`` 就看不见 —— 继承来的合法
        标记被降级成"未受管"，继承来的不认识的标记则躲开"在场但不认识
        即拒"（codex #446 r20 P2）。
        """
        import shutil
        import tempfile

        from scripts.research import fundamental_gp_campaign as camp

        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "parent.yaml"
            shutil.copy(_ROOT / _PRESET_REL, parent)
            child = Path(td) / "child.yaml"
            child.write_text(f"extends: {parent.name}\n", encoding="utf-8")
            effective = camp._read_effective_config(child)
            self.assertEqual(camp.PROTOCOL_ID, effective.get("protocol_id"),
                             "继承来的 protocol_id 没被看见")
            # 不认识的继承标记必须被 **_cmd_mine 本身**拒 —— 只钉
            # _read_effective_config 是不够的：把 _cmd_mine 的调用换回
            # 直接读子文件，那样的钉照绿（变异实测 M-q）。这是本 PR 第
            # 四次栽在"钉了函数、没钉接线"上，所以这里穿透到子命令。
            raw = yaml.safe_load(parent.read_text(encoding="utf-8"))
            raw["protocol_id"] = "some_other_protocol"
            parent.write_text(yaml.safe_dump(raw, allow_unicode=True),
                              encoding="utf-8")
            real_mining = camp.run_mining
            real_factory = camp.build_panel_factory
            stub_out = Path(tempfile.mkdtemp())
            camp.run_mining = lambda cfg, **kw: type(  # type: ignore[assignment]
                "R", (), {"run_id": "stub", "pool": [],
                          "output_dir": stub_out})()
            camp.build_panel_factory = lambda: None  # type: ignore[assignment]
            try:
                self.assertEqual(1, camp.main(
                    ["mine", "--config", str(child)]))
            finally:
                camp.run_mining = real_mining  # type: ignore[assignment]
                camp.build_panel_factory = real_factory  # type: ignore[assignment]

    def test_protocol_validation_happens_at_subcommand_start(self) -> None:
        """协议校验必须排在子命令**开头**，不能排在昂贵工作之后。

        协议正文说的是"启动时加载并校验"。`_cmd_starter_check` 曾把它排
        在加载 run、核 PIT 绑定、重建工厂输出与量价面板之后（第 22/43
        条语句）—— 真数据上要先跑几分钟才发现 fail-closed 条件
        （codex #446 r21 P2）。同一条纪律 r12 已给 record-baseline 修过
        一次；这条钉按 AST 位置把四个子命令一起钉死，新增子命令自动落网。
        """
        import ast

        from scripts.research import fundamental_gp_campaign as camp

        protocol_calls = {
            "load_frozen_plan", "run_protocol_binding",
            "assert_run_matches_protocol", "assert_evaluation_endpoint",
            "assert_window_discipline", "assert_promotion_criteria_frozen",
            "assert_promotion_window", "_read_effective_config",
            "assert_mining_config_matches_protocol",
        }
        tree = ast.parse(Path(camp.__file__).read_text(encoding="utf-8"))
        subcommands = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name.startswith("_cmd_")
        ]
        self.assertEqual(4, len(subcommands),
                         "子命令数变了 —— 协议头声称的条数也要跟着改")
        for fn in subcommands:
            with self.subTest(subcommand=fn.name):
                # docstring 与局部 import 不算"工作"，跳过它们再计数。
                body = [
                    st for st in fn.body
                    if not isinstance(st, (ast.Import, ast.ImportFrom))
                    and not (isinstance(st, ast.Expr)
                             and isinstance(st.value, ast.Constant)
                             and isinstance(st.value.value, str))
                ]
                first = next(
                    (i for i, st in enumerate(body)
                     for c in ast.walk(st)
                     if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Name)
                     and c.func.id in protocol_calls),
                    None)
                self.assertIsNotNone(
                    first, f"{fn.name} 完全没有协议校验")
                self.assertLessEqual(
                    first, 2,
                    f"{fn.name} 的协议校验排在第 {first} 条实际语句 —— "
                    "协议说的是'启动时'，昂贵工作不得先于它。")

    def test_every_subcommand_turns_a_protocol_violation_into_exit_1(
            self) -> None:
        """协议拒绝在 CLI 上一律是**受控非零退出**，不是 traceback。

        此前只有 promote 自己 catch，mine 与 record-baseline 会把
        ProtocolViolation 抛到操作人面前（codex #446 r21 P2）。修在
        **共享的** main() 边界 —— 逐个子命令加 handler 与逐个入口加守卫、
        逐个异常类型加枚举是同一个病。子命令集合从 argparse 推导。
        """
        import argparse
        import tempfile

        from scripts.research import fundamental_gp_campaign as camp

        holder: dict[str, argparse.ArgumentParser] = {}
        real = argparse.ArgumentParser.parse_args

        def _capture(self_parser, *a, **kw):  # noqa: ANN001
            holder["p"] = self_parser
            raise SystemExit(0)

        argparse.ArgumentParser.parse_args = _capture  # type: ignore[method-assign]
        try:
            with self.assertRaises(SystemExit):
                camp._parse_args(["mine", "--config", "x"])
        finally:
            argparse.ArgumentParser.parse_args = real  # type: ignore[method-assign]
        names = sorted(
            next(a for a in holder["p"]._actions
                 if isinstance(a, argparse._SubParsersAction)).choices)
        self.assertTrue(names)

        # 每个子命令喂一份必然违反协议的输入，要求 rc == 1（不是抛）。
        with tempfile.TemporaryDirectory() as td:
            empty_run = Path(td) / "empty"
            empty_run.mkdir()
            bad_cfg = Path(td) / "bad.yaml"
            raw = yaml.safe_load(
                (_ROOT / _PRESET_REL).read_text(encoding="utf-8"))
            raw["protocol_id"] = "not_this_protocol"
            bad_cfg.write_text(yaml.safe_dump(raw, allow_unicode=True),
                               encoding="utf-8")
            argv_for = {
                "mine": ["mine", "--config", str(bad_cfg)],
                "starter-check": ["starter-check", "--run", str(empty_run)],
                "record-baseline": [
                    "record-baseline", "--run", str(empty_run),
                    "--end-date", "1999-01-01",
                    "--out", str(Path(td) / "b.json")],
                "promote": ["promote", "--run", str(empty_run),
                            "--to", "vX"],
            }
            for name in names:
                with self.subTest(subcommand=name):
                    self.assertIn(
                        name, argv_for,
                        f"{name} 是新增子命令，请在本钉里给它一份必然"
                        "违反协议的输入 —— 否则它的失败契约无人守。")
                    try:
                        rc = camp.main(argv_for[name])
                    except camp.ProtocolViolation:
                        self.fail(f"{name}: ProtocolViolation 冲出了 "
                                  "main()，操作人看到的是 traceback。")
                    self.assertNotEqual(
                        0, rc, f"{name}: 违反协议却返回 0")

    def test_a_malformed_plan_section_is_a_controlled_refusal(self) -> None:
        """段的**类型**不对也必须是 ProtocolViolation。

        ``__missing__`` 覆盖的是"某段缺席"，覆盖不了"某段类型不对"：
        语法合法的 ``windows: []`` 能通过存在性检查，随后
        ``plan["windows"]["is_start"]`` 抛 TypeError 冲出 CLI 边界
        （codex #446 r22 P2）。实测六种形状分别抛 TypeError 与
        AttributeError —— 所以修在**层的边界**而不是给每段写类型表。
        """
        import tempfile

        from scripts.research import fundamental_gp_campaign as camp
        from src.factor_mining.miner import load_config

        base = yaml.safe_load(
            (_ROOT / _PLAN_REL).read_text(encoding="utf-8"))
        cfg = load_config(_ROOT / _PRESET_REL)
        shapes = ([], 5, "a string", None, [1, 2], {})
        with tempfile.TemporaryDirectory() as td:
            promo = Path(td) / "promo.yaml"
            promo.write_text(
                yaml.safe_dump({"criteria": {"min_oos_ir": 0.3}}),
                encoding="utf-8")
            # mine 侧消费的段
            for key in ("windows", "metric", "terminals", "fitness",
                        "search", "universe"):
                for shape in shapes:
                    with self.subTest(section=key, shape=type(shape).__name__):
                        drifted = dict(base)
                        drifted[key] = shape
                        f = Path(td) / "p.yaml"
                        f.write_text(
                            yaml.safe_dump(drifted, allow_unicode=True),
                            encoding="utf-8")
                        with self.assertRaises(camp.ProtocolViolation):
                            plan = camp.load_frozen_plan(f)
                            camp.assert_mining_config_matches_protocol(
                                cfg, plan)
            # 晋升侧消费的段
            for shape in shapes:
                with self.subTest(section="adjudication",
                                  shape=type(shape).__name__):
                    drifted = dict(base)
                    drifted["adjudication"] = shape
                    f = Path(td) / "a.yaml"
                    f.write_text(
                        yaml.safe_dump(drifted, allow_unicode=True),
                        encoding="utf-8")
                    with self.assertRaises(camp.ProtocolViolation):
                        plan = camp.load_frozen_plan(f)
                        camp.assert_promotion_criteria_frozen(promo, plan)

    def test_every_protocol_layer_function_carries_the_boundary(self) -> None:
        """协议层的每个对外函数都必须带 ``@protocol_boundary``。

        函数集合**从模块推导**（``assert_*`` / ``_read_*`` / 协议判定），
        不手写表 —— 新增一个协议层函数若忘了装饰器，这条钉直接红。
        这是"层的契约"能成立的机器保证：调用方只 catch
        ProtocolViolation，前提是这一层真的只抛它。
        """
        import inspect

        from scripts.research import fundamental_gp_campaign as camp

        layer = {
            name for name, fn in vars(camp).items()
            if inspect.isfunction(fn)
            and (name.startswith("assert_") or name.startswith("_read_")
                 or name in {"load_frozen_plan", "run_protocol_binding",
                             "_verify_pool_truncation"})
        }
        self.assertTrue(layer, "没有推导出任何协议层函数")
        for name in sorted(layer):
            with self.subTest(fn=name):
                fn = getattr(camp, name)
                # functools.wraps 保留 __wrapped__，据此判定已被包裹。
                self.assertTrue(
                    hasattr(fn, "__wrapped__"),
                    f"{name} 没有 @protocol_boundary —— 它抛的异常类型"
                    "不受约束，调用方无从受控处理。")
                # 且包裹的确实是我们的边界（而非别的装饰器）。
                src = inspect.getsource(camp.protocol_boundary)
                self.assertIn("ProtocolViolation", src)

    def test_the_boundary_does_not_enumerate_exception_types(self) -> None:
        """边界装饰器必须捕 ``Exception``，不能列一张类型元组。

        第一版列了 TypeError / AttributeError / IndexError / KeyError /
        ValueError 五种，而 ``holdout_year: .inf`` 让 ``int()`` 抛
        ``OverflowError`` 直接漏出去（codex #446 r23 P2）。**在边界上
        枚举异常类型，与枚举入口、枚举必需段、枚举每段类型是同一个病**
        —— 我在 run_protocol_binding 上已按契约做成全函数，却在这个装饰
        器上又列了一次表。
        """
        import ast
        import inspect

        from scripts.research import fundamental_gp_campaign as camp

        tree = ast.parse(inspect.getsource(camp.protocol_boundary))
        handlers = [h for h in ast.walk(tree)
                    if isinstance(h, ast.ExceptHandler)]
        self.assertTrue(handlers)
        # 允许先 re-raise ProtocolViolation，其余必须是裸 Exception。
        kinds = []
        for h in handlers:
            if h.type is None:
                kinds.append("bare")
            elif isinstance(h.type, ast.Name):
                kinds.append(h.type.id)
            elif isinstance(h.type, ast.Tuple):
                kinds.append("TUPLE")
        self.assertNotIn(
            "TUPLE", kinds,
            "边界在枚举异常类型 —— 漏一个就 traceback（实测漏过 "
            "OverflowError）。契约说只抛 ProtocolViolation，就该捕 "
            "Exception。")
        self.assertIn("Exception", kinds)

    def test_extreme_scalars_in_the_plan_are_controlled_refusals(
            self) -> None:
        """协议里的极端标量（inf / nan / 超大整数）也必须是受控拒绝。"""
        import tempfile

        from scripts.research import fundamental_gp_campaign as camp

        base = yaml.safe_load(
            (_ROOT / _PLAN_REL).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            for field, value in (
                ("holdout_year", float("inf")),
                ("holdout_year", float("nan")),
                ("holdout_year", 99999999999999999999),
                ("forbidden_from", float("inf")),
            ):
                with self.subTest(field=field, value=repr(value)):
                    drifted = dict(base)
                    drifted["windows"] = dict(drifted["windows"])
                    drifted["windows"][field] = value
                    f = Path(td) / "p.yaml"
                    f.write_text(yaml.safe_dump(drifted, allow_unicode=True),
                                 encoding="utf-8")
                    with self.assertRaises(camp.ProtocolViolation):
                        plan = camp.load_frozen_plan(f)
                        camp.assert_window_discipline("2024-12-31", plan)

    def test_no_broken_input_escapes_the_cli(self) -> None:
        """四个子命令 × 多种坏输入：一律受控非零退出，无一 traceback。

        这条是把我这轮的**穷举扫**固化下来。它抓到过三处 codex 没点名的
        逃逸：`_cmd_mine` 的第 0 条语句是 `load_config()`，排在协议校验
        之前且不属协议层，会抛 ParserError / YamlInheritanceError /
        FileNotFoundError。修法是把战役配置加载纳入协议层
        （`load_mining_config`），而不是放宽 `main()` 的 handler ——
        配置读不出来 = 无从证明它符合协议，本就属于这一层。

        子命令集合从 argparse 推导：新增一个而没在矩阵里登记会直接失败。
        """
        import argparse
        import tempfile

        from scripts.research import fundamental_gp_campaign as camp

        holder: dict[str, argparse.ArgumentParser] = {}
        real = argparse.ArgumentParser.parse_args

        def _capture(self_parser, *a, **kw):  # noqa: ANN001
            holder["p"] = self_parser
            raise SystemExit(0)

        argparse.ArgumentParser.parse_args = _capture  # type: ignore[method-assign]
        try:
            with self.assertRaises(SystemExit):
                camp._parse_args(["mine", "--config", "x"])
        finally:
            argparse.ArgumentParser.parse_args = real  # type: ignore[method-assign]
        names = sorted(
            next(a for a in holder["p"]._actions
                 if isinstance(a, argparse._SubParsersAction)).choices)

        with tempfile.TemporaryDirectory() as td:
            T = Path(td)
            empty = T / "empty"
            empty.mkdir()
            bad_yaml_run = T / "badyaml"
            bad_yaml_run.mkdir()
            (bad_yaml_run / "config.yaml").write_text(
                "a: [unclosed\n", encoding="utf-8")
            not_a_dir = T / "nofile.txt"
            not_a_dir.write_text("x", encoding="utf-8")
            bad_cfg = T / "bad.yaml"
            bad_cfg.write_text("gp: [unclosed\n", encoding="utf-8")
            list_cfg = T / "list.yaml"
            list_cfg.write_text("- a\n", encoding="utf-8")
            promo_bad = T / "promo.yaml"
            promo_bad.write_text("criteria: [unclosed\n", encoding="utf-8")

            matrix = {
                "mine": [
                    ["mine", "--config", str(bad_cfg)],
                    ["mine", "--config", str(list_cfg)],
                    ["mine", "--config", str(T / "absent.yaml")],
                ],
                "starter-check": [
                    ["starter-check", "--run", str(empty)],
                    ["starter-check", "--run", str(bad_yaml_run)],
                    ["starter-check", "--run", str(not_a_dir)],
                ],
                "record-baseline": [
                    ["record-baseline", "--run", str(empty),
                     "--end-date", "not-a-date", "--out", str(T / "a.json")],
                    ["record-baseline", "--run", str(not_a_dir),
                     "--end-date", "2024-12-31", "--out", str(T / "b.json")],
                ],
                "promote": [
                    ["promote", "--run", str(empty), "--to", "vX"],
                    ["promote", "--run", str(empty), "--to", "vX",
                     "--config", str(promo_bad)],
                ],
            }
            for name in names:
                self.assertIn(
                    name, matrix,
                    f"{name} 是新增子命令，请在本矩阵里给它坏输入 —— "
                    "否则它的 CLI 失败契约无人守。")
                for argv in matrix[name]:
                    with self.subTest(subcommand=name, argv=argv[1:3]):
                        try:
                            rc = camp.main(argv)
                        except SystemExit as exc:
                            rc = exc.code
                        except Exception as exc:  # noqa: BLE001
                            self.fail(
                                f"{name}: {type(exc).__name__} 冲出了 "
                                f"main() —— 操作人看到的是 traceback。")
                        self.assertNotEqual(0, rc)

    def test_the_plan_is_loaded_before_dispatch(self) -> None:
        """协议在 ``main`` 分派**之前**加载，子命令不再各自加载。

        codex 连着三轮打的都是"子命令内部的次序"：藏在 `if governed:`
        里（r24）、排在 protocol_id 三态校验之后（r25）、排在两处配置
        读取之后（r26）。每次我往 AST 钉上加一层判定，每一版都有新盲区
        —— 判得出嵌套判不出 `raise`，判得出 `raise` 判不出"会抛的调用"。

        根因是**用静态检查逼近动态属性**（"哪条语句先执行"）。把加载移到
        唯一的分派点之前，这个属性就变成结构上成立的：判据只剩"在
        `main` 里，`load_frozen_plan()` 出现在 `args.func(...)` 之前"，
        没有盲区可言。
        """
        import ast

        from scripts.research import fundamental_gp_campaign as camp

        tree = ast.parse(Path(camp.__file__).read_text(encoding="utf-8"))
        main_fn = next(n for n in ast.walk(tree)
                       if isinstance(n, ast.FunctionDef) and n.name == "main")
        load_line = dispatch_line = None
        for node in ast.walk(main_fn):
            if not isinstance(node, ast.Call):
                continue
            if (isinstance(node.func, ast.Name)
                    and node.func.id == "load_frozen_plan"
                    and load_line is None):
                load_line = node.lineno
            if (isinstance(node.func, ast.Attribute)
                    and node.func.attr == "func" and dispatch_line is None):
                dispatch_line = node.lineno
        self.assertIsNotNone(load_line, "main 没有加载协议")
        self.assertIsNotNone(dispatch_line, "main 没有分派到子命令")
        self.assertLess(
            load_line, dispatch_line,
            "main 在加载协议之前就分派了 —— 子命令内部的次序问题会重新"
            "出现，而那是静态检查逼不出来的。")
        # 子命令不得再各自加载（否则次序问题从后门回来）。
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name.startswith("_cmd_")]:
            with self.subTest(subcommand=fn.name):
                calls = [c for c in ast.walk(fn)
                         if isinstance(c, ast.Call)
                         and isinstance(c.func, ast.Name)
                         and c.func.id == "load_frozen_plan"]
                self.assertFalse(
                    calls,
                    f"{fn.name} 自己调用了 load_frozen_plan —— 协议应由"
                    "main 在分派前加载，子命令从 args.plan 取。")

    def test_a_broken_config_still_loads_the_plan_first(self) -> None:
        """行为层证人：配置怎么坏，协议都已经先加载并校验过。

        三条组合路径（配置缺失 / 配置畸形 / 外来 protocol_id）× 一份
        **已揭盲**的协议，都必须以"协议已揭盲"失败 —— 若失败信息指向
        配置，说明协议根本没被加载（codex #446 r26 P2）。
        """
        import io
        import shutil
        import tempfile
        from contextlib import redirect_stderr

        from scripts.research import fundamental_gp_campaign as camp

        real_plan = camp.FROZEN_PLAN_PATH
        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / "plan.yaml"
            raw = yaml.safe_load(
                (_ROOT / _PLAN_REL).read_text(encoding="utf-8"))
            raw["holdout_unblinded"] = True
            broken.write_text(yaml.safe_dump(raw, allow_unicode=True),
                              encoding="utf-8")
            malformed = Path(td) / "bad.yaml"
            malformed.write_text("gp: [unclosed" + chr(10),
                                 encoding="utf-8")
            foreign = Path(td) / "foreign.yaml"
            shutil.copy(_ROOT / _PRESET_REL, foreign)
            fraw = yaml.safe_load(foreign.read_text(encoding="utf-8"))
            fraw["protocol_id"] = "some_foreign_protocol"
            foreign.write_text(yaml.safe_dump(fraw, allow_unicode=True),
                               encoding="utf-8")
            cases = {
                "config_absent": Path(td) / "absent.yaml",
                "config_malformed": malformed,
                "foreign_protocol_id": foreign,
            }
            camp.FROZEN_PLAN_PATH = broken  # type: ignore[assignment]
            try:
                for label, cfg in cases.items():
                    with self.subTest(case=label):
                        err = io.StringIO()
                        with redirect_stderr(err):
                            rc = camp.main(["mine", "--config", str(cfg)])
                        self.assertEqual(1, rc)
                        self.assertIn(
                            "holdout_unblinded", err.getvalue(),
                            f"{label}: 失败指向配置而非已揭盲的协议 —— "
                            "协议在这条路径下没被加载。")
            finally:
                camp.FROZEN_PLAN_PATH = real_plan  # type: ignore[assignment]

    def test_an_ungoverned_batch_still_refuses_a_broken_plan(self) -> None:
        """行为层证人：未受管的批次遇到坏协议同样要拒。

        只钉 AST 位置不够 —— 结构对了、语义可能仍不对。这里真的跑
        `mine`（run_mining 打桩），喂一份**没有 protocol_id** 的配置 +
        一份已揭盲的协议，要求受控非零退出。
        """
        import shutil
        import tempfile

        from scripts.research import fundamental_gp_campaign as camp

        real_plan = camp.FROZEN_PLAN_PATH
        real_mining = camp.run_mining
        real_factory = camp.build_panel_factory
        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / "plan.yaml"
            raw = yaml.safe_load(
                (_ROOT / _PLAN_REL).read_text(encoding="utf-8"))
            raw["holdout_unblinded"] = True          # 已揭盲
            broken.write_text(yaml.safe_dump(raw, allow_unicode=True),
                              encoding="utf-8")
            cfg = Path(td) / "link_check.yaml"
            shutil.copy(
                _ROOT / "config/factor_mining/fundamental_link_check.yaml",
                cfg)
            stub_out = Path(td) / "out"
            stub_out.mkdir()
            camp.FROZEN_PLAN_PATH = broken  # type: ignore[assignment]
            camp.run_mining = lambda c, **kw: type(  # type: ignore[assignment]
                "R", (), {"run_id": "stub", "pool": [],
                          "output_dir": stub_out})()
            camp.build_panel_factory = lambda: None  # type: ignore[assignment]
            try:
                rc = camp.main(["mine", "--config", str(cfg)])
            finally:
                camp.FROZEN_PLAN_PATH = real_plan  # type: ignore[assignment]
                camp.run_mining = real_mining  # type: ignore[assignment]
                camp.build_panel_factory = real_factory  # type: ignore[assignment]
            self.assertEqual(
                1, rc,
                "未受管的批次在协议已揭盲时照跑了 —— 协议正文声称的"
                "'任一不符即拒'不成立。")

    def test_a_foreign_protocol_id_still_loads_the_plan_first(self) -> None:
        """"外来 protocol_id + 坏协议"这条组合路径也必须先加载协议。

        `_cmd_mine` 的三态校验曾排在加载之前：一个 typo / 外来的标记会
        在加载之前 raise，于是这条路径下协议从未被加载 —— 而结构钉只
        判"不被 if 包裹"，判不出"前面有提前退出"（codex #446 r25 P2）。

        行为层证人：喂一份外来 ID 的配置 + 一份**已揭盲**的协议，要求
        失败信息指向**协议已揭盲**（说明协议先被加载并校验了），而不是
        指向 protocol_id。
        """
        import io
        import shutil
        import tempfile
        from contextlib import redirect_stderr

        from scripts.research import fundamental_gp_campaign as camp

        real_plan = camp.FROZEN_PLAN_PATH
        real_mining = camp.run_mining
        real_factory = camp.build_panel_factory
        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / "plan.yaml"
            raw = yaml.safe_load(
                (_ROOT / _PLAN_REL).read_text(encoding="utf-8"))
            raw["holdout_unblinded"] = True
            broken.write_text(yaml.safe_dump(raw, allow_unicode=True),
                              encoding="utf-8")
            cfg = Path(td) / "foreign.yaml"
            shutil.copy(_ROOT / _PRESET_REL, cfg)
            craw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
            craw["protocol_id"] = "some_foreign_protocol"
            cfg.write_text(yaml.safe_dump(craw, allow_unicode=True),
                           encoding="utf-8")
            stub_out = Path(td) / "out"
            stub_out.mkdir()
            camp.FROZEN_PLAN_PATH = broken  # type: ignore[assignment]
            camp.run_mining = lambda c, **kw: type(  # type: ignore[assignment]
                "R", (), {"run_id": "stub", "pool": [],
                          "output_dir": stub_out})()
            camp.build_panel_factory = lambda: None  # type: ignore[assignment]
            err = io.StringIO()
            try:
                with redirect_stderr(err):
                    rc = camp.main(["mine", "--config", str(cfg)])
            finally:
                camp.FROZEN_PLAN_PATH = real_plan  # type: ignore[assignment]
                camp.run_mining = real_mining  # type: ignore[assignment]
                camp.build_panel_factory = real_factory  # type: ignore[assignment]
            self.assertEqual(1, rc)
            self.assertIn(
                "holdout_unblinded", err.getvalue(),
                "失败信息指向 protocol_id 而非已揭盲的协议 —— 说明协议"
                "在 protocol_id 校验之前根本没被加载。")

if __name__ == "__main__":
    unittest.main()
