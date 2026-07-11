# Gate-1 · 财报 PIT 可行性只读勘察 — preflight memo（H8-Q1）

> **Status:** 勘察完成，待用户复核。**只读证据，无实现、无 OpenSpec、无因子/模型改动。**
> **性质:** Step-0 可行性勘察，STOP-gated（brief `quality_profitability_gate1_pit_scout_brief.md`）。
> **证据来源:** 一次性探针 `scripts/oneshot_gate1_pit_scout.py`（NON-PRODUCTION，可弃）+ 补充探针，实测 tushare `income/balancesheet/cashflow/fina_indicator`（2026-07-10 tushare 恢复后跑通）。
> **覆盖率样本:** CSI300-ever 全量 n=627（含 21 退市名），按年 2018–2025（① 全量重跑，含幸存者拆分；前期 n=60 抽样一致，差异 ≤3pp）。
> **一句话结论:** C1/C2/C3 **charter 必需字段全部存在**，PIT 语义齐备（`f_ann_date`）→ **三候选均 feasible（排除金融股）**，各带明确 caveat；无 not-feasible。**这不授权任何回测**——Gate-3 冻结前不得进行。

---

## 1. 逐字段可行性表（feasible / caveat / not-feasible + 证据）

| 端点.字段 | 候选 | 存在? | 覆盖率(n=60, 2019-25 典型) | 判定 | 证据/说明 |
|---|---|---|---|---|---|
| income.revenue | C1/C2/C3 | ✅ | **100%** | feasible | 主输入 |
| income.total_revenue | (备) | ✅ | 100% | feasible | revenue 备选 |
| income.oper_cost | C1/C2/C3 | ✅ | **~89%** | **caveat** | **缺失=金融股**(实测 pufa/华夏/民生/中信/招行 全空,industry=银行/证券)→ 排除金融股 |
| income.admin_exp | C2/C3 | ✅ | **100%** | feasible | |
| income.sell_exp | C2/C3 | ✅ | ~84% | caveat | 金融股缺 |
| income.rd_exp | C2/C3 | ✅ | **2%(2017)/53%(2018)/80-86%(2019+)** | **caveat** | 研发费用单列**存在**;2019 前稀疏(准则),2018 仅 53%→早窗弱 |
| income.int_exp | C2 | ✅ | **16-22%** | **caveat** | 极稀疏 → **改用 `fin_exp`**(charter 允许"int_exp 或 fin_exp") |
| income.fin_exp | C2(利息项) | ✅ | ~89% | feasible | C2 利息项的实际可用列 |
| balancesheet.total_assets | C1/C3 | ✅ | **100%** | feasible | 分母 |
| balancesheet.total_hldr_eqy_inc_min_int | C2 | ✅ | **100%** | feasible | C2 分母(含少数股东权益) |
| balancesheet.total_hldr_eqy_exc_min_int | (备) | ✅ | 100% | feasible | |
| balancesheet.accounts_receiv | C3 | ✅ | ~88% | caveat | 金融股缺;C3 需两期 |
| balancesheet.inventories | C3 | ✅ | ~89% | caveat | 同上 |
| balancesheet.prepayment | C3 | ✅ | ~89% | caveat | 同上 |
| balancesheet.accounts_pay | C3 | ✅ | ~88% | caveat | 同上 |
| balancesheet.adv_receipts | C3 | ✅ | **82%→35%(2020断裂)** | **caveat** | 2020 新收入准则:预收账款→合同负债 |
| balancesheet.contract_liab | C3(补) | ✅ | **0%→88%(2020起)** | feasible | **补 adv_receipts 断裂:二者 coalesce 全年段 82-92%** |
| cashflow.n_cashflow_act | C3(交叉) | ✅ | (未逐年测,单只在) | feasible | 现金/应计交叉核验 |
| fina_indicator.roe / grossprofit_margin | 交叉校验 | ✅ | 在 | caveat | **仅 end_date/ann_date,无 f_ann_date** → PIT 更弱,只作交叉校验 |
| fina_indicator.q_op | 交叉校验 | ❌ | — | not-feasible(该字段) | 该端点无此列;不影响候选(非必需) |

**无任一候选因 charter 必需字段缺失而 not-feasible。** 所有 caveat 均为**覆盖/口径**问题,按 charter §0「不静默替代」处理:缺失即保持缺失、显式报告。

## 2. 公告日语义结论 + 生效规则

- `income/balancesheet/cashflow` **均返回 `end_date`(报告期)、`ann_date`、`f_ann_date`(实际公告日)、`report_type`、`end_type`**。
- 实测公告滞后(2.5 案例):`end_date=20211231` → `ann_date` 茅台 20220331 / 格力 20220430 / 招行 20220319,**滞后约 3-4 个月**;`f_ann_date == ann_date`(首次公告即实际公告)。
- **PIT 生效规则(建议):** 生效日 = `f_ann_date`(缺则 `ann_date`);公告多在盘后 → **默认 `f_ann_date` 之后第一个可交易日生效**(无盘中时间戳,保守默认)。财报按"截至该日最新已公告报表"as-of carry-forward,**非 fillna**;未公告期保持缺失。

## 3. 修订 / 版本结论

- `update_flag` **存在**(income/balancesheet/cashflow;值 0/1);同一 `end_date` 常有 0/1 双行。
- **但真实重述无独立公告日:** 三只蓝筹(茅台/格力/平安)**0 个 end_date 带多个不同 `ann_date`** → 0/1 双行**共享同一 `ann_date`**、值相同。
- **PIT 限制(如实记录):** tushare 能区分"原始 vs 调整"(update_flag),但**不为重述打独立的重述公告日**。若某报告期日后被真实重述,只能拿到挂在**原始 ann_date** 下的(可能已被回填的)值。
- **建议:** 严格 PIT 取 `update_flag` 原始披露值、键到 `f_ann_date`;并把"重述无法定到重述日"列为**已知 PIT 风险**(charter §7 falsifier 之一)。

## 4. 覆盖率表（全量 CSI300-ever n=627，含 21 退市名，按年 × 字段，行级非空率）

（① 全量重跑;与前期 n=60 抽样**基本一致**,逐字段差异 ≤3pp,此处以全量替换。）

```
字段                                2018  2019  2020  2021  2022  2023  2024  2025
income.revenue / total_revenue      100%  100%  100%  100%  100%  100%  100%  100%
income.admin_exp                    100%  100%  100%  100%  100%  100%  100%  100%
income.oper_cost                     88%   89%   89%   86%   88%   87%   87%   88%
income.fin_exp                       88%   89%   89%   86%   88%   87%   87%   88%
income.sell_exp                      86%   87%   87%   84%   86%   85%   86%   86%
income.rd_exp                        55%   83%   83%   83%   84%   83%   83%   84%
income.int_exp                       13%   16%   16%   18%   17%   19%   18%   18%
balancesheet.total_assets           100%  100%  100%  100%  100%  100%  100%  100%
balancesheet.total_hldr_eqy_inc_min 100%  100%  100%  100%  100%  100%  100%  100%
balancesheet.accounts_receiv/pay     ~87   ~87   ~88   ~87   ~87   ~87   ~86   ~86
balancesheet.inventories/prepayment  ~87   ~87   ~87   ~86   ~86   ~86   ~86   ~86
balancesheet.adv_receipts            82%   79%   34%   33%   37%   39%   41%   45%
balancesheet.contract_liab           10%   16%   85%   91%   90%   93%   92%   91%
  → adv_receipts ∪ contract_liab     ~82   ~90   ~92   ~92   ~90   ~91   ~91   ~89
```
注:行级非空率(含 update_flag 双行);缺失主体为**金融股**(无 oper_cost/营运资本口径)。

### 4b. 幸存者偏差检验（① active 606 vs delisted 21，2018–2025 pooled 非空率）

**退市/退出名未系统性更差** —— 多数字段 delisted 覆盖 **≥** active,证明含退市名的 PIT
回测**不会因"输家财报取不到"产生幸存者缺口**(正是所需):

| 字段 | active | delisted | Δ |
|---|---|---|---|
| adv_receipts | 49% | 67% | **+18** |
| accounts_pay / receiv | 87/86% | 94/92% | +7/+6 |
| inventories | 85% | 92% | +7 |
| oper_cost | 88% | 91% | +3 |
| revenue / total_assets / 权益 / admin_exp | 100% | 100% | 0 |
| **rd_exp** | **80%** | **59%** | **−21** ⚠ |
| contract_liab | 69% | 65% | −4(被 adv_receipts +18 抵消,union 无损) |

**唯一实质例外 = `rd_exp`**:退市名报研发费用更少(−21pp)→ C2 的 R&D 加回在退市/输家
cohort 更易缺失,可能对 C2 引入方向性偏差。补进 §7 C2 caveat。样本 delisted n=21(小),
差值为方向性证据。

## 5. 人工核验案例（PIT 证据）

| 公司 | end_date | ann_date | f_ann_date | 说明 |
|---|---|---|---|---|
| 600519 茅台 | 20211231 | 20220331 | 20220331 | 年报滞后 3 月;公告前不可见 |
| 000651 格力 | 20211231 | 20220430 | 20220430 | 年报滞后 4 月 |
| 600036 招行 | 20211231 | 20220319 | 20220319 | 年报滞后 ~2.5 月 |
| (季报) 茅台 | 20220630 | 20220803 | 20220803 | 中报滞后;逐期 ann 晚于期末 |

**证得:** 报告期末 ≠ 可得日;`f_ann_date` 是可见日;生效应取 `f_ann_date` 之后。重述定日不可得(§3)。

## 6. 逐候选结论

- **C1 — GPA `(revenue − oper_cost)/total_assets`:** **FEASIBLE(排除金融股)。** 三字段全在;revenue/total_assets 100%,oper_cost ~89%(缺=金融股)。PIT 用 f_ann_date。唯一 caveat = **金融股不可算 → 显式排除,不填补**。
- **C2 — PROF(R&D 加回):** **FEASIBLE(排除金融股)+ caveat。** rd_exp/int_exp **单列存在**(charter 关键疑虑解除)。caveat:① 利息项用 `fin_exp`(89%)而非 `int_exp`(20%);② `rd_exp` 2019 起 ~84%、2018 仅 53%、2017 ~0 → 早窗弱,且**缺 rd_exp 的处理**(视作不可算 vs 视作 0)须 Gate-2/3 显式定,不得静默;③ 金融股排除。
- **C3 — Cash-based OP:** **FEASIBLE + 最多 caveat。** 需两期 + 营运资本项(~88%)+ 金融股排除;**`adv_receipts` 2020 断裂由 `contract_liab` 补齐**(coalesce 后 82-92%)。**建议 Gate 应计项固定为:** Δ应收、Δ存货、Δ预付、Δ应付、Δ(adv_receipts + contract_liab);n_cashflow_act 作现金交叉。

**共性:** 三候选**一律排除金融板块**(银行/证券/保险——无营业成本与营运资本口径,实证坐实);PIT 生效键 `f_ann_date` + 下一交易日;重述无法定到重述日(已知风险)。

## 7. 门结论（未通过即停）

- **Gate-1 通过判据:** charter 必需字段是否 PIT 可得 → **是**(三候选字段齐、PIT 语义齐、覆盖足够+口径清楚)。
- **本 memo 不提任何实现方案、不建数据视图、不建因子。** 通过复核后才谈 **Gate-2**(仅"财报 PIT 契约 + 数据桥"的独立小 OpenSpec,不含因子/模型/生产改动)。
- **待用户决策的 caveat(带入 Gate-2/3,不在此拍板):**
  1. 金融股排除的界定口径(用 oper_cost 缺失 or 显式行业名单);
  2. C2 缺 `rd_exp` 的处理(不可算 vs 视作 0);利息项固定用 `fin_exp`;
     **幸存者注(§4b):`rd_exp` 在退市/输家 cohort 覆盖更低(delisted 59% vs active 80%,−21pp)→ R&D 加回可能对 C2 引入方向性偏差,Gate-3 前须评估**;
  3. C3 应计项集合固定(含 adv_receipts∪contract_liab);
  4. 重述 PIT 限制的接受度(取原始披露值 + 记风险)。

**STOP —— 等用户复核。**
