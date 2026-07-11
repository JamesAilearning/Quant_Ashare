# Gate-1 · 财报 PIT 可行性只读勘察 — Claude Code 任务 brief

> **Status:** 待用户点火后交 Claude Code 执行。
> **性质:只读勘察,STOP-gated。不实现任何特征 / 数据视图 / OpenSpec。**
> 配套:`quality_profitability_charter.md`(候选定义)。

## 0. 给 Claude Code 的执行约束
- 这是 **Step-0 只读勘察**(对标此前 CSI800 扩池勘察)。不写生产码、不改 canonical、不建特征。
- 产出 = 一份可行性 memo,写到 `docs/prereg/quality_profitability_gate1_pit_preflight.md`,然后 **STOP 等用户复核**。
- 遵循 `AGENTS.md`。勘察若需拉数,放 `scripts/` 下**一次性探针**并标注,不进生产路径。
- 复用现有 `src/data/pit/index_membership.py` 的 CSI300 PIT 成分;先读 `docs/pit/pit_universe_design.md` §4.5——财报 PIT = **Phase-E.2,未建**,别当已交付。

## 1. 目标
确认 tushare 能否为盈利能力因子家族(charter 的 C1/C2/C3)提供 **PIT 正确** 的财报数据。逐字段判 `feasible` / `caveat` / `not-feasible`。

## 2. 必查项(逐条给证据)

### 2.1 字段 → 端点映射
对 charter 里 C1/C2/C3 所需**每个**字段,确认由哪个 tushare 端点提供、字段名、单位:
- `income`:revenue / total_revenue、oper_cost、sell_exp、admin_exp、rd_exp、int_exp / fin_exp…
- `balancesheet`:total_assets、total_hldr_eqy_inc_min_int、total_hldr_eqy_exc_min_int、accounts_receiv、inventories、prepayment、accounts_pay、adv_receipts…
- `cashflow`:n_cashflow_act(供 cash-based / 应计交叉核验)
- `fina_indicator`:roe、grossprofit_margin、q_op…(**仅作交叉校验**,不作主输入)

任一 charter **必需**字段缺失 → 对应候选标 `not-feasible`,记录,**不替代**。

### 2.2 公告日语义(PIT 核心)
- 每个端点是否同时返回 `end_date`(报告期)、`ann_date`、`f_ann_date`(实际公告日)?
- 明确"报告期"vs"信息可得日";PIT 生效日应取 `f_ann_date`(或 `ann_date`)。
- 生效时点:公告多在盘后 → **默认下一可交易日生效**;确认有无盘中时间戳可支持更早(通常无 → 保守默认)。

### 2.3 修订 / 版本语义
- tushare 是否暴露原始 vs 修订披露(如 `update_flag`)?能否取"当时首次披露值",还是只有最新修订值?
- 若只能取最新修订值 → 记为 **PIT 限制**(会把今天的修订回填历史),明确写入 memo 作为风险。

### 2.4 覆盖与缺失(在 CSI300 PIT 成分上)
- 用现有 PIT 成分,统计每字段**按年**覆盖率、缺失模式、重复披露、stale 值、退市名。
- 特别关注 C3 需两期 → 更易缺失。
- **重要区分:** 财报按"截至该日最新已公告报表"持续生效,是有意的 `as-of` carry-forward;它**不是** `fillna`。无效/缺失/未公告记录必须**保持缺失并显式报告**,绝不后向填充、全样本填充或静默删股。

### 2.5 人工核验案例(≥3–5 个 firm-quarter)
- 证明:公告日前不可见、公告后才生效、修订后才替换旧值。
- 对一个公开来源(交易所公告 / 中证)交叉核对公告日。

### 2.6 确认 daily_basic 不是替代
- 明确 `daily_basic` 只有日度估值 / 换手 / 市值快照,**无报表级字段**,不能替代财报 PIT。

## 3. memo 产出格式(`..._gate1_pit_preflight.md`)
1. 逐字段可行性表(feasible / caveat / not-feasible + 证据)。
2. 公告日语义结论 + 生效规则。
3. 修订 / 版本结论(能否取原始披露)。
4. 覆盖率表(按年 × 字段)。
5. 人工案例记录。
6. **逐候选结论:** C1 / C2 / C3 各自 feasible / not-feasible + 理由。
7. 明确"未通过即停",不提任何实现方案。

## 4. STOP
memo 写完即停,等用户复核。**通过后**才谈 Gate-2(仅建财报 PIT 契约 + 数据桥的独立小 OpenSpec,不含因子 / 模型 / 生产改动)。不得顺手实现数据视图或因子。
