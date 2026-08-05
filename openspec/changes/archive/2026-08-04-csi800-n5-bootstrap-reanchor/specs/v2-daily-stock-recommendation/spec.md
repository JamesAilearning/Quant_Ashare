# v2-daily-stock-recommendation — delta for 2026-08-04-csi800-n5-bootstrap-reanchor

## ADDED Requirements

### Requirement: 自举中止后的三元组重注册 SHALL 走新提案且如实入档

首次自举被任一门拒绝而中止后，若操作人裁决继续晋升，重注册 SHALL 以**新 OpenSpec 提案**进行，且满足全部下列义务：

1. 新三元组窗口 SHALL 按与原注册**同源的冻结公式**在当前 bundle
   尾重新推导（T-6m/T-3m/T 错峰、24 月滚动训窗 + 3 月 valid、
   交易日吸附），推导规则与结果表 SHALL 写入提案供签署；
2. 被拒轮次的**全部门工件（含 FAIL 件）** SHALL 随提案入库
   （evidence 目录），FAIL 工件永不删除；
3. 提案 SHALL 显式披露新旧窗口的任何重叠，并声明门判据与工装
   零改动——公式重锚定 SHALL NOT 构成对失败窗口的挑选；
4. 被拒轮次已训成员（含通过成员级门者）SHALL 全体弃置，新三元
   组三名成员 SHALL 全部重训——不得混装新旧窗口成员；
5. 重注册提案 merge 之前，任何点火 SHALL NOT 发生（merge = 新窗
   冻结生效，预注册纪律与原注册同权）。

#### Scenario: 重注册后旧窗口成员不得晋升

- **WHEN** 重注册提案 merge 后，切换执行器收到按旧（被拒轮次）
  窗口训练的成员
- **THEN** 预注册窗口绑定按新冻结值逐位比对失败，成员被拒——
  旧窗口成员（含曾过门者）无晋升路径

#### Scenario: 被拒轮次证据不可湮灭

- **WHEN** 重注册提案入库
- **THEN** 被拒轮次的三门工件与拒绝简报以 evidence 形式入库，
  后续任何变更 SHALL NOT 删除或改写 FAIL 工件
