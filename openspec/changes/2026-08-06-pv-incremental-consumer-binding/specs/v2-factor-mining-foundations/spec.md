# v2-factor-mining-foundations — delta for 2026-08-06-pv-incremental-consumer-binding

## ADDED Requirements

### Requirement: OOS 消费方 SHALL 强制注册绑定

pv_incremental_v1 的 OOS 评估器与 FWER 裁决器 SHALL 在消费候选清单前
加载其注册 provenance，且：

1. 缺注册 sidecar 的清单 SHALL 拒绝评估与裁决（未注册的清单不是批次）；
2. 清单当前字节的 sha256 SHALL 等于注册时记录的摘要，不等即拒（独占
   创建只防并发注册，不防事后修改）；
3. 评估器所用基线的 sha256 SHALL 等于注册记录的挖掘基线摘要，不等即拒
   （同一冻结模型的多份合法导出必须按摘要区分）；
4. 判决工件 SHALL 记录所绑定的注册摘要；
5. 上述校验 SHALL NOT 提供关闭开关。

#### Scenario: 注册后被修改的清单拒绝

- **GIVEN** 已注册的 candidates.json 在注册后被编辑（含语义等价的重排）
- **WHEN** 评估器或裁决器消费它
- **THEN** 拒绝并指出字节与注册摘要不符

#### Scenario: 异基线拒绝

- **GIVEN** 所传基线的摘要不等于注册记录的挖掘基线摘要
- **WHEN** 评估器运行
- **THEN** 拒绝并指出候选是对另一基线繁殖的

#### Scenario: 未注册清单拒绝

- **GIVEN** 清单旁没有注册 sidecar
- **WHEN** 任一消费方运行
- **THEN** 拒绝并指向注册器
