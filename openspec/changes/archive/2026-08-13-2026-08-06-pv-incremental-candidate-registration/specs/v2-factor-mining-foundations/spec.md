# v2-factor-mining-foundations — delta for 2026-08-06-pv-incremental-candidate-registration

## ADDED Requirements

### Requirement: 候选注册 SHALL 由工具从 GP 池生成并自证可被消费

pv_incremental_v1 的 OOS 决策批次 SHALL 由注册工具从 GP 池生成，且：

1. 注册 SHALL 拒绝非本协议的 GP run——run 的解析后配置与冻结件在
   宇宙、IS 窗、字段集、前瞻收益价格、ic_term、薄日门、简约系数与
   正交带权上任一不等即拒；未绑定基线预测的 run SHALL 拒（其候选
   未经增量判据选择）；
2. 候选 id SHALL 为安全文件名 slug、批内唯一，且 SHALL NOT 由进程
   随机的 `expr_hash` 派生；
3. 表达式 SHALL 以冻结文法可解析的形态逐字登记；
4. 每个候选 SHALL 携带池记录的 IS 方向（+1/-1）；
5. 适应度非有限的池条目 SHALL NOT 进入注册；
6. 注册器 SHALL 在写盘前以**评估器自身的** preflight 验证清单，任一
   拒绝即中止；
7. 已存在的注册 SHALL NOT 被覆盖。

#### Scenario: 异协议 GP run 拒绝注册

- **GIVEN** GP run 的配置在 ic_term / 窗口 / 宇宙 / 字段集任一项上
  偏离冻结件
- **WHEN** 运行注册器
- **THEN** 注册拒绝并点名漂移项，不产生清单

#### Scenario: 清单自证可被消费

- **GIVEN** 池中候选含禁用终端或非 CSF/PURE 根
- **WHEN** 运行注册器
- **THEN** 在写盘前以评估器 preflight 判定拒绝

#### Scenario: 方向逐行随表达式带出

- **GIVEN** 池中某候选的 IS 方向为 -1
- **WHEN** 生成清单
- **THEN** 该表达式所在行的 orientation 为 -1
