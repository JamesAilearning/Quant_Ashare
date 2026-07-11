# 质量因子研究 · 盈利能力家族 · Gate-0 候选章程(H8-Q1)

> **Status:** Gate-0 理论已签字(2026-07-10,用户 approve H8-Q1)。本章程是 research-only、confirmatory 预注册的**前置**。任何回测在 Gate-3 预注册冻结前**不得进行**。
> **决策人:** 用户。**执行:** Claude Code。**架构/评审:** Cowork Claude。

## 0. 定位与边界
- 本文只固定「测什么因子、为什么、如何证伪、如何计多重检验」,**不含任何回测结果、不授权实现**。
- 家族 = 盈利能力(profitability);多重检验批次 `multiplicity_family_id: quality_profitability_v1`;注册候选 ≤3,视为**同一 FWER 批次**。
- 所有 tushare 字段映射均为**提议值,待 Gate-1 PIT 勘察坐实**。凡真实 schema 不支持者,该候选标 `not-feasible`,**不得用近似字段静默替代**(设计稿 §4.1)。

## 1. 投资者命题(investor thesis)
真实、以现金为基础、且可持续的经营盈利能力,在 A 股(散户主导、偏题材与账面盈利)可能被系统性低估;这类"闷声赚真钱"的公司,在预先固定的较长持有期内,应有更好的风险调整横截面预期收益,或对现有 Alpha158 特征带来**可检验的增量**。

## 2. 注册候选(≤3,同一多重检验批次)

### C1 — GPA(gross profits to assets)
- **公式:** `GPA = (营业收入 − 营业成本) / 期末总资产`
- **tushare 提议字段(待坐实):** `income.revenue`(或 `total_revenue`)、`income.oper_cost`、`balancesheet.total_assets`
- **方向:** 越高越好 (+)
- **更新频率:** 按报告**公告日** as-of carry-forward(非报告期末)
- **机制:** 毛利最少受会计政策污染,是经济生产率最干净的度量(Novy-Marx 2013)。A 股:散户过度聚焦账面利润/题材,真实生产率被低估。

### C2 — PROF(operating profitability,R&D 加回)
- **公式:** `PROF = (营收 − 营业成本 − (销售费用 + 管理费用 − 研发费用) − 利息费用) / (归母权益 + 少数股东权益)`
  即:分子中研发费用**加回**(视为投资而非费用);分母 = 含少数股东权益的总权益。
- **tushare 提议字段(待坐实):** `revenue`、`oper_cost`、`sell_exp`、`admin_exp`、`rd_exp`、`int_exp`(或 `fin_exp`)、`balancesheet.total_hldr_eqy_inc_min_int`
  ⚠ `rd_exp` / `int_exp` 是否单列须 Gate-1 确认;不可得则本候选标 `not-feasible`。
- **方向:** (+)
- **机制:** 不惩罚 R&D 的经营盈利,捕捉含未来投入的真实经营效率。Profitability Retrospective(2025)实证该定义可**吸收** F-score / O-score / ROE / 低波等 12 个质量指标。

### C3 — Cash-based OP(现金基础经营盈利)
- **公式(精神):** `cash-based OP = [(营收 − 营业成本 − 销售管理费用 + 研发) − Δ应收 − Δ存货 − Δ预付 + Δ应付 + Δ预收 + Δ其他经营性应计] / 期末总资产`
  精确应计项**待 Gate-1 按 tushare 实际可得项固定**。
- **tushare 提议字段(待坐实):** 上述 income 项 + `balancesheet` 营运资本项(`accounts_receiv`、`inventories`、`prepayment`、`accounts_pay`、`adv_receipts`…);需**两期**→ PIT 要求更高。
- **方向:** (+)
- **机制:** 剔除应计后的"现金真盈利"更持久、更难粉饰(Ball-Gerakos-Linnainmaa-Nikolaev 2016);**直接检验 H8-Q1 的"利润真实"。**

## 3. 预注册确认性诊断(非可调旋钮)
**应计质量 / cash-vs-accrual:** 比较 C2(含应计的 OP)与 C3(现金基础 OP)。若"利润真实"命题成立,C3 应 ≥ C2 的预测力、且高应计组更弱。**看到结果后不得据此临时调权、加减项或合成"赢家"版本。**

## 4. A 股专属约束
- **规模分档内排序:** 2019 注册制前小盘壳价值使盈利/估值暴露混淆(Liu-Stambaugh-Yuan 2019);质量排序须在 size decile 内做,否则可能是伪装的规模押注。
- **无 PIT 行业/国企分类** → 只报暴露、标"未中性化",**不得伪称已控制混杂**(设计稿 Gate-4A)。
- **先验校准(诚实预期):** A 股质量整体偏弱(Jansen-Swinkels-Zhou 2021);多重检验后 t 门槛 ≈ 2.85(Hou-Qiao-Zhang 2024)。**首轮很可能阴性——且这是有效结果,不是失败。**

## 5. 禁止变体(prohibited variants)
看到任何回测后,**不得**:改公式/加减项、改公告滞后或生效时点、改窗口/持有期、挑最优持有期作主结果、把候选合成组合、用 OOS 结果反修计划。任何此类改动 = **新计划、新未触碰验证窗**。

## 6. 多重检验登记
`multiplicity_family_id: quality_profitability_v1`;有效试验数 = 完整 ledger 记录的**全部**候选 + 变体 + 失败试验;DSR / PBO 的试验数以此为准。**无完整记录不得声称已校正选择偏差。**

## 7. falsifier(任一即为有效阴性)
- 数据无法证明公告日 / 首披 / 修订版本的 PIT;
- 覆盖不足、符号不稳、或暴露主要来自规模 / 行业 / 壳价值;
- 冻结后 OOS 无增量,或扣成本后撑不起低换手长持形态;
- 整批未过预注册多重检验门。

## 8. 引用
Novy-Marx (2013) GPA · Ball et al. (2016) cash-based OP · Medhat & Novy-Marx (2025) Profitability Retrospective · Harvey-Liu (2021) Lucky Factors · Feng-Giglio-Xiu (2020) Taming the Factor Zoo · Bailey & López de Prado (2014) DSR/PBO · Jansen-Swinkels-Zhou (2021) · Hou-Qiao-Zhang (2024) · Liu-Stambaugh-Yuan (2019) · Leippold-Wang-Zhou (2022, JFE)。

## 9. 下一步(STOP-gated)
Gate-1 财报 PIT 只读勘察(见 `quality_profitability_gate1_pit_scout_brief.md`)→ 出可行性 memo → **用户复核** → 才谈 Gate-2 OpenSpec。Gate-1 坐实字段后,任何 `not-feasible` 候选就地标注,不替代。
