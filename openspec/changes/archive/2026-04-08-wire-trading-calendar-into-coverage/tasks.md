## 1. Trading calendar module

- [x] 1.1 新建 `src/data/trading_calendar.py`。
- [x] 1.2 定义 `TradingCalendarError(ValueError)`。
- [x] 1.3 定义 `class TradingCalendar(Protocol)`：`count_trading_days(start: date, end: date) -> int`。
- [x] 1.4 实现 `class StaticTradingCalendar`：构造时排序去重为 tuple；bisect 实现闭区间计数；非 `date` 输入抛 `TradingCalendarError`。
- [x] 1.5 实现 `class QlibTradingCalendar`：懒导入 `qlib.data.D`；首次调用拉取全量日历缓存为 `StaticTradingCalendar`；导入 / 拉取失败抛 `TradingCalendarError`，错误信息显式提示先 `init_qlib_canonical`。

## 2. Benchmark loader 集成

- [x] 2.1 给 `BenchmarkArtifactLoader.load` 增加 `calendar: Optional[TradingCalendar] = None` 参数（位置参数仍排在 `reference_date` 之后）。
- [x] 2.2 `coverage_ratio` 计算：`calendar is not None` → 用 `calendar.count_trading_days`；`None` → 保留 0.63 fallback。
- [x] 2.3 更新代码注释：fallback 注释从 "later change" 改为 "fallback path; pass a TradingCalendar for accurate accounting"。
- [x] 2.4 不改动其他 profile 字段、不改动其他方法。

## 3. 单元测试 — trading calendar

- [x] 3.1 新建 `tests/logic/test_trading_calendar.py`。
- [x] 3.2 `StaticTradingCalendarTests`：覆盖 empty / single date / `end < start` / endpoints inside / 一端在外 / 边界重合 / 跨年 / 输入有重复 / 非 `date` 输入 9 个 case。

## 4. 集成测试 — benchmark loader + calendar

- [x] 4.1 在 `tests/logic/test_benchmark_loader_e2e.py` 加 `BenchmarkLoaderCalendarTests`。
- [x] 4.2 case A：注入 calendar 恰好覆盖 fixture 所有交易日 → coverage_ratio == 1.0 → contract health "ok"。
- [x] 4.3 case B：注入膨胀 calendar（在 fixture 区间内多塞几个虚假交易日）→ coverage_ratio < 配置阈值 → 触发 `ISSUE_INCOMPLETE_COVERAGE` warning。

## 5. Quality Gates

- [x] 5.1 `python -m unittest discover -s tests` 全套测试通过（114 + 新增）。
- [x] 5.2 grep 验证 loader 中 `_A_SHARE_TRADING_DAY_RATIO` 仍存在（fallback 保留）。
- [x] 5.3 grep 验证 contracts 模块没有引入 `trading_calendar` import（边界保持）。

## 6. Spec promotion

- [x] 6.1 新建 `openspec/specs/v2-trading-calendar/spec.md`。
- [x] 6.2 把 `v2-benchmark-artifact-loader` 的 ADDED Requirement 追加到现有 spec 文件。
- [x] 6.3 归档 change 文件夹到 `openspec/changes/archive/2026-04-08-wire-trading-calendar-into-coverage/`。
