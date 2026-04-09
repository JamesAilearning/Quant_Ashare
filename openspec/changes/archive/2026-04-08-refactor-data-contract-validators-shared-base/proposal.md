# Proposal: refactor-data-contract-validators-shared-base

## Why

V2 的三个数据契约（benchmark / universe / taxonomy）当前各自独立实现了
几乎相同的校验流程：

1. 检查 `artifact_present` / `manifest_present` → 追加 `missing_artifact_file` /
   `missing_manifest_file` 错误；
2. 比对 `metadata` 与 `REQUIRED_METADATA_FIELDS` → 追加 `schema_mismatch`；
3. 校验必备列 → 追加 `schema_mismatch`；
4. `stale_days > threshold` → `stale_data` 警告；
5. `coverage_ratio < min` → `incomplete_coverage` 警告；
6. 未来日期 / `snapshot_at` 不匹配 → 各自的 temporal 错误码；
7. 按 `errors` / `warnings` 去重并聚合出 `ok / warning / error` 的 health。

三份实现字面上几乎一模一样，在最近的两个 change 里（
`harden-canonical-backtest-input-for-quant-risks` 与
`enforce-snapshot-at-matches-artifact-data`）我们已经不得不同时在三处做对
称修改。这是典型的"改一处忘两处"温床，也是交易日历接入、adjust_mode 语义
化等下一批 change 进一步放大的成本项。

本次纯重构把共享逻辑收敛到 `src/contracts/_shared_validators.py`，
让三个契约只保留自己**真正不同**的部分（错误码命名、temporal 字段、
成员一致性 / 映射一致性的额外检查），其余全部走共享函数。

## Goals

- **零行为变化**：全套 114 个测试保持通过，不新增任何测试（重构纯粹的定义
  就是"外部可观察行为不变"）。
- **单点修改**：未来要引入交易日历、`snapshot_at` 严格相等策略改动、
  新的 warning 码等，只需改一处。
- **显式错误码保留**：每个契约的错误码常量（`ISSUE_TEMPORAL_ISSUE`
  vs `ISSUE_TEMPORAL_LEAKAGE` 等）**保持不变**，避免 spec 语义漂移。
  共享函数以错误码字符串为参数而非在内部决定。
- **不引入抽象基类**：共享模块以**无状态纯函数**导出，不搞
  `DataArtifactContractBase` 继承层次。三个契约的输入/输出数据类和返回
  类型互不相同，继承会带来类型碎片和逃逸性，纯函数组合更干净。

## Non-Goals

- **不**改任何错误码字符串或 operator-facing 字段结构。
- **不**接入交易日历（下一个 change）。
- **不**修改 loader 或 publisher。
- **不**新增测试；如果有测试挂了就说明重构失败，必须就地修复。

## What Changes

1. 新建 `src/contracts/_shared_validators.py`，导出以下纯函数：
   - `check_presence(profile, *, missing_artifact_code, missing_manifest_code)`
   - `check_metadata(profile, required_fields, *, schema_mismatch_code)`
   - `check_columns(profile, required_columns, *, schema_mismatch_code)`
   - `check_staleness(profile, threshold, *, stale_code)`
   - `check_coverage(profile, min_ratio, *, incomplete_coverage_code)`
   - `check_temporal_basic(profile, reference_date, *, temporal_code)`
   - `check_snapshot_at_mismatch(profile, *, temporal_code)`
   - `aggregate_health(errors, warnings)`
   - `dedupe(values)`
   - `normalize_columns(values)`
   - `parse_iso_date(value, *, error_cls)`

2. 重写三个契约的 `validate_and_build_status` 方法，让它们按序调用
   共享函数，累计 errors / warnings，并追加自己独有的检查（universe 的
   `has_inconsistent_membership`、taxonomy 的 `has_inconsistent_mappings`、
   universe/taxonomy 的 `temporal_mode` 与 metadata 冲突）。

3. `_as_date` 辅助统一到 `parse_iso_date(value, error_cls=SelfError)`，
   每个契约保留薄薄一层 classmethod 作为向后兼容入口，因为它们被
   `validate_input_boundary` 用来校验 `reference_date`。

4. Spec delta：每个 data contract spec 加一条 Requirement：
   "Contract implementation SHALL reuse shared validator helpers and
   SHALL NOT duplicate presence / metadata / columns / staleness / coverage
   / snapshot_at-mismatch checks inline."

## Impact

- **Affected specs**: `v2-benchmark-data-contract`, `v2-universe-data-contract`,
  `v2-taxonomy-data-contract`（各追加一条 Requirement）。
- **Affected code**:
  - `src/contracts/_shared_validators.py`（新）
  - `src/contracts/benchmark_data_contract.py`（瘦身）
  - `src/contracts/universe_data_contract.py`（瘦身）
  - `src/contracts/taxonomy_data_contract.py`（瘦身）
- **Affected tests**: 无改动（全套回归通过即可）。
- **Backward compatibility**:
  - 所有公共常量（错误码、字段名、`*_REQUIRED_*` 元组）都保持原位置导出。
  - `BenchmarkDataContract.validate_and_build_status` / 其 siblings 的签名
    与返回类型不变。
- **Risk**: 中等偏低。保护措施 = 现有 114 测试 + 全量测试必须在每一步之间
  保持通过。
