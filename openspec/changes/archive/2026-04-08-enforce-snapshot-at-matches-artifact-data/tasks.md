## 1. Profile 字段扩展

- [x] 1.1 在 `BenchmarkArtifactProfile` 上新增 `has_snapshot_at_mismatch: bool = False`。
- [x] 1.2 在 `UniverseArtifactProfile` 上新增 `has_snapshot_at_mismatch: bool = False`。
- [x] 1.3 在 `TaxonomyArtifactProfile` 上新增 `has_snapshot_at_mismatch: bool = False`。

## 2. Loader 计算

- [x] 2.1 `BenchmarkArtifactLoader.load` 在 `metadata["snapshot_at"]` 与 csv 末日均可解析时严格比较，不等则置 `has_snapshot_at_mismatch=True`。
- [x] 2.2 该字段写入返回的 `BenchmarkArtifactProfile`。

## 3. 合约校验

- [x] 3.1 `BenchmarkDataContract.validate_and_build_status` 在 `profile.has_snapshot_at_mismatch` 为真时追加 `ISSUE_TEMPORAL_ISSUE`。
- [x] 3.2 `UniverseDataContract.validate_and_build_status` 在 `profile.has_snapshot_at_mismatch` 为真时追加 `ISSUE_TEMPORAL_LEAKAGE`。
- [x] 3.3 `TaxonomyDataContract.validate_and_build_status` 在 `profile.has_snapshot_at_mismatch` 为真时追加 `ISSUE_TEMPORAL_LEAKAGE`。

## 4. 测试

- [x] 4.1 `tests/logic/test_benchmark_loader_e2e.py` 新增：manifest `snapshot_at` 比 csv 末日新一天 ⇒ profile `has_snapshot_at_mismatch=True` ⇒ 合约 health="error"。
- [x] 4.2 同上，但 manifest `snapshot_at` 比 csv 末日旧一天 ⇒ 同样错误。
- [x] 4.3 `tests/governance/test_benchmark_data_contract.py` 新增 unit 测试：直接构造 profile 设置 `has_snapshot_at_mismatch=True` ⇒ 错误集合包含 `ISSUE_TEMPORAL_ISSUE`。
- [x] 4.4 `tests/governance/test_universe_data_contract.py` 新增对应 unit 测试。
- [x] 4.5 `tests/governance/test_taxonomy_data_contract.py` 新增对应 unit 测试。
- [x] 4.6 现有 happy-path 测试不需要修改（现有 fixture 的 `snapshot_at` 已经等于 csv 末日）。

## 5. Quality Gates

- [x] 5.1 全套 unittest discovery 通过。
- [x] 5.2 确认 publisher 默认行为未改动（仍把 `snapshot_at` 设为 `end_time`）。
- [x] 5.3 确认 0.63 trading-day 近似未被本变更触碰（属于下一个 change）。
