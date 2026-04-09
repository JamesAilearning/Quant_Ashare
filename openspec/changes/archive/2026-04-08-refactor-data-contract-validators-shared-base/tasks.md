## 1. Shared Module

- [x] 1.1 创建 `src/contracts/_shared_validators.py`。
- [x] 1.2 定义 Protocol 最小接口：`_HasPresenceFlags`, `_HasMetadata`, `_HasColumns`, `_HasStaleness`, `_HasCoverage`, `_HasSnapshotEnd`, `_HasSnapshotAtMismatch`, `_HasFutureFlags`。
- [x] 1.3 实现 `parse_iso_date(value, *, error_cls)`。
- [x] 1.4 实现 `normalize_columns(values)` → `tuple[str, ...]`。
- [x] 1.5 实现 `dedupe(values)` → `tuple[str, ...]`。
- [x] 1.6 实现 `check_presence(profile, *, missing_artifact_code, missing_manifest_code)`。
- [x] 1.7 实现 `check_metadata_fields(profile, required_fields, *, schema_mismatch_code)` 返回 `(present, missing, errors)`。
- [x] 1.8 实现 `check_required_columns(normalized_columns, required_columns, *, schema_mismatch_code)`。
- [x] 1.9 实现 `check_staleness(profile, threshold, *, stale_code)`。
- [x] 1.10 实现 `check_coverage(profile, min_ratio, *, incomplete_coverage_code)`。
- [x] 1.11 实现 `check_temporal_basic(profile, reference_date, *, temporal_code, error_cls, future_flags)`。
- [x] 1.12 实现 `check_snapshot_at_mismatch(profile, *, temporal_code)`。
- [x] 1.13 实现 `aggregate_health(errors, warnings)`。

## 2. Benchmark 契约重构

- [x] 2.1 导入共享模块。
- [x] 2.2 `validate_and_build_status` 改为按顺序调用共享函数。
- [x] 2.3 保留 benchmark 特有的错误码常量导出不变。
- [x] 2.4 运行 benchmark 相关测试必须通过：`tests/governance/test_benchmark_data_contract.py`, `tests/logic/test_benchmark_loader_e2e.py`。

## 3. Universe 契约重构

- [x] 3.1 导入共享模块。
- [x] 3.2 `validate_and_build_status` 按顺序调用共享函数 + 保留本地 `temporal_mode` 与 metadata 冲突检查 + 保留 `has_inconsistent_membership` 检查。
- [x] 3.3 运行 `tests/governance/test_universe_data_contract.py` 通过。

## 4. Taxonomy 契约重构

- [x] 4.1 导入共享模块。
- [x] 4.2 `validate_and_build_status` 按顺序调用共享函数 + 保留本地 `temporal_mode` 冲突 + 保留 `has_inconsistent_mappings` 检查。
- [x] 4.3 运行 `tests/governance/test_taxonomy_data_contract.py` 通过。

## 5. Quality Gates

- [x] 5.1 `python -m unittest discover -s tests` 全套 114 测试通过。
- [x] 5.2 `git diff --stat` 人工检查：三个契约文件行数明显下降。
- [x] 5.3 确认没有新的公共常量（`grep` 搜索共享模块没有被别处导入常量）。
- [x] 5.4 确认错误码字符串没有变动：`grep -E "ISSUE_" src/contracts/` 结果与重构前一致。
