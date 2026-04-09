## 1. Publisher 改动

- [x] 1.1 在 `src/data/benchmark_artifact_publisher.py` 顶部 import `QlibTradingCalendar`。
- [x] 1.2 在 `publish()` 末尾调用 `BenchmarkArtifactLoader.load(..., calendar=QlibTradingCalendar())`。
- [x] 1.3 不改任何其他逻辑。

## 2. 测试

- [x] 2.1 在 `BenchmarkPublisherSnapshotAtDerivationTests` 加 `test_publish_passes_calendar_to_loader`：用 `unittest.mock.patch` 监视 loader 调用，断言 `calendar` kwarg 是 `QlibTradingCalendar` 实例。

## 3. Quality Gates

- [x] 3.1 全套测试通过。
- [x] 3.2 grep 验证 publisher 中存在 `calendar=QlibTradingCalendar()` 行。

## 4. Spec promotion

- [x] 4.1 把新的 Requirement 追加到 `openspec/specs/v2-benchmark-artifact-publisher/spec.md`。
- [x] 4.2 归档到 `openspec/changes/archive/2026-04-08-publisher-uses-trading-calendar/`。
