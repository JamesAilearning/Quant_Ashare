# v2-canonical-backtest-contract — delta for 2026-08-07-risk-constraints-mode-optin

## ADDED Requirements

### Requirement: 违规反应 SHALL 可配，且缺省保持 RAISE

两引擎（`PipelineConfig` / `WalkForwardConfig`）SHALL 各提供
`risk_constraints_mode`，取值 `raise`（缺省）或 `warn_and_clip`，非法
取值 SHALL 拒。缺省值 SHALL 使既有配置的行为逐字节不变。

约束**限值** SHALL NOT 因本开关改变：`campaign_v1` 的
`max_per_name=0.05` / `max_leverage=1.0` 及其余校准值保持不变。

#### Scenario: 缺省不变

- **GIVEN** 一份未声明 `risk_constraints_mode` 的既有配置
- **WHEN** 它跑回测
- **THEN** 违规仍然 RAISE，行为与本变更前相同

#### Scenario: 非法取值拒绝

- **GIVEN** `risk_constraints_mode="clip"`
- **WHEN** 构造任一引擎的配置
- **THEN** 拒绝并指出合法取值

### Requirement: 容忍违规 SHALL 要求独立声明用途

`metrics_purpose`（`official` 缺省 / `predictions_only`）SHALL 是与
`risk_constraints_mode` **相互独立**的配置字段，SHALL NOT 由后者派生 ——
派生等于选了宽松反应即自动获得豁免，个人覆盖档（`my_*.yaml` /
`*.local.yaml`，按约定不入库）与直接构造 runner 的调用方都会因此绕过守卫。

`risk_constraints_mode="warn_and_clip"` 而未声明
`metrics_purpose="predictions_only"` SHALL 在配置层拒绝，并**独立地**在
`BacktestRunner.run` 的官方指标边界拒绝（所有入口的汇合点）。边界处的
`metrics_purpose` 缺省 SHALL 为 `official`。

#### Scenario: 只选宽松反应不足以放行

- **GIVEN** 配置只声明 `risk_constraints_mode="warn_and_clip"`
- **WHEN** 构造配置或直接调用 runner
- **THEN** 两处各自拒绝，并说明须声明产物为预测

### Requirement: 容忍违规的跑 SHALL NOT 被标为 canonical

容忍了风险约束违规的跑 SHALL NOT 在任何一层被标记为 canonical/official
指标。理由是 clip 为 post-trade：`return_series` / `risk_analysis` / `positions` 均为
未 clip 的执行结果，即 RAISE 所拒绝的那批数字。因此 `metrics_purpose`
为 `predictions_only` 时：

1. `CanonicalBacktestOutput.metric_status` SHALL 为
   `predictions_only_non_canonical`，而非 `official`；
2. 该状态 SHALL 逐层传递到 per-fold 记录、总表报告顶层、以及 run catalog
   记录 —— 任一层缺失都会让消费者把这批数字读成普通结果；
3. 任一**有指标**的 fold 非 official SHALL 使整跑非 official（混折不得
   洗白标签）；未盖章的 fold SHALL NOT 被视为 official 的证据；
4. `metrics_purpose` SHALL 与状态同处记录（含官方跑），使"键不存在"不成为
   判断用途的唯一信号；
5. `official_backtest_path` SHALL 保持记录**实际执行的代码路径**，不因本
   状态改写 —— 该字段陈述的是路径身份，改写它是另一方向的失实。

#### Scenario: 宽松跑的报告与索引都自陈非 canonical

- **GIVEN** 一次 `metrics_purpose="predictions_only"` 的 walk-forward 跑
- **WHEN** 读取 `walk_forward_report.json` 与 `output/runs/_index.jsonl`
- **THEN** 两者的 `metric_status` 均为 `predictions_only_non_canonical`，
  且均带 `metrics_purpose`

### Requirement: 失败的 fold SHALL NOT 被标为 official

失败的 fold SHALL 携带显式的失败状态，SHALL NOT 被标为 official。在
`BacktestRunner.run` 返回前失败的 fold（训练/预测/回测阶段）从未跨过
canonical 边界，因此**没有指标可标记**；它
SHALL NOT 被盖上运行声明的用途 —— 后者会让占位记录看起来像是该 fold 通过
了边界。运行级判定 SHALL 将其视为**两边都不构成证据**：既不为整跑背书，
也不污染整跑。运行级判定 SHALL 由**各 fold 实际携带的状态**推导，报告与
run catalog SHALL 使用同一个推导，声明的用途 SHALL NOT 单独构成 official
的依据。

#### Scenario: 失败折不为整跑背书

- **GIVEN** 一次 official 跑，其中一折失败、其余折为 official
- **WHEN** 生成总表与 catalog 记录
- **THEN** 失败折自身记为失败状态，整跑仍为 official（部分完成）

#### Scenario: 全部失败不得自称 official

- **GIVEN** 一次跑没有任何 fold 产出指标
- **WHEN** 生成总表
- **THEN** 运行级状态回落到声明的用途，而不以失败折作为 official 的证据

#### Scenario: 混折不洗白

- **GIVEN** 一次跑里既有 official 折又有 predictions-only 折
- **WHEN** 生成总表
- **THEN** 运行级状态为非 canonical

### Requirement: 用途 SHALL NOT 进入约束校准映射

`metrics_purpose` SHALL 记在 provenance 的 `strategy` 层，SHALL NOT 作为
`provenance.config.risk_constraints` 的键 —— 战役 veto 对该映射做**精确
等值**比较，多一个键会使每个新生成的 csi800 战役 fold 无法晋升。

#### Scenario: 校准映射保持恰好五个校准值

- **GIVEN** 任一带约束的跑（含 `predictions_only`）
- **WHEN** 读取 `provenance.config.risk_constraints`
- **THEN** 其内容恰等于战役 veto 的期望校准值，用途在其**兄弟**键上可读
