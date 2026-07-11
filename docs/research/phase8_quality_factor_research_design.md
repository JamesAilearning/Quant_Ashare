# 阶段 8：基本面质量因子研究设计与 Claude Code 交接

> **状态：设计建议，非实现授权。**
>
> **读者：** 后续负责 OpenSpec、数据勘察和代码实现的 Claude Code，以及审核者。
>
> **决策人：** 用户。任何生产晋升、正式大规模回测、GPU 窗口或扩大候选空间，均须由用户明确批准。
>
> **创建日期：** 2026-07-10

## 1. 一句话决策

阶段 8 的目标不是继续优化 Alpha158 的短期量价信号，也不是重启已经封存的 GP 挖矿线；目标是：**在 PIT 正确的 CSI300 研究环境中，受控地验证“利润真实且可持续”的基本面质量因子，能否为低换手、较长持有提供增量信息。**

这是一项研究基础设施和可证伪假设工作，不是一次“多试几个指标、挑回测最好者”的选股工程。

## 2. 已知事实与不应改写的边界

### 2.1 已经成立的战略结论

- 降频、标签跨度、cadence × horizon 三次可信阴性，已穷尽当前 `Alpha158 + CSI300` 的内部调参路径；问题是原料薄，而非再平衡机制尚未调到最佳。
- 已确定顺序：**先在 CSI300 验证质量因子方向，再讨论 CSI800 扩池。** 不得把扩池、top-k、cadence、标签跨度和新因子混在同一首轮试验。
- 当前 canonical 生产路径仍以 Alpha158 为准。阶段 8 的任何产物一律是 `research-only`，不得自动加入 canonical runtime、每日推荐或生产模型。
- 旧 GP 生产路线仍遵循 D6 的封存决定；本文不授权以“新文献”或“更多算力”为由恢复 GP。

### 2.2 本阶段明确不做

| 不做什么 | 原因 |
| --- | --- |
| 用现有 `daily_basic` 直接宣称获得 ROE、盈利质量或财报 PIT | 它只有日度估值/换手/市值快照；财报基本面 PIT 尚未建成。 |
| 用报告期末日代替信息可得日 | 会制造最经典的财报前视偏差。 |
| 在第一轮同时改变因子、股票池、模型、标签、调仓频率和 top-k | 结果不再能归因。 |
| 以 IC、Sharpe、回测收益或低相关的任一单项作为“新因子”结论 | 这些都不能证明增量性、统计可靠性和可交易性。 |
| 用 OOS 结果修改公式、公告滞后、窗口、持有期或候选集合 | 这就是数据窥探；必须新建计划并重新验证。 |
| 自动晋升候选 | 研究候选必须经过人工审查和独立的晋升决定。 |

## 3. 文献给出的收敛判断

文献不支持“质量因子在 A 股必然有效”这个说法，反而要求收窄首个假设。

- [Jansen, Swinkels, Zhou (2021), *Anomalies in the China A-share Market*](https://doi.org/10.1016/j.pacfin.2021.101607) 发现 A 股质量类总体证据偏弱，但盈利能力、ROE、毛利/资产、经营利润存在局部信号。
- [Hou, Qiao, Zhang, *Finding Anomalies in China*（2024 working paper）](https://eng.pbcsf.tsinghua.edu.cn/dfiles/wp6.pdf) 在 454 个中国信号中，按多重检验将 t 门槛校准至 2.85；许多单因子结论消失，组合策略才更有希望。
- [Yin & Liao (2020), *Firm’s Quality Increases and the Cross-Section of Stock Returns: Evidence from China*](https://doi.org/10.1016/j.iref.2019.12.001) 提示“质量变化”也值得与质量水平区分验证，而非只测高 ROE。
- [Liu, Stambaugh, Yuan (2019), *Size and Value in China*](https://doi.org/10.1016/j.jfineco.2019.03.008) 警示中国小市值壳价值和盈利价格比暴露会混淆质量结论。

因此，首轮应验证一个窄的“盈利真实性与持续性”因子族；广义 QMJ、成长、安全、分红、估值和国企异质性均不得在同一首轮任意扩张。

## 4. 推荐的首个研究问题

### H8-Q1：利润真实且可持续

> 在 CSI300 的 PIT 成分股中，若一家公司在当时已经公开可得的财报显示出更高、且更可信的经营盈利能力，则它在预先固定的中长期持有期内，应有更好的风险调整后横截面预期收益，或为现有 Alpha158 研究特征带来可检验的增量。

这是一个**可被推翻**的命题。以下均构成有效阴性结果：

- 数据无法满足公告日、首次披露和修订版本的 PIT 证明；
- 候选没有足够覆盖率、符号不稳定、或暴露主要来自规模/行业/壳价值；
- 冻结后的 OOS 无增量，或扣成本后不能支持目标策略形态；
- 整个候选批次未通过预先声明的多重检验门。

### 4.1 有限候选集，而不是开放搜索

第一份确认性预注册最多注册 **3 个主候选**，并将它们视为同一多重检验批次。建议候选来自同一经济命题的三个已发表定义：

1. 广义经营盈利能力（例如 PROF 类定义）；
2. 毛利/资产类盈利能力（GPA）；
3. 现金基础经营盈利能力（cash-based operating profitability）。

应计质量或“经营现金流相对会计盈利”的差异可以作为**预注册的确认性诊断**，但不能在看到结果后临时调权、加减项或组合成一个赢家版本。

在开始任何真实回测前，计划必须逐个写死：精确公式、输入字段、单位、方向、允许变换、固定参数、更新频率、持有期、执行时点、缺失策略和候选 ID。若真实 Tushare schema 无法支持某个定义，则该候选应被标为 `not-feasible`，不是用近似字段静默替代。

## 5. 研究链条与停止门

```text
用户的“好公司”理论
        ↓
有限候选集与可证伪章程（不看任何回测）
        ↓
财报 PIT 可行性勘察 ──失败──> 记录并停止；不得试因子
        ↓
提交确认性预注册 + 机器 gate rehearsal
        ↓
A. 因子本体 OOS 验证
        ↓
B. 相对 Alpha158 / 已采纳池的增量验证
        ↓
C. 独立的低换手策略形态验证
        ↓
人工审查；另行决定是否提出晋升变更
```

### Gate 0 — 用户理论签字

在任何数据探索前，由用户确认本轮最优先的“好公司”含义。本文推荐从“利润真实且持续”开始；若用户选择成长、财务健康或估值，则必须另写经济机制和有限候选集，不得沿用 H8-Q1 的结果或判据。

### Gate 1 — 财报 PIT 勘察，未通过即停止

后续 Claude Code 的第一个技术任务应是**只读勘察**，不是实现特征。须逐项确认数据供应商的真实 schema 和历史覆盖：

- 可用的三表/财务指标端点以及每个字段的生产者；
- 报告期字段、公告日期字段、首次公告/更正公告信息和版本语义；
- 公告发生在交易日盘后时的可交易时点规则；默认保守做法是下一可交易日生效，除非有可审计的发布时间戳支持更早使用；
- 是否能保留当时可见的原始版本，而不是把今天的修订数字回填到历史；
- CSI300、退市股和不同年份的覆盖率、缺失模式、重复披露和 stale 值；
- 至少若干可人工核验公告案例，证明公告日前不可见、公告后才生效、更正后才替换旧值。

当前 `docs/pit/pit_universe_design.md` 已将“财报按公告日的 PIT join”列为 Phase-E.2，故不得把该事实当成已经完成。

**重要区分：** 财报按“截至该日最新已公告报表”持续生效，是有意的 `as-of` carry-forward；它不是对缺失值做 `fillna`。无效、缺失或尚未公告的记录必须保持缺失并显式报告，绝不能后向填充、全样本填充或静默删股。

### Gate 2 — 新的 OpenSpec 基础变更

只有 Gate 1 通过且用户确认数据契约后，才创建一个**仅覆盖 PIT 财报研究地基**的 OpenSpec change。建议范围包括：

- 原始财报文件的版本化获取、内容 hash 和 fetch provenance；
- `report_period`、`announcement_date`、`available_from_trade_date`、修订关系和数据版本的显式 contract；
- 一个唯一的研究侧 PIT financial data view；任何 evaluator 或特征实现不得绕过该 view 直接读未审计原始文件；
- PIT 案例、覆盖率、修订、无前视和缺失行为的治理测试。

此基础变更**不**应同时加入质量因子、改 Alpha158、改训练配置或跑策略实验。

### Gate 3 — 确认性预注册与演练

数据地基通过后，建立一个新的研究账本和一组按家族命名的产物，建议如下：

```text
docs/prereg/quality_factor_research_ledger.yaml
docs/prereg/quality_<family>_charter.md
docs/prereg/quality_<family>_pit_preflight.md
docs/prereg/quality_<family>.yaml
docs/prereg/quality_<family>_rehearsal.md
docs/prereg/quality_<family>_results.md
docs/prereg/quality_<family>_verdict.txt
```

现有 `docs/prereg/*.yaml` 的 git-provenance gate 可以复用，但它只能证明计划早于 run，**不能证明财报 PIT 正确或候选空间已冻结**。阶段 8 计划至少还须人工审查以下块：

```yaml
protocol_id:
status: confirmatory
decision_scope: research_only

theory:
  investor_thesis:
  economic_mechanism:
  citations:
  factor_expected_sign:
  falsifier:

candidate_family:
  registered_candidates:
    - id:
      exact_formula:
      input_fields:
      permitted_transformations:
      fixed_parameters:
      update_frequency:
      rank_direction:
  prohibited_variants:
  multiplicity_family_id:

pit_data_contract:
  source_snapshot_manifest:
  report_period_field:
  announcement_date_field:
  effective_trade_time_rule:
  restatement_revision_policy:
  carry_forward_and_staleness_rule:
  missingness_policy:
  coverage_acceptance_rule:

study_design:
  universe: csi300_pit
  universe_membership_artifact:
  benchmark:
  execution_timing:
  cost_model:
  st_handling:
  fold_windows_and_embargo:
  untouched_final_holdout:
  permitted_config_diff: [quality_factor_only]

adjudication:
  primary_factor_metric:
  primary_strategy_metric:
  fwer_multiple_testing_rule:
  incremental_spanning_rule:
  bootstrap:
  sensitivity_slices:
  success_iff:
  reject_iff:
  inconclusive_iff:

promotion:
  manual_only: true
```

所有候选、公式变体、数据快照、参数、失败试验和未注册探索必须写进 ledger。DSR 与 PBO 的有效试验数必须基于这个完整记录；没有记录就不得声称它们校正了选择偏差。

演练至少应覆盖：正常接受、未注册候选被 flag、dirty checkout 被拒绝、计划提交晚于 run 被拒绝、数据 manifest 不一致被拒绝、PIT 案例失败被拒绝。

### Gate 4A — 因子本体验证

本阶段只回答：该预注册因子是否有 PIT 正确、方向合理、覆盖充分、随时间稳定的中长期信号？

- 同批候选统一进行 block-bootstrap / FWER 或预注册 FDR 控制；不得逐个套 `p < 0.05`。
- 报告 RankIC/IC、时间稳定性、覆盖率、缺失率、横截面分布、单调性、输入微扰和预注册的子样本切片。
- 所有标准化、排名和参数估计只能使用当时及以前数据；不得全样本 z-score。
- 没有 PIT 行业、规模或国企分类数据时，只能报告暴露和“未中性化”，不得伪称已控制混杂。

首轮只允许固定一个主持有期；其余持有期只能作为预先声明的敏感性报告，不能事后挑最优者作为主结果。

### Gate 4B — 增量验证

冻结 4A 通过的公式后，才允许测试“Alpha158”与“Alpha158 + 该质量因子”的增量。两侧除质量因子外必须共享：

- PIT 数据版本与 universe membership；
- 折窗、embargo、benchmark、成本、ST 处理、执行时点、模型、top-k 和 cadence；
- 干净 checkout、一次连续运行和可验证的 git provenance。

主尺子仍应是预注册的配对**净超额**及 moving-block-bootstrap CI；IC、IR、gross-vs-net、相关性、overlap 与 seam 指标是必报诊断，而非挑选替代主判据的工具。

还需验证候选对既有池的边际贡献：相关/聚类只是初筛，随后必须做残差化或 spanning 式增量检验。可参考 [Taming the Factor Zoo](https://doi.org/10.1111/jofi.12883) 与 [Lucky Factors](https://doi.org/10.1016/j.jfineco.2021.04.014)。

### Gate 4C — 策略形态验证

只有 4B 成功，才单独预注册“约 20 只、低换手、较长持有”的策略试验。这个阶段不得与首轮因子发现合并，因为它同时引入 top-k、调仓和执行成本变量。

成功不等于自动生产化；它只说明值得由用户决定是否申请一个单独的晋升/生产健康验证变更。

## 6. 实现架构建议

### 6.1 数据契约优先

推荐建立明确的研究数据契约，而非先把财务列塞进任意 DataFrame：

```text
versioned raw filings
        ↓
validated financial PIT contract
        ↓
FinancialPITDataView (唯一数据桥)
        ↓
registered candidate evaluator
        ↓
research reports + immutable ledger
        ↓
manual promotion decision
```

每个财务观测应能追溯到数据源、获取批次、内容 hash、报告期、公告/可用日期、修订版本和被哪一条候选公式使用。字段不可用、schema 漂移、日期语义不明或 provenance 缺失应 fail loud，不得返回空因子或默认值。

### 6.2 候选卡与 registry

实现前先定义一个可序列化的候选卡 contract。每张卡至少包含：

- 唯一 ID、家族、研究状态（`draft` / `registered` / `rejected` / `passed-research`）；
- 经济机制、论文来源、预期方向和 falsifier；
- 精确公式、输入字段、单位、时间规则、允许变换和复杂度；
- 缺失、stale、极端值、排名和中性化规则；
- 注册批次、数据 manifest、实验计划、结果和拒绝原因。

未知字段、未注册候选、未经允许的公式变换以及状态跳转必须 fail loud。研究 registry 与 canonical feature registry 必须物理和语义隔离。

### 6.3 后续“受限发现”层的正确位置

若 H8-Q1 的有限候选经验证确有信号，下一阶段才可考虑：

- 滚动 OOS 的 adaptive group-LASSO，用于筛选基本面变量族；
- 浅树，用于提出“盈利在何种条件下更有效”的有限交互假设；
- 在预注册、PIT-safe 语法内使用带复杂度惩罚的符号回归/GP。

这些工具只能**提出新的候选假设**；其输出必须进入新的预注册和未触碰验证窗，不能直接成为可晋升因子。参考 [Freyberger, Neuhierl, Weber (2020)](https://doi.org/10.1093/rfs/hhz123)、[Becker & O’Reilly (2009)](https://doi.org/10.1145/1543834.1543837) 和 [AlphaEvolve (2021)](https://doi.org/10.1145/3448016.3457324)。

## 7. 必须有的测试与报告

### PIT / contract 测试

- 报告期早于公告日时，公告日前不可读取；
- 盘后公告只能在预注册的下一可交易时点生效；
- 更正公告前返回旧版本，更正后才可返回新版本；
- 被删除、缺失、重复、过期、未知 schema 和未知数据版本均 fail loud 或按已登记策略显式拒绝；
- 退市和成员资格边界沿用现有 PIT universe 语义；
- 因子输入不存在时，不得静默用零、中位数、最新值或未来值替代。

### 研究治理测试

- 候选集合、计划、数据 manifest 和 run provenance 可追溯；
- plan 的最后提交早于所有决策级 run；
- 同批候选的多重检验集合完整且不可漏报失败者；
- A 与 B 的配置 diff 仅包含预注册的质量因子；
- Pipeline 与 WalkForward 如都产生研究报告，字段名称和 provenance schema 必须对称一致。

### 每次结果必须披露

- 全部已注册候选及其正、负、不可判定结果；
- PIT 覆盖率、缺失率、数据版本和人工案例检查；
- 主结果、所有预注册敏感性切片、gross 与 net、成本假设、换手；
- 规模、行业、国企/非国企等实际可得暴露，及哪些未能控制；
- DSR/PBO 的输入、有效试验数和局限；
- 诚实结论：成功、拒绝或不可判定，绝不将 CI 跨零写成“等价”。

## 8. Claude Code 的推荐执行顺序

1. 只读检查当前分支、OpenSpec 和数据边界；确认本设计没有被更高优先级的活动变更冲突。
2. 完成 Gate 1 的 PIT feasibility memo，**停下等待用户确认**；不得先造因子或开始回测。
3. 仅在用户批准后，提出小范围 OpenSpec：财报 PIT contract 与数据桥，不含因子/模型/生产改动。
4. 实现并验证 PIT 基础、contract 和失败模式；运行本仓库要求的导入 smoke 与逻辑/治理测试。
5. 写候选章程、ledger 和确认性预注册，提交后运行 gate rehearsal；**停下等待用户点火**。
6. 依次完成 4A、4B、4C；每一层失败都记录并停止，不扩大搜索空间。
7. 如有研究成功，仅提交结果和晋升建议；由用户决定是否发起单独生产变更。

## 9. 阅读顺序

1. 本仓库的 `docs/prereg/cadence_horizon.yaml` 和 `docs/run-comparison-runbook.md`：复用 git-provenance、共同 OOS、配对尺子和三态裁决。
2. `docs/pit/pit_universe_design.md`：理解财报 PIT 当前是缺口，不是已交付能力。
3. [Harvey & Liu (2020)](https://doi.org/10.1111/jofi.12951)、[Giglio, Liao & Xiu (2021)](https://doi.org/10.1093/rfs/hhaa111)、[Bailey et al. (2017)](https://doi.org/10.21314/jcf.2016.322)、[Bailey & López de Prado (2014)](https://doi.org/10.3905/jpm.2014.40.5.094)：建立批量检验、PBO 和 DSR 的正确预期。
4. 本文 §3 的 A 股研究：校准“盈利质量值得测，但绝不保证有效”的先验。

## 10. 交接中的一句话

**先证明财报数据在当时真的可见，再在一个冻结、有限、可证伪的盈利质量候选集里寻找增量；任何成功都必须先通过 PIT、统计、成本和人工晋升四道门，才有资格讨论生产。**
