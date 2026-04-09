# Proposal: enforce-snapshot-at-matches-artifact-data

## Why

V2 的三个数据契约（benchmark / universe / taxonomy）都要求 manifest 中必须存在
`snapshot_at` 字段，用来声明"这份 artifact 反映的是哪个时点的世界"。但当前
的合约实现**从未**把 `snapshot_at` 和 artifact 里真正最后一行的日期比较过。
结果是：

- Manifest 可以合法地声称 `snapshot_at = 2026-02-27`，而 csv 里最大的一行
  日期其实是 `2026-02-20`。
- Loader 会原样产出一个 profile，合约会返回 `health="ok"`。
- 下游任何依赖 `snapshot_at` 判断"数据是否是最新的"的消费者（包括未来的 runtime
  回测触发器、每日健康检查、operator 面板），都会在不知情的情况下吃下旧数据，
  并且没有任何错误会浮现。

这正是 V2 宪章最想防御的一种失败模式："没有显式报错，但静默发生了时点错位"。
它同时违反三个 V2 原则：
1. **无隐式回退** —— 合约隐式信任 metadata 与数据一致；
2. **官方指标可审计** —— 下游 provenance 会记录一个撒谎的 `snapshot_at`；
3. **无隐藏耦合** —— `snapshot_at` 与实际行的关系仅存在于 publisher 的默认值
   里（`snapshot_at = end_time`），合约和 loader 对此一无所知。

## What Changes

1. **Profile 扩展**：在 `BenchmarkArtifactProfile`、`UniverseArtifactProfile`、
   `TaxonomyArtifactProfile` 上新增 `has_snapshot_at_mismatch: bool = False`
   字段。Loader 负责计算，合约负责消费。
2. **Loader 计算规则**：
   - 当 `metadata["snapshot_at"]` 可解析为 ISO 日期，且 artifact 里至少有一
     行可解析日期时，比较两者；任何不相等都置 `has_snapshot_at_mismatch = True`。
   - 对于 universe / taxonomy 的 `static` temporal_mode（artifact 没有日期列），
     跳过比较，保持 `False`（`snapshot_at` 在该模式下本就是 metadata 孤证）。
3. **合约校验**：三个 `validate_and_build_status` 在发现
   `has_snapshot_at_mismatch` 时追加对应的 temporal 错误码
   （`ISSUE_TEMPORAL_ISSUE` / `ISSUE_TEMPORAL_LEAKAGE`），把 health 降级为
   `error`。
4. **Benchmark loader 实现**：`BenchmarkArtifactLoader._read_csv` 已经记录了
   `max_date`，只需把它与 manifest 的 `snapshot_at` 对比后把布尔值写入
   profile。Universe 和 taxonomy 的 loader 目前尚未存在，本次变更只对合约
   一侧补齐契约表达，并在 benchmark loader 上落实计算；universe/taxonomy 的
   loader 按照新合约在后续 change 里接入。
5. **回归测试**：
   - Benchmark: 两个新的 e2e 测试，分别覆盖 `snapshot_at > max_row_date`（谎称更
     新）和 `snapshot_at < max_row_date`（谎称更旧）两种情形。
   - 三个合约各加一个 unit 级的正向测试：`has_snapshot_at_mismatch=True` ⇒
     health="error"，错误集合包含 temporal 错误码。

## Impact

- **Affected specs**: `v2-benchmark-data-contract`, `v2-universe-data-contract`,
  `v2-taxonomy-data-contract`, `v2-benchmark-artifact-loader`。
- **Affected code**:
  - `src/contracts/benchmark_data_contract.py`
  - `src/contracts/universe_data_contract.py`
  - `src/contracts/taxonomy_data_contract.py`
  - `src/data/benchmark_artifact_loader.py`
- **Affected tests**:
  - `tests/governance/test_benchmark_data_contract.py`
  - `tests/governance/test_universe_data_contract.py`
  - `tests/governance/test_taxonomy_data_contract.py`
  - `tests/logic/test_benchmark_loader_e2e.py`
- **Behavior change**: 原本 health="ok" 的 snapshot_at / 数据不一致场景现在
  会被降级到 health="error"。所有现有 fixture 的 `snapshot_at` 已经等于 csv
  末日（publisher 就是这么写的），因此 happy-path 测试不受影响。
- **Out of scope**:
  - 不引入交易日历，coverage ratio 的 0.63 近似保持不变。
  - 不修改 publisher；publisher 的默认 `snapshot_at = end_time` 行为已经正确，
    本次只是让合约开始真正校验它。
  - 不新增 universe / taxonomy loader；等它们被接入时会继承新契约。
