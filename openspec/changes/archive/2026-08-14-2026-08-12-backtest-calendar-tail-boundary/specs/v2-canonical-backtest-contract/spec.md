# v2-canonical-backtest-contract — delta for 2026-08-12-backtest-calendar-tail-boundary

## ADDED Requirements

### Requirement: 回测 runner SHALL 在执行前拒绝日历末尾越界的评估窗

`BacktestRunner.run` SHALL 在调用 qlib 回测**之前**校验 `evaluation_end`
在执行日历上的位置，并在以下两种情况拒绝（`BacktestRunnerError`）：

1. **末位越界**：`evaluation_end` 定位到的日历下标为最后一项（含
   `evaluation_end` 晚于日历末位的情况）。qlib 以闭区间表示交易步，
   `get_step_time` 取右端点时无条件读 `calendar[idx + 1]` 的**时间戳**
   （`qlib/backtest/utils.py:131`）—— 末位上该读必然 `IndexError`。
   不变量是"其后尚存一根日历项（作为右界时间戳）"，而非"存在成交用的
   T+1 数据 bar"。
2. **首日回绕**：`evaluation_end` 早于日历首日。qlib 的 `locate_index`
   对此得下标 `-1`，numpy 回绕到 `calendar[-1]`，请求窗口被静默读成
   整个日历 —— 隐式回退，SHALL 拒绝。

`evaluation_end` 的定位 SHALL 按 qlib `locate_index` 的末端语义：
**不晚于 `evaluation_end` 的最后一个日历项**；`evaluation_end` 本身
不是交易日且其后尚有日历项时 SHALL NOT 误判。

#### Scenario: evaluation_end 落在日历末位 — 在 qlib 之前拒绝

- **GIVEN** 执行日历的末位 == `evaluation_end` 定位到的日历项
- **WHEN** 调用 `BacktestRunner.run`
- **THEN** 抛 `BacktestRunnerError`
- **AND** qlib 回测 SHALL NOT 被调用（mock 断言零调用）

#### Scenario: evaluation_end 晚于日历末位 — 同样拒绝

- **GIVEN** `evaluation_end` 晚于执行日历末位
- **WHEN** 调用 `BacktestRunner.run`
- **THEN** 抛 `BacktestRunnerError`（与末位情形同一守卫）

#### Scenario: evaluation_end 是非交易日但其后尚有日历项

- **GIVEN** `evaluation_end` 为周末，其后仍有交易日
- **WHEN** 调用 `BacktestRunner.run`
- **THEN** 守卫放行，SHALL NOT 因非交易日误判

#### Scenario: evaluation_end 早于日历首日 — 拒绝回绕

- **GIVEN** `evaluation_end` 早于执行日历首日
- **WHEN** 调用 `BacktestRunner.run`
- **THEN** 抛 `BacktestRunnerError`，SHALL NOT 让 qlib 把窗口静默
  读成整个日历

### Requirement: 末尾边界报文 SHALL 指名可用的最后一天

末位越界的错误信息 SHALL 同时含有：触发的 `evaluation_end` 值、执行
日历末位日期、**可用的最后一天**（末位前一根 bar；日历不足两项时
SHALL 说明无可用日期）。

三者 SHALL 各自被独立绑定：验证用的日历末位与 `evaluation_end`
SHALL 取不同日期，否则同一个字符串同时满足两处插值，报文即使丢掉
`evaluation_end` 也能通过。

理由：现状是 qlib 抛 `index N is out of bounds for axis 0 with size N`，
operator 无从判断该把窗口收到哪一天 —— 阶段 B 当年是试出来的。报文
不给这个日期，就是把手动回收的负担留在原地。

报文 SHALL 提示 walk-forward 引擎对此类折的自动跳过语义（#327），
使单折 caller 知道两侧行为为何不同。

#### Scenario: 报文含三个日期与 walk-forward 提示

- **GIVEN** 一次落在日历末位的调用
- **WHEN** 守卫拒绝
- **THEN** 报文含 `evaluation_end` 值、日历末位日期、可用的最后一天
- **AND** 报文提及 walk-forward 引擎的跳折语义

### Requirement: 守卫日历 SHALL 与 qlib 执行日历逐参镜像

守卫所查日历 SHALL 经 `BacktestRunner._load_execution_calendar` seam
取得，实现为 `D.calendar(freq=<exchange_config.freq>, future=True)`：

- `future=True` SHALL 显式传入 —— `TradeCalendarManager.reset` 加载的
  正是 future 日历；查了不同的日历等于没守。bundle 无 future 日历时
  qlib 回落 data 日历，守卫**按构造**继承同一回落，无需自行判断。
- freq SHALL 取自 `request.exchange_config.freq`，与 executor 的
  `time_per_step` 同源。
- SHALL NOT 复用印花税权重路径的日历（那是 `future=False` 且带
  start/end 裁剪的另一份）。

seam SHALL 可被测试注入合成日历（沿用
`WalkForwardEngine._load_trading_calendar` 惯例），SHALL NOT 要求
测试 mock `qlib.data.D` 本身。

walk-forward 引擎侧 `_load_trading_calendar` SHALL 保持 data 日历
不变：该日历同时服务窗口生成与 coverage 校验，其 #327 守卫的 data
日历口径是已记录的保守方向（bundle 带 future 日历时最多提早一个
bundle-roll 跳掉末折，绝不放行会崩的折）。

#### Scenario: future 日历长于 data 日历时不误拒

- **GIVEN** seam 返回的执行日历比 data 日历多一根未来 bar，且
  `evaluation_end` 定位到 data 日历末位
- **WHEN** 调用 `BacktestRunner.run`
- **THEN** 守卫放行 —— 判定条件与崩溃条件镜像，无假阳性

#### Scenario: seam 逐参镜像

- **GIVEN** 对 seam 的实现做参数断言
- **WHEN** seam 加载日历
- **THEN** `D.calendar` 收到 `freq=<exchange_config.freq>` 与
  `future=True`

#### Scenario: run() 的 freq 取自请求而非写死

- **GIVEN** 一个 `exchange_config.freq` 被置为哨兵值的请求
- **WHEN** 调用 `BacktestRunner.run`
- **THEN** seam 收到该哨兵值 —— 写死 `"day"` 的实现 SHALL 失败
  （`SUPPORTED_EXCHANGE_FREQUENCIES` 目前只有 `"day"`，不注入哨兵
  则该 SHALL 无法被绑定）

#### Scenario: walk-forward 侧仍读 data 日历

- **GIVEN** 对 `WalkForwardEngine._load_trading_calendar` 做参数断言
- **WHEN** 它加载日历
- **THEN** `D.calendar` 收到的调用**不含** `future` 参数 —— 翻成
  `future=True` 会让窗口生成越到无数据的未来交易日，且不会有任何
  既有测试失败

### Requirement: 守卫 SHALL NOT 因日历不可用而被跳过

守卫加载日历失败时 SHALL 抛具名 `BacktestRunnerError`，SHALL NOT 让
底层异常裸逃出 `run()`，更 SHALL NOT 在加载失败时跳过守卫继续回测 ——
与印花税日历取数同一条"禁止静默回退"规矩。

`evaluation_end` SHALL 用 `CanonicalBacktestContract` 校验时所用的**同
一个** ISO 解析器解析。两个解析器对 ISO 周日期不一致
（`date.fromisoformat("2026-W01-1")` 在 3.11+ 有效，`pd.Timestamp` 抛
`DateParseError`），因此把原始字符串交给另一个解析器会让**合约有效**
的请求以裸 pandas 异常逃出 `run()`。

日历为空时 SHALL 拒绝。该分支是纵深防御而非真实 provider 路径：qlib 的
`CalendarProvider.calendar` 在无边界调用下会先 `_calendar[0]` 越界，
异常在 seam 内抛出并由上面的包装具名 —— 此分支覆盖的是"返回空而不
抛错"的 provider，使守卫永不索引空日历。

#### Scenario: 日历加载失败 → 具名拒绝

- **GIVEN** seam 抛出任意异常
- **WHEN** 调用 `BacktestRunner.run`
- **THEN** 抛具名 `BacktestRunnerError`，含底层异常信息
- **AND** qlib 回测 SHALL NOT 被调用

#### Scenario: ISO 周日期不以裸 pandas 异常逃逸

- **GIVEN** `evaluation_end="2026-W01-1"`（合约校验通过，解析为
  2025-12-29），且执行日历末位正是 2025-12-29
- **WHEN** 调用 `BacktestRunner.run`
- **THEN** 抛具名 `BacktestRunnerError` 的末位拒绝，而非
  `DateParseError`

#### Scenario: 空日历拒绝

- **GIVEN** seam 返回空日历
- **WHEN** 调用 `BacktestRunner.run`
- **THEN** 抛具名 `BacktestRunnerError`，qlib 回测 SHALL NOT 被调用
