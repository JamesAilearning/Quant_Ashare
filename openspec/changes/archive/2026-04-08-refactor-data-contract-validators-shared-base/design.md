# Design: refactor-data-contract-validators-shared-base

## Decision 1: 无状态纯函数，而非抽象基类

**选项 A（否决）**：抽象基类 `DataArtifactContractBase` + 模板方法。

**选项 B（采用）**：一个 `_shared_validators.py` 模块，导出无状态纯函数，
三个契约通过组合调用。

**理由**：

- 三个契约的 Input / Profile / Status 数据类互不相同；抽象基类会被迫使用
  `Any` 或泛型参数化，带来类型碎片。
- 抽象方法 `_build_status_body(...)` 的参数列表会演化成一堆 `Mapping` 和
  `Optional`，可读性明显差于三个契约各自按顺序调用几个顶级函数的版本。
- 纯函数便于单独测试 / 模拟；即便目前不为共享模块本身写新测试，未来若要
  加也无需构造虚拟子类。
- V2 的设计偏好是"**显式优于隐式**"。继承层次会把一些默认行为藏在父类里，
  违反这条原则。

## Decision 2: 错误码作为参数传入，不在共享模块硬编码

三个契约的错误码字符串虽然部分相同，但 temporal 错误码区别（
`temporal_issue` vs `temporal_leakage`），`missing_artifact_file`
也可能在未来被某个契约重命名。共享函数签名统一采用 keyword-only 的
错误码入参：

```python
def check_presence(
    profile: _HasPresenceFlags,
    *,
    missing_artifact_code: str,
    missing_manifest_code: str,
) -> list[str]:
    ...
```

这样：
- 共享模块**不引入**新的公共常量；
- 每个契约仍是自己错误码的唯一真相来源；
- 未来某个契约想换码，只改自己那一行调用点。

## Decision 3: 以鸭子类型最小接口定义 Profile 约束

共享函数的参数类型使用 `typing.Protocol` 定义最小接口，例如：

```python
class _HasPresenceFlags(Protocol):
    artifact_present: bool
    manifest_present: bool
```

只声明共享函数**真正读取**的字段。好处：
- 三个具体 Profile 数据类无需继承任何抽象类；
- mypy 在某个函数悄悄多读一个字段时会立刻报错；
- 保持零运行时依赖。

## Decision 4: `snapshot_at` 不匹配检查作为独立共享函数

上一个 change 在三个契约里分别加了几乎相同的 4 行：

```python
if profile.has_snapshot_at_mismatch:
    errors.append(ISSUE_TEMPORAL_*)
```

虽然短，但这是重构的典型"种子"。单独抽成 `check_snapshot_at_mismatch`，
错误码通过参数注入。未来若想加容忍度或策略切换，改一处即可。

## Decision 5: `metadata.temporal_mode` 冲突检查**不**进共享层

Universe 和 taxonomy 都有这段：

```python
mode_from_metadata = str(metadata.get("temporal_mode", "")).strip().lower()
if mode_from_metadata and mode_from_metadata != request.temporal_mode:
    errors.append(ISSUE_SCHEMA_MISMATCH)
```

这段涉及 `request.temporal_mode` —— 不是 Profile 字段而是 Input 字段，
每个契约都有自己的 `temporal_mode` 允许值枚举。把它抽进共享层会迫使
共享函数引用 Input 类型，反而更耦合。维持在契约本体内，代价只是两处
相同的 3 行，不值得抽。Benchmark 没有这段，它是纯 benchmark-code 契约。

## Decision 6: `_as_date` 统一到共享 `parse_iso_date`

共享 `parse_iso_date(value, *, error_cls)` 接收错误类作为参数；每个契约
保留 `classmethod _as_date(cls, value)` 作为 thin wrapper，继续被
`validate_input_boundary` 调用。不破坏既有调用点。

## Decision 7: `dedupe` 与 `aggregate_health` 要抽

这两个最小但出现率 100% 的工具：
- `dedupe(seq)` → `tuple(dict.fromkeys(seq))`
- `aggregate_health(errors, warnings)` → `"error" | "warning" | "ok"`

虽然每个只有一两行，正是共享层存在的意义——保证三处决策语义永远同步。

## Decision 8: 顺序与副作用

共享函数不持有状态、不抛异常（除非 `parse_iso_date` 拿到非法字符串，此时
沿用调用方传入的 `error_cls`）。所有函数返回 `list[str]`（要追加的错误码）
或元组，调用方累积。这样调用顺序与现有实现完全一致，避免重构引入隐蔽
行为变化。

## Decision 9: 重构验证只看测试矩阵

无新增测试。本 change 的"正确性定义"等价于：

> 重构前后，`python -m unittest discover -s tests` 的测试集合与逐项
> pass/fail 结果完全相同。

现有 114 个测试已经覆盖了 presence / metadata / schema / stale /
coverage / temporal / snapshot_at mismatch / static-mode 豁免 / happy
path 所有分支，重构过不了这些就等于错了。

## 不属于本变更的事项

- 不接入交易日历（`0.63` 比例保持）；
- 不修改错误码字符串或导出常量；
- 不动 publisher / loader / canonical backtest contract；
- 不引入共享 Error 基类（继续保留三个独立 `*ContractError`）。
