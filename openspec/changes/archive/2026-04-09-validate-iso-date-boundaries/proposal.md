# Proposal: validate-iso-date-boundaries

## Why

V2 有两个对外/内部边界接受 ISO 日期字符串，但**只校验非空**，不校验
形状、不校验时间顺序：

1. `CanonicalBacktestContract.validate_input` 对 `evaluation_start` /
   `evaluation_end` 只做 `not str(... or "").strip()`。允许 `evaluation_start
   = "banana"`、`evaluation_end = "2026/02/27"`、`evaluation_start =
   "2026-02-27"` + `evaluation_end = "2026-02-01"` 等明显错误的输入通过。
2. `BenchmarkArtifactPublisher.publish` 对 `start_time` / `end_time`
   同样只检查非空，然后**直接丢给 `D.features`**。垃圾日期落到 qlib 后
   报的错和"日期格式错"完全无关，operator 看到的根因和真实根因相差
   十万八千里。

V1 教训：边界校验缺位 → 错误在传播链深处显现 → operator 半夜 4 点
反向 grep 找元凶。这两个边界是 V2 的"输入大门"，必须自己拒收脏数据，
而不是把脏数据扔给下游再让下游报一个语义无关的错。

## Goals

- 在两处边界**直接**用 ISO 日期解析器校验 `start` / `end`：
  解析失败 → 抛该模块自己的错误类型 + 显式错误信息（含原始字符串）。
- 校验 `start <= end` 闭区间合法（允许相等：单日窗口是合法用例）。
- 复用既有的 `_shared_validators.parse_iso_date(*, error_cls=...)`，
  不新增解析器（避免又一份"自己的 ISO 解析"）。

## Non-Goals

- **不**校验日期与"今天"的关系（时序合理性是另一类问题，由既有
  `has_future_*` flags 处理）。
- **不**改 `BenchmarkArtifactLoader` 的 `_parse_iso_date`（loader 是
  best-effort 容错路径，与 publisher / canonical contract 的"严格
  边界"语义不同）。
- **不**改 `evaluation_start` / `evaluation_end` 的字段类型（仍然是
  `str`，不切到 `date`）—— 切类型会引发跨模块连锁改动，超出本次范围。
- **不**校验 `evaluation_start` / `evaluation_end` 是否落在交易日上
  （那需要在 canonical contract 注入 calendar，是 V2 后续工作）。

## What Changes

1. `src/core/canonical_backtest_contract.py`：
   - 在 `validate_input` 现有的 "非空" 检查之后，调用
     `_sv.parse_iso_date(request.evaluation_start, error_cls=CanonicalBacktestContractError)`
     和 `_sv.parse_iso_date(request.evaluation_end, error_cls=...)`。
     由于 `parse_iso_date` 在空字符串 / None 时返回 `None`，先做非空
     检查再解析的顺序保证非空时一定拿到 `date`。
   - 比较两个 `date` 实例：`if start_d > end_d:` → 抛
     `CanonicalBacktestContractError("evaluation_start (X) must be <= evaluation_end (Y).")`。
   - import 行追加 `from src.contracts import _shared_validators as _sv`。
     `src/core/` 引用 `src/contracts/_shared_validators` 与既有
     `src/core/canonical_backtest_contract.py` 引用 `src/contracts/canonical_boundaries`
     方向一致，未引入新依赖。

2. `src/data/benchmark_artifact_publisher.py`：
   - `publish` 现有 `_require_non_empty_str(start_time, "start_time")` 之后，
     新增内部 staticmethod `_parse_iso_strict(value, field_name)`，调用
     `date.fromisoformat`，失败则抛
     `BenchmarkArtifactPublisherError(f"{field_name} must be ISO date YYYY-MM-DD, got '{value}'.")`。
   - 解析 start / end 后比较：`start_d > end_d` → 抛
     `BenchmarkArtifactPublisherError("start_time '{X}' must be <= end_time '{Y}'.")`。
   - 选择本地小 helper 而不是 `_sv.parse_iso_date`：publisher 已经有
     一组 `_require_*` boundary helpers 自成体系，加一个对称的
     `_parse_iso_strict` 比跨包导入 `_shared_validators`（前缀 `_`
     的"contracts 内部"模块）更符合现有结构。

3. 测试：
   - `tests/governance/test_canonical_backtest_contract.py`：加 4 case
     - bad-format `evaluation_start` 抛错
     - bad-format `evaluation_end` 抛错
     - `start > end` 抛错（用合法 ISO 日期但反向）
     - `start == end` 通过（单日窗口合法）
   - `tests/logic/test_benchmark_publisher_e2e.py` 的
     `BenchmarkPublisherInitGuardTests` 加 3 case（不依赖 qlib bundle，
     因为这些校验在 `_require_canonical_init` 之后但在 qlib 调用之前）：
     - bad-format start_time → 在 publisher 边界 raise，无文件落地
     - bad-format end_time → 同上
     - start > end → 同上
     - **注意**：这些 case 必须先把 canonical init 状态注入（沿用既有
       的 `_CANONICAL_CONFIG` 直注模式），否则会被 init guard 先一步抛错。

## Impact

- **Affected specs**: `v2-canonical-backtest-contract`（追加一条
  Requirement）, `v2-benchmark-artifact-publisher`（追加一条 Requirement）。
- **Affected code**:
  - `src/core/canonical_backtest_contract.py`（+1 import + ~10 行）
  - `src/data/benchmark_artifact_publisher.py`（+1 staticmethod + ~10 行）
- **Affected tests**:
  - `tests/governance/test_canonical_backtest_contract.py`（+4 case）
  - `tests/logic/test_benchmark_publisher_e2e.py`（+3 case）
- **Backward compatibility**:
  - 既有调用方传的全部是合法 ISO 日期（看 archive/2026-04-08 系列
    change 的所有测试代码即可证实），新校验对它们 0 影响。
  - 任何**之前**能通过非空检查但不是合法 ISO 日期的调用方，现在会
    在边界明确报错——这是 bug 修复而非语义变更。
- **Risk**: 极低。改动局限在两个 validate 入口；不改任何字段类型；
  错误类型沿用各自模块既有的异常类。
