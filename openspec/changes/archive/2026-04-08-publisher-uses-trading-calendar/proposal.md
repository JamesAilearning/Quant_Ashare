# Proposal: publisher-uses-trading-calendar

## Why

`wire-trading-calendar-into-coverage` 给 loader 加了可选 calendar 参数，
但 `BenchmarkArtifactPublisher.publish` 内部对 loader 的调用是这样：

```python
profile = BenchmarkArtifactLoader.load(
    artifact_path=str(artifact_file),
    manifest_path=str(manifest_file),
    reference_date=reference_date,
)
```

没有传 calendar，所以 publisher 返回的 `BenchmarkPublishResult.profile`
里 `coverage_ratio` 走的是 0.63 fallback —— 而 publisher 是**真的**
有 qlib 在线、有真实日历可用的环境。这是一个明显的"我们已经修好的工具
没有用上"的死角：上游 loader 接受 calendar，下游 publisher 持有 qlib，
中间的衔接点却仍然走粗糙近似。

## Goals

- publisher 在调用 loader 时**默认注入 `QlibTradingCalendar()`**，
  让 round-trip profile 的 `coverage_ratio` 用真实交易日历分母。
- **零配置**：调用方无需手动构造 calendar；publisher 自己在
  `publish()` 内部 lazy 创建一次 `QlibTradingCalendar` 即可。
- **零跨模块改动**：只动 `benchmark_artifact_publisher.py`。

## Non-Goals

- 不改 loader API。
- 不暴露 calendar 注入参数给 publisher 的调用方（单一职责：publisher
  存在就是为了"用 qlib 写出好 artifact"，calendar 选择不是它的接口）。
- 不缓存 calendar 跨多次 `publish()` 调用（每次 publish 都新建一个
  `QlibTradingCalendar`，内部首次调用时拉取，单次 publish 内复用）。
  跨调用缓存是过度设计；qlib calendar 拉取一次只有数千条记录，成本极低。

## What Changes

1. `BenchmarkArtifactPublisher.publish` 中：
   ```python
   from src.data.trading_calendar import QlibTradingCalendar
   ...
   profile = BenchmarkArtifactLoader.load(
       artifact_path=str(artifact_file),
       manifest_path=str(manifest_file),
       reference_date=reference_date,
       calendar=QlibTradingCalendar(),
   )
   ```
   `QlibTradingCalendar` 是懒导入 qlib 的，所以这条 import 不会破坏
   contract-only 测试的"无 qlib"假设——但 publisher 本来就需要 qlib，
   这不是新的耦合。

2. 测试：在 `BenchmarkPublisherSnapshotAtDerivationTests`（mocked
   单元测试）里加一个 case，验证 publisher 把 calendar 传给 loader：
   - 用 `unittest.mock.patch` 监视 `BenchmarkArtifactLoader.load` 的
     调用，断言 `calendar` 关键字参数不是 `None` 且类型是
     `QlibTradingCalendar`。
   - 不需要 calendar 真的能 fetch（qlib 仍是 fake stub），因为本测试
     只关心 publisher 是否"传了"，不关心 calendar 内部行为。

3. Spec 追加 Requirement：
   "Publisher SHALL inject a TradingCalendar (default
   `QlibTradingCalendar`) into its internal loader call so the
   round-trip profile's coverage_ratio is calendar-accurate."

## Impact

- **Affected specs**: `v2-benchmark-artifact-publisher`（追加一条 Requirement）。
- **Affected code**: `src/data/benchmark_artifact_publisher.py`（+2 行 import + 1 行 kwarg）。
- **Affected tests**: `tests/logic/test_benchmark_publisher_e2e.py`（+1 mocked case）。
- **Backward compatibility**: 
  - publisher 的对外签名不变。
  - 返回的 `profile.coverage_ratio` 数值在 `end_time` 完整覆盖
    交易日的窗口下与原行为一致（calendar 算出 N，分子也是 N，
    比值 1.0；fallback 算出 round(span*0.63)，分子是 N，比值也是
    min(N/round(span*0.63), 1.0) = 1.0 当数据足够稠密时）。
  - 在窗口包含长假期的情况下，新行为更准确（fallback 会因为分母
    膨胀产生 incomplete_coverage 假阳性）。
- **Risk**: 极低。publisher 已经 hard-require qlib init；
  `QlibTradingCalendar` 是它合法的 peer 组件。
