# Proposal: derive-publisher-snapshot-at-from-data

## Why

Change `enforce-snapshot-at-matches-artifact-data` (already archived)
强制 loader 把 manifest 中的 `snapshot_at` 与 csv 实际最大行日期做严格相等
比对，不一致即视作 `temporal_issue` 错误。

但 `BenchmarkArtifactPublisher.publish` 当前的实现是：

```python
effective_snapshot_at = snapshot_at or end_time
```

也就是说，**publisher 直接把请求参数 `end_time`（或调用方手填的
`snapshot_at`）写进 manifest，从来不去看 qlib 实际返回的 csv 末行**。
这是 change 4 没有同时治理的对称漏洞。它会在以下场景里产生"我们自己的
publisher 写出来的 artifact 自己的 loader 拒收"的尴尬结果：

1. **`end_time` 落在节假日 / 周末**：例如 `end_time = "2026-02-28"`
   （周六），qlib provider 返回的最后一行是 `2026-02-27`，但
   manifest 写入的 snapshot_at 是 `2026-02-28` → 严格相等失败
   → contract 报 `temporal_issue`。
2. **`end_time` 落在停牌日**：同理。
3. **调用方误填 `snapshot_at`**：用户手动覆盖时如果填了与实际数据
   不一致的日期，publisher 静默接受、loader 静默否决，
   错误信息出现在 contract 层而不是 publisher 层，定位成本高。

V1 教训："任何允许 publisher 与 loader 对同一个字段持有不同事实的接口，
最终都会让运维半夜 4 点起床查 manifest"。这条 change 要把这个不对称
彻底补上。

## Goals

- **Publisher 永远写真**：`snapshot_at` 必须等于 csv 实际最大行日期。
  没有例外路径，没有"反正后面会校验"的便宜行事。
- **显式校验调用方传参**：当 caller 传了 `snapshot_at` 参数，publisher
  SHALL 校验它与 qlib 数据实际最大日期相等；不等则**在 publisher 层**
  抛 `BenchmarkArtifactPublisherError`，错误信息直接告知差异。
- **不破坏现有 round-trip 测试**：现有测试用 `TEST_END = "2026-02-27"`
  恰好是交易日，2026-02-27 既是 end_time 也是 max row date，本次修改
  对它仍然 ok。
- **零跨模块改动**：只动 `benchmark_artifact_publisher.py` 一个文件
  和它的测试。loader、contract、shared validators 均不动。

## Non-Goals

- **不**动 universe / taxonomy publisher（它们尚未实现）。
- **不**动 publisher 是否调用 qlib.init 的边界。
- **不**改 `snapshot_at` 字段的语义（仍是"产物最大行日期"）。
- **不**给 publisher 加 calendar 注入（独立小改动，可放入下一次 change
  或留给未来 universe loader 接入时一并考虑）。

## What Changes

1. `BenchmarkArtifactPublisher.publish`：
   - 在 `_flatten_close_frame(raw)` 之后、写 csv 之前，从 rows 中
     提取**真实最大日期** `actual_max_date = rows[-1][0]`（rows 已按
     日期升序排序）。
   - 计算 `effective_snapshot_at`：
     - `snapshot_at is None` → 用 `actual_max_date`（**不再使用 end_time
       做 fallback**）。
     - `snapshot_at is not None` → 与 `actual_max_date` 字符串比对，
       不等则抛 `BenchmarkArtifactPublisherError`，错误信息形如
       `"snapshot_at='X' does not match actual max row date 'Y' in qlib data ..."`。
   - 删除"`snapshot_at` 默认为 `end_time`"这条注释，替换为新的语义。

2. 测试 `tests/logic/test_benchmark_publisher_e2e.py`：
   - 新增 `test_snapshot_at_is_derived_from_actual_data`：用
     `end_time = "2026-02-28"`（周六）调用 publisher，断言
     manifest 里 `snapshot_at == "2026-02-27"`，并且 round-trip
     contract health 仍是 "ok"。
   - 新增 `test_explicit_snapshot_at_mismatch_raises`：手动传一个
     与实际不符的 `snapshot_at`，断言 publisher 抛
     `BenchmarkArtifactPublisherError`，**而不是**走到 loader / contract
     才报错。
   - 这两个新 case 同样受 qlib 本地 bundle 可用性 skip 保护。

3. Spec：给 `v2-benchmark-artifact-publisher` 加一条 Requirement：
   "Publisher SHALL derive `snapshot_at` from the actual maximum row
   date in the published csv, and SHALL reject mismatched explicit
   `snapshot_at` arguments at the publisher boundary."

## Impact

- **Affected specs**: `v2-benchmark-artifact-publisher`（追加一条 Requirement）。
- **Affected code**: `src/data/benchmark_artifact_publisher.py`。
- **Affected tests**: `tests/logic/test_benchmark_publisher_e2e.py`（追加 2 case）。
- **Backward compatibility**:
  - 调用方**没有**传 `snapshot_at` 时：原行为是写 `end_time`，新行为
    是写 `actual_max_date`。在 `end_time` 是交易日的常见情况下两者
    相等，对调用方无差别；在 `end_time` 不是交易日时，旧行为产生的
    artifact 会被 change 4 之后的 loader 拒收，新行为则是正确的。
    严格说这是 bug 修复而非语义变更。
  - 调用方传了 `snapshot_at`：原行为是无脑接受；新行为是严格校验。
    若调用方一直在传与实际数据一致的值，则没有差别；若调用方一直
    在传错的值，新行为会**更早**报错。在 publisher 层报错比在 loader
    层报错更可定位，这是收益不是回归。
- **Risk**: 低。所有改动局限在 publisher 一个文件；现有 publisher 测试
  仍然通过；新增的 2 个测试都受 qlib bundle skip 保护。
