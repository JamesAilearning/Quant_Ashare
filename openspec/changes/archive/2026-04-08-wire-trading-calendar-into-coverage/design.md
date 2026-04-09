# Design: wire-trading-calendar-into-coverage

## 1. Why a Protocol, not a base class

`TradingCalendar` 暴露的最小接口只有一个方法：`count_trading_days(start, end) -> int`。
适配器之间没有共享状态、没有共享构造函数语义，继承零收益。Protocol 让
loader 接收"任何提供该方法的对象"——包括测试里临时手写的小 stub——
而不需要进口任何基类。

## 2. Why calendar belongs to `src/data/`, not `src/contracts/` or `src/core/`

- **不属于 contracts**：contracts 是纯校验，分母怎么算是 loader 的责任，
  contracts 只看最终的 `coverage_ratio` 数字。
- **不属于 core**：core 目前只放运行时初始化和 canonical backtest 入口。
  把 calendar 放进 core 会让 core 隐式承担"数据来源"职责。
- **属于 data**：calendar 是 loader 的输入 plumbing，未来 universe 和
  taxonomy loader 也会消费同一个 calendar。`src/data/trading_calendar.py`
  与 `src/data/benchmark_artifact_loader.py` 横向并列，依赖方向清晰。

## 3. Why `StaticTradingCalendar` exists in production code, not just tests

- 它是 `QlibTradingCalendar` 的内部缓存载体（`_fetch()` 一次返回的就是它）。
- 它让"calendar 持久化到磁盘 / 通过外部源加载"这类未来变体只需要写一个
  小工厂函数返回 `StaticTradingCalendar`，无需再创建第三个适配器。
- 它让单元测试可以直接构造确定性日历，不必 patch qlib。

## 4. Bisect over set lookup

`StaticTradingCalendar` 用排序后的 tuple + `bisect_left/right` 实现闭区间
计数：
- `O(log n)` 查询，比 `sum(1 for d in dates if start <= d <= end)` 的 O(n)
  更适合"日历有数千条但 query 区间小"的实际负载。
- 排序后存为 tuple（不可变），保证对象生命周期内日历不会被外部突变。

构造时显式 `sorted(set(...))` 去重 + 排序，调用方可以传任意顺序的列表。

## 5. Inclusive vs exclusive interval semantics

`count_trading_days(start, end)` 语义为**闭区间** `[start, end]`。
原因：benchmark loader 把 `snapshot_start` 和 `snapshot_end` 都当作真实
出现过的交易日，闭区间最自然且和 V1 共识一致。`end < start` 返回 0
（不抛错），这样 loader 不需要为反向区间写守卫代码。

## 6. QlibTradingCalendar 的懒导入与缓存

- **懒导入 qlib**：`from qlib.data import D` 只在首次 `count_trading_days`
  调用时执行。这样导入 `src.data.trading_calendar` 不会触发 qlib 加载，
  没有 qlib 的环境也能跑 contract-only 测试。
- **首次缓存**：第一次调用拉取全量日历（A 股一万出头条），转换为
  `StaticTradingCalendar`，后续调用走 bisect。重复的 query 不再回到
  qlib provider。
- **错误码**：导入失败 / 拉取失败一律 `TradingCalendarError`，错误信息
  显式写明"先调用 `init_qlib_canonical`"。这是可被 operator 直接读懂的
  操作指引，而不是模糊的 `RuntimeError`。

## 7. 为什么保留 0.63 fallback

- **零行为变化是本次 change 的 hard rule**。删除 fallback 等于强制所有
  既有调用点（包括 21 个 contract-only 测试）现场注入 calendar，违反
  "纯增量改动"原则。
- **fallback 仍是合法降级路径**。某些诊断脚本只关心 "csv 行数有没有反常
  断层"，不需要交易日精度；强制它们构造 calendar 是过度工程。
- **可以单独 deprecate**：未来某次 change 可以把 fallback 标记为 warning
  并在 spec 里加 SHALL；这次只做加法。

## 8. 测试拓扑

```
tests/logic/test_trading_calendar.py
    StaticTradingCalendarTests
        - empty calendar
        - single date
        - end before start
        - both endpoints inside
        - both endpoints outside (one before, one after)
        - one endpoint exactly on boundary
        - cross-year span
        - duplicates in input
        - non-date input rejected

tests/logic/test_benchmark_loader_e2e.py (新增)
    BenchmarkLoaderCalendarTests
        - calendar matching csv exactly -> coverage 1.0, contract ok
        - calendar inflated (extra trading days outside csv rows) -> coverage < 1.0
            -> ISSUE_INCOMPLETE_COVERAGE warning
        - calendar=None -> identical to legacy fallback (sanity)
```

`QlibTradingCalendar` 的 happy-path 不在本次单元测试覆盖范围内（依赖
真实 qlib provider），但导入失败路径用 monkeypatch 验证错误消息形状。

## 9. 不打破的边界

- loader 仍然不调用 `qlib.init`。
- contracts 不接触 calendar。
- `_shared_validators.py` 不接触 calendar。
- 错误码字符串无变更。

## 10. Trade-offs

- **优点**：消灭一个 magic number；为 universe / taxonomy 后续接入预留
  统一的 plumbing；测试可以构造确定性日历。
- **代价**：loader API 多了一个可选参数；多了一个文件。
- **接受**：API 加可选参数是最低代价的兼容性扩展；新文件在 src/data/
  下与既有 loader 并列，不增加目录层级。
