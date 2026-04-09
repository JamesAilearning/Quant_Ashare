# Proposal: wire-trading-calendar-into-coverage

## Why

Benchmark loader 当前用一个常数近似来估算"应有的交易日数"：

```python
_A_SHARE_TRADING_DAY_RATIO = 0.63
expected_rows = max(int(round(span_days * _A_SHARE_TRADING_DAY_RATIO)), 1)
coverage_ratio = min(outcome.rows / expected_rows, 1.0)
```

代码注释里直接承认这是一个**待替换的占位实现**：

> A tighter calendar-aware coverage check belongs to a later change that wires
> in a trading calendar.

这个近似值带来两类问题：

1. **假阳性 / 假阴性**：A 股全年交易日是 240±5 天，受春节、十一、清明、
   端午、中秋等不规则假期影响。0.63 是粗糙的均值，对短窗口（几周到一个月）
   误差非常大。例如十月第一周（国庆假期）真实交易日为 0，0.63×7 ≈ 4，
   导致一份合规的"长假后才发布"的 artifact 被误判为缺数据；又比如
   春节窗口若被切到了节中段，近似值可能高估覆盖率。
2. **不可审计**：operator 看到 `coverage_ratio = 0.74`，无从知道分母是
   "真实交易日"还是"日历近似值"，无法对照现实判断。

V1 教训："任何 magic number 都会成为下一个事故的罪魁祸首" — 这条注释里
的 `0.63` 已经精确符合这个模式。

## Goals

- **接入真实交易日历**：通过依赖注入把 `TradingCalendar` 提供给
  `BenchmarkArtifactLoader`，让 `coverage_ratio` 的分母变为窗口内**真实
  交易日数**。
- **零硬依赖 qlib**：calendar 是 Protocol，loader 只接受抽象接口；
  `StaticTradingCalendar`（in-memory）用于测试和离线场景；
  `QlibTradingCalendar`（懒导入 qlib.data.D.calendar）用于生产路径。
- **向后兼容**：calendar 参数为**可选**，未注入时回退到既有 0.63 近似
  并附带显式 deprecation 注释。这样既不破坏当前 114 测试，也允许
  调用方按节奏迁移。
- **可重用**：calendar 组件归属 `src/data/`，未来 universe / taxonomy
  loader 接入相同抽象时无需新增包。

## Non-Goals

- **不**改 `stale_days` 的计量单位（仍是日历日）。staleness 阈值的
  trading-day 化是单独议题，因为它会改变 contract 的告警边界。
- **不**接入 universe / taxonomy loader（它们当前还没有 loader 实现）。
- **不**改任何 contract 的错误码或字段结构。
- **不**强制 production 路径必须注入 calendar；这是渐进迁移。
- **不**实现 calendar 的网络抓取。`QlibTradingCalendar` 只复用已经
  init 过的 qlib provider。

## What Changes

1. 新建 `src/data/trading_calendar.py`：
   - `class TradingCalendarError(ValueError)`
   - `class TradingCalendar(Protocol)`，方法 `count_trading_days(start, end) -> int`
   - `class StaticTradingCalendar`：构造时接受可迭代的 `date` 列表，
     内部排序去重存为 tuple；`count_trading_days` 用 bisect 实现 O(log n)
     的闭区间计数。
   - `class QlibTradingCalendar`：懒导入 `qlib.data.D`，首次调用时拉取
     全量日历缓存为 `StaticTradingCalendar`，后续调用复用缓存。导入失败
     或拉取失败时抛 `TradingCalendarError`，错误信息直接指向
     `src.core.qlib_runtime.init_qlib_canonical`。

2. 修改 `src/data/benchmark_artifact_loader.py`：
   - `BenchmarkArtifactLoader.load` 新增可选参数 `calendar: Optional[TradingCalendar] = None`。
   - 计算 `coverage_ratio` 时：
     - `calendar is not None` → `expected_rows = max(calendar.count_trading_days(start, end), 1)`
     - `calendar is None` → 保留既有 0.63 近似，并把现有注释扩写为
       "fallback path; pass a TradingCalendar for accurate accounting"。
   - 不改任何其他 profile 字段。

3. 测试：
   - `tests/logic/test_trading_calendar.py`（新）：
     - `StaticTradingCalendar.count_trading_days` 在空区间 / 单点 / 完整
       区间 / 端点重合 / `end < start` / 起止日不在交易日列表中 / 跨年
       的多个场景。
     - 构造非 `date` 输入抛 `TradingCalendarError`。
   - `tests/logic/test_benchmark_loader_e2e.py`（新增 case）：
     - 注入一个只覆盖 fixture 区间内**所有**真实交易日的
       `StaticTradingCalendar` → coverage_ratio == 1.0，contract health "ok"。
     - 注入一个**多余**交易日的 calendar（人为膨胀分母）→ coverage_ratio
       < 1.0 → 触发 `incomplete_coverage` warning。
     - 不传 calendar → 行为与重构前完全一致（既有 happy-path 测试已经
       覆盖这一路径，无需新增）。

4. Spec 变更：
   - **新增** spec `v2-trading-calendar`（含 ADDED Requirements 描述
     Protocol、Static、Qlib adapter 的契约）。
   - `v2-benchmark-artifact-loader` ADDED Requirement：
     "Loader SHALL accept an optional TradingCalendar and SHALL use it for
     coverage_ratio when supplied; calendar-free fallback remains supported."

## Impact

- **Affected specs**: `v2-trading-calendar`（新建）、`v2-benchmark-artifact-loader`（追加）。
- **Affected code**:
  - `src/data/trading_calendar.py`（新）
  - `src/data/benchmark_artifact_loader.py`（修改 `load` 签名 + coverage 分支）
- **Affected tests**:
  - `tests/logic/test_trading_calendar.py`（新）
  - `tests/logic/test_benchmark_loader_e2e.py`（追加 2 个 case）
- **Backward compatibility**:
  - `BenchmarkArtifactLoader.load` 新增的 `calendar` 参数为可选，
    既有调用点（contract-only 测试 + 现实 caller）不需要修改即可继续运行。
  - 0.63 fallback 行为完全保留，不删除。
  - profile 字段未变。
- **Risk**: 低。calendar 参数完全可选；qlib 适配器是懒导入，不影响
  没有 qlib 的环境。
