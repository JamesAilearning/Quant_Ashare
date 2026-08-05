# v2-daily-stock-recommendation — delta for 2026-08-05-add-serving-universe-consistency-guard

## ADDED Requirements

### Requirement: 单模型 serving SHALL 以 fail-closed 守卫绑定模型与请求宇宙

单模型路径的 `recommend()` SHALL 在任何特征构建之前验证请求的
`instruments` 与模型 sidecar 记录的训练宇宙一致，且 SHALL 满足：

1. 训练宇宙 SHALL 来自模型自身元数据（晋升 meta `<stem>.meta.json`
   优先，其次 trainer sidecar `<model>.pkl.meta.json`，与 fit 窗解析
   共用同一路径序）——SHALL NOT 来自代码默认值；
2. 请求宇宙 ≠ 模型宇宙 SHALL 拒绝运行（exit 非零），domain 错误
   SHALL 同时给出两个宇宙值、拒绝理由（零认证证据/横截面分布外）
   与修法；
3. 任一 sidecar 存在但均不携 `universe` 字段、或字段非非空字符串、
   或 sidecar 不可读 SHALL 同样拒绝并指向回填指引——SHALL NOT
   静默放行或默认；
4. canonical 训练路径产出的 trainer sidecar SHALL 自记 `universe`
   （配置未知 universe 时 SHALL 不写字段而非造默认值）；
5. 单模型产物 meta SHALL 增记 `model_universe`（sidecar 解析值）；
   ensemble 产物形状 SHALL 不变（身份经 manifest）；
6. ensemble 路径既有拒绝行为（成员 index 恰等）SHALL 保持不退化。

#### Scenario: 宇宙不匹配拒绝

- **GIVEN** 模型 sidecar 记录 `universe: csi300`
- **WHEN** 单模型 `recommend()` 以 `instruments=csi800` 运行
- **THEN** 运行在特征构建前拒绝，错误信息同时含 `csi300` 与
  `csi800` 及修法

#### Scenario: 宇宙未记录拒绝（回填指引）

- **GIVEN** 模型仅有不携 `universe` 字段的 trainer sidecar
- **WHEN** 单模型 `recommend()` 运行
- **THEN** 运行拒绝且错误信息含回填（backfill）指引，不以任何
  代码默认宇宙放行

#### Scenario: 匹配组合正常通过

- **GIVEN** 模型 sidecar 记录 `universe: csi300`
- **WHEN** 单模型 `recommend()` 以 `instruments=csi300` 运行
- **THEN** 守卫放行，产物 meta 携 `model_universe: csi300`
