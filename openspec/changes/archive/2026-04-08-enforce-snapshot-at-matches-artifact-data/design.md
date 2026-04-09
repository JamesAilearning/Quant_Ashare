# Design: enforce-snapshot-at-matches-artifact-data

## Decision 1: 计算责任放在 loader，合约只消费布尔

**选项 A**：合约自己读 `metadata["snapshot_at"]` 并与 `profile.snapshot_end`
比较。

**选项 B（采用）**：loader 计算并暴露 `has_snapshot_at_mismatch: bool`，
合约只读这个布尔。

**理由**：合约模块的设计哲学是"只看 profile，不解析 metadata 字符串"。
它消费 `has_future_data`、`has_future_known_metadata`、
`has_inconsistent_membership` 这类布尔字段，把"如何从原始数据导出布尔"
留给 loader。这是已经建立的对称性，本次变更应保持。一旦合约开始绕过
profile 直接解析 metadata，未来的 universe/taxonomy loader 还要再实现一遍
同样的逻辑，违反 DRY。

## Decision 2: "不一致"的判定是严格相等，不是 ≤

**选项 A**：`has_snapshot_at_mismatch = snapshot_at != max_row_date`。

**选项 B**：`has_snapshot_at_mismatch = snapshot_at > max_row_date`（只
拒绝撒谎更新）。

**采用 A（严格相等）**。理由：

- `snapshot_at > max_row_date`（manifest 谎称更新）是经典的 stale-data
  漏报，必须拒绝。
- `snapshot_at < max_row_date`（manifest 谎称更旧）也是漏洞：意味着 csv
  在 manifest 写入之后被改动过、追加过，或两个文件不是同一次发布的产物。
  对一个声称"无隐藏耦合、可审计 provenance"的系统而言，这种状态绝不能被
  报告为 ok。
- 对 publisher 来说，这两者都不会自然发生（publisher 把 `snapshot_at`
  设为 `end_time`，并在该时间戳后不会再追加行），因此严格相等不会破坏
  既有 happy-path。

## Decision 3: tolerance = 0 天

不引入 `snapshot_at_tolerance_days` 之类的旋钮。理由：

- 任何"允许 ±N 天"的容忍度本身就是隐式行为，必须显式地放进合约输入；
- 当前没有任何业务场景要求容忍度 > 0；
- 如果将来真的需要（例如上游 vendor 周末发数据），届时再做一个 explicit
  spec change 引入字段 + 默认 0 + governance 测试，远比现在偷偷加一个
  默认值好。

## Decision 4: 对 universe / taxonomy 的 static 模式跳过比较

`static` temporal_mode 下，artifact 不含日期列（`UNIVERSE_REQUIRED_MODE_COLUMNS[static] = ()`），
"实际数据末日"无法定义。在该模式下 `snapshot_at` 本就只是 metadata 的孤
证，没有第二来源可对照，因此 `has_snapshot_at_mismatch` 必须保持
`False`。

这不是 V2 原则的妥协 —— `static` 模式的语义本来就是"这份名单不带时间维度"，
点位时间风险落在上层调用方（必须显式选择 `snapshot_at` 作为有效日期）。
在该模式下加额外检查只会产生噪声，无法捕捉真实漏洞。

## Decision 5: 错误码复用而非新增

Benchmark 复用 `ISSUE_TEMPORAL_ISSUE`；universe / taxonomy 复用
`ISSUE_TEMPORAL_LEAKAGE`。理由：

- 这两个错误码本就承载"声明的时间与现实不一致"的语义；
- 新增 `snapshot_at_mismatch` 这种细粒度错误码会让 operator 面板出现两个
  含义重叠的桶，反而更难辨别；
- 真正想区分"数据末日落后/超前"或"manifest 未来日期"时，operator 仍可读
  `profile.snapshot_end` + `metadata["snapshot_at"]` + 错误集合自行判断。

## Decision 6: Loader 实现细节

`BenchmarkArtifactLoader._read_csv` 已经在循环中维护 `max_date`，只需在
`load()` 方法的尾部加入：

```python
has_snapshot_at_mismatch = False
manifest_snapshot_at_text = str(metadata.get("snapshot_at", "")).strip()
if manifest_snapshot_at_text and outcome.snapshot_end is not None:
    manifest_snapshot_at = cls._parse_iso_date(manifest_snapshot_at_text)
    artifact_max_date = cls._parse_iso_date(outcome.snapshot_end)
    if (
        manifest_snapshot_at is not None
        and artifact_max_date is not None
        and manifest_snapshot_at != artifact_max_date
    ):
        has_snapshot_at_mismatch = True
```

并把它作为新参数传给 `BenchmarkArtifactProfile`。这意味着只有当**两个值
都可解析**时才比较；任何一边缺失或格式错都不会触发不匹配标志（缺失会被
独立的 schema_mismatch / missing_manifest 错误码捕捉）。

## 不属于本变更的事项

- **交易日历 / coverage ratio 改造** —— 已列入下一个 change。
- **Adjust mode 语义化** —— 已列入下一个 change。
- **Universe / taxonomy 实体 loader** —— 它们目前尚未存在；当被引入时
  会自然按本变更的新合约填字段。
- **新增 snapshot_at_tolerance_days 字段** —— 显式拒绝（见 Decision 3）。
