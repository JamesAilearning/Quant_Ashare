# v2-factor-mining-foundations — delta for 2026-08-05-pv-incremental-gp-wiring

## ADDED Requirements

### Requirement: pv_incremental_v1 的增量判据 SHALL 在 GP 繁殖期生效且基线 SHALL provenance 绑定

GP 搜索 SHALL 以对 Alpha158 基线预测的正交惩罚参与适应度，且基线
SHALL 经 provenance 绑定后方可参与评分：

1. 正交罚 SHALL 为 banded hinge（`权重 × max(0, 日截面 Spearman
   平均 |ρ| − 冻结带)`），带与权重 SHALL 取自冻结件；未开启该权重时
   既有 v1 适应度公式 SHALL 逐字不变；
2. 相关度 SHALL 以**日截面** Spearman 计（与冻结件/OOS 评估器同语义）；
3. 基线未覆盖的交易日 SHALL NOT 产生惩罚，且未覆盖计数 SHALL 入 run
   记录如实披露（IS 前段无基线是折几何的必然结果，不得静默吸收）；
4. 缓存/checkpoint SHALL 携基线指纹，跨基线（含无基线↔有基线）复用
   SHALL 使既有分数失效重算；
5. 基线装载 SHALL 强制 provenance sidecar 绑定（模型名恰等、
   file_sha256 绑盘上文件、run_config_sha256 与 source_git 非空），
   任一不满足 SHALL 拒绝运行；
6. 基线导出 SHALL 逐折校验预测工件 sha256、SHALL 拒绝任何触及盲态
   holdout 年或禁用期的折、SHALL 要求单一干净 commit，且 SHALL NOT
   覆盖既有导出；
7. D5 边界 SHALL 保持：基线通路 SHALL NOT 直接 import qlib 或
   `src.pit`。

#### Scenario: 带内相关不罚、超带线性罚

- **GIVEN** 冻结带 0.30、权重 2.0
- **WHEN** 候选对基线的日截面平均 |ρ| 为 0.30 / 0.50
- **THEN** 罚项分别为 0.0 / 0.4

#### Scenario: 基线未覆盖窗段不罚且披露

- **GIVEN** 候选的因子值日期与基线无交集
- **WHEN** 计算正交罚
- **THEN** 罚项为 0，且该表达式计入 run 记录的未覆盖计数

#### Scenario: 未绑定基线拒绝运行

- **GIVEN** 基线 parquet 缺 provenance sidecar 或 sidecar 与盘上文件
  的 sha256 不符
- **WHEN** miner 装载基线
- **THEN** 运行拒绝，不产生任何评分

#### Scenario: 触及 holdout 的折拒绝导出

- **GIVEN** 某折 test 窗落在盲态 holdout 年
- **WHEN** 运行基线导出器
- **THEN** 导出拒绝并点名该折，不写出任何工件
