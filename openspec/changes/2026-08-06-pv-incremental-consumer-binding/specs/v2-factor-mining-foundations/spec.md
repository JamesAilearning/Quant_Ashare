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
5. 注册的真实性 SHALL 以**已提交的** append-only 战役 ledger 为权威：
   manifest 摘要 SHALL 已被 ledger 条目记录，ledger 的工作树内容与其
   HEAD 提交内容不一致、ledger 未提交、不在仓库内、或协议不符 SHALL
   一律拒绝（旁置 sidecar 与清单同等可写，二者自洽不构成认证）；
6. 消费方所用的 provenance SHALL 取自该 ledger 条目；旁置 sidecar 的
   输入摘要与 ledger 条目不一致 SHALL 拒绝；
7. 上述校验 SHALL NOT 提供关闭开关。

#### Scenario: 注册后被修改的清单拒绝

- **GIVEN** 已注册的 candidates.json 在注册后被编辑（含语义等价的重排）
- **WHEN** 评估器或裁决器消费它
- **THEN** 拒绝并指出字节与注册摘要不符

#### Scenario: 异基线拒绝

- **GIVEN** 所传基线的摘要不等于注册记录的挖掘基线摘要
- **WHEN** 评估器运行
- **THEN** 拒绝并指出候选是对另一基线繁殖的

#### Scenario: 仅工作树修改的 ledger 不构成注册

- **GIVEN** ledger 在工作树被追加了某清单摘要但未提交
- **WHEN** 任一消费方运行
- **THEN** 拒绝并指出与已提交内容不一致

#### Scenario: 篡改的 sidecar 输入摘要拒绝

- **GIVEN** 清单已被 ledger 合法记录，但旁置 sidecar 的基线摘要被改
- **WHEN** 任一消费方运行
- **THEN** 拒绝并指出 sidecar 与已提交 ledger 条目不一致

#### Scenario: 未注册清单拒绝

- **GIVEN** 清单旁没有注册 sidecar
- **WHEN** 任一消费方运行
- **THEN** 拒绝并指向注册器
