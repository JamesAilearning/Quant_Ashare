# v2-csi800-expansion-guards — delta for 2026-08-05-membership-coverage-stamp

## ADDED Requirements

### Requirement: sleeve 覆盖界 SHALL 以 demonstrated coverage stamp 为准且缺失时回退变更界

成员快照解析器 SHALL 在写出 instruments span 文件的同时原子落盘 demonstrated coverage stamp（`instruments/membership_coverage.json`，schema `membership_coverage_v1`，逐 sleeve 记录解析器实际看到的最后快照日）；sleeve 覆盖守卫 SHALL 按下列顺序裁定 per-sleeve 覆盖界：

1. 该 sleeve 在 stamp 中有条目 → 界 = stamp 的 `last_snapshot`
   （解析器可证看到的快照终点——churn-free 尾在此界内 SHALL 放行）；
2. stamp 文件缺失或该 sleeve 无条目 → 界 = span 文件的最后可证
   变更日（legacy 语义逐字保留）；
3. stamp 声称的覆盖早于 span 中可见的变更 SHALL 拒绝（工件自相
   矛盾）；stamp 文件存在但畸形/schema 不符 SHALL 拒绝（缺失=合法
   legacy，畸形=腐坏——静默回退会把腐坏洗成保守）。

整体 `coverage_end` SHALL 仍取各 sleeve 界的 min（per-sleeve 独立
覆盖语义不变）；部分重解析 SHALL 合并保留未触及 sleeve 的既有
stamp 条目。

#### Scenario: stamp 放行无变更尾巴内的 as_of

- **WHEN** 某 sleeve 最后变更为 D1，stamp 记录解析终点 D2 > D1，
  attribution/门以 D1 < as_of ≤ D2 解析 sleeve
- **THEN** 解析放行且 `coverage_end` 如实报 stamp 界——构成未变
  是解析器可证看到的事实，非合成推断

#### Scenario: 无 stamp 的 legacy bundle 行为不变

- **WHEN** bundle 无 `membership_coverage.json`（或该 sleeve 无
  条目）且 as_of 晚于该 sleeve 最后可证变更日
- **THEN** 按 legacy 语义拒绝（保守代理界继续生效）

#### Scenario: 自相矛盾或畸形的 stamp 拒绝

- **WHEN** stamp 声称的 `last_snapshot` 早于 span 中可见的变更日，
  或 stamp 文件存在但无法解析/schema 不符
- **THEN** sleeve 解析 fail-loud 拒绝，指出腐坏而非静默回退
