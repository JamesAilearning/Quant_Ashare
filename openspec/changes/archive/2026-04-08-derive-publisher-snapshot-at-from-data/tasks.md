## 1. Publisher fix

- [x] 1.1 在 `BenchmarkArtifactPublisher.publish` 中 `rows = cls._flatten_close_frame(raw)` 之后、写 csv 之前，计算 `actual_max_date = max(row[0] for row in rows)`。
- [x] 1.2 删除 `effective_snapshot_at = snapshot_at or end_time`。
- [x] 1.3 新逻辑：`snapshot_at is None` → `effective_snapshot_at = actual_max_date`；`snapshot_at is not None and snapshot_at != actual_max_date` → 抛 `BenchmarkArtifactPublisherError`，错误信息包含两个日期。
- [x] 1.4 等于的情况下使用 caller 传入的 `snapshot_at`（与 actual_max_date 字面相同，无差别）。
- [x] 1.5 更新 docstring：`snapshot_at` 描述从 "defaults to end_time" 改为 "defaults to actual max row date in published data; if explicitly supplied, must equal that date or publisher raises".

## 2. Tests

- [x] 2.1 在 `tests/logic/test_benchmark_publisher_e2e.py` 的 `BenchmarkPublisherE2ETests` 加 `test_snapshot_at_is_derived_from_actual_data`：用 `end_time="2026-02-28"`（周六）发布，断言 `result.profile.metadata["snapshot_at"] == "2026-02-27"` 且 contract health == "ok"。
- [x] 2.2 加 `test_explicit_snapshot_at_mismatch_raises`：传 `snapshot_at="2026-02-25"`，断言 publisher 直接抛 `BenchmarkArtifactPublisherError`，断言错误消息同时包含 `2026-02-25` 和实际 max date。
- [x] 2.3 现有 happy-path 测试无需改动（`end_time="2026-02-27"` 是交易日）。

## 3. Quality Gates

- [x] 3.1 `python -m unittest discover -s tests` 全套通过（130 + 0~2 新增，依赖 qlib bundle 的 2 个会被 skip）。
- [x] 3.2 grep 验证 publisher 中不再出现 `snapshot_at or end_time` 模式。

## 4. Spec promotion

- [x] 4.1 把新的 Requirement 追加到 `openspec/specs/v2-benchmark-artifact-publisher/spec.md`。
- [x] 4.2 归档 change 文件夹到 `openspec/changes/archive/2026-04-08-derive-publisher-snapshot-at-from-data/`。
