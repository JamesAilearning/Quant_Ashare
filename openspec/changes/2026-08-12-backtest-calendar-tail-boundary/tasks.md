# Tasks: 2026-08-12-backtest-calendar-tail-boundary

闭合 issue #213（walk-forward 侧已由 #327 修复；本变更覆盖其余全部
caller 的汇合点）。零配置键。

## 1. Runner 守卫

- [x] 新 seam `BacktestRunner._load_execution_calendar(freq)` =
      `D.calendar(freq=freq, future=True)`，docstring 记录与
      `TradeCalendarManager.reset` 的逐参镜像关系
- [x] `run()` 内、qlib.backtest import 块之后插入守卫：
      `bisect_right(cal, evaluation_end) - 1` 定位末端下标
- [x] 下标 == 末位（含 `evaluation_end` 越尾）→ `BacktestRunnerError`，
      报文含 `evaluation_end` / 日历末位 / 可用的最后一天 /
      walk-forward 跳折提示；日历不足两项时说明无可用日期
- [x] 下标 < 0（早于日历首日）→ `BacktestRunnerError`（堵回绕洞）
- [x] 日历加载失败 → 具名 `BacktestRunnerError` hard-fail（不裸抛、
      不跳守卫；印花税日历同规矩）；空日历同拒
- [x] 非交易日 `evaluation_end` 且其后有日历项 → 放行

## 2. 测试（tests/logic/test_backtest_runner.py，patch seam 不 mock qlib.data.D）

- [x] 末位触发 → 抛错，且 qlib backtest **零调用**（sys.modules 哨兵）
- [x] `evaluation_end` 越尾 → 同一拒绝
- [x] 非交易日 + 其后有 bar → 放行（沿 WARN 测试基架跑到 qlib 边界的
      test-stop，断言错误非守卫错误）
- [x] 早于日历首日 → 拒绝
- [x] 报文含三个日期 + walk-forward 提示
- [x] future 日历长于 data 日历、end 在 data 末位 → 放行（镜像无假阳性）
- [x] seam 参数断言：`freq=exchange_config.freq`、`future=True`
- [x] 空日历 → 拒绝
- [x] 存量测试适配：印花税 no-silent-fallback 测试的 mock 改为按
      `future` kwarg 分流（守卫调用放行、印花税调用仍失败），测试
      目标原样保留

## 2b. 对抗审查回修（三镜头 + 逐条反驳，7 条全存活）

- [x] **F1（真缺陷）** `evaluation_end` 改用合约同一解析器
      `date.fromisoformat`：`pd.Timestamp(原串)` 对 ISO 周日期抛
      `DateParseError` 裸逃出 `run()`，而合约用 `date.fromisoformat`
      放行 —— 本改动前该串的首次 pandas 解析发生在印花税取数的
      except 内、被具名包装。已加回归测试并做变异验证（还原成
      `pd.Timestamp(原串)` 时该测试红）
- [x] **F2** 空日历分支标注为纵深防御（真实 provider 会先在 seam 内
      `_calendar[0]` 越界，走"加载失败"具名包装）
- [x] **F3** 补 1 项日历测试，绑定"不足两项时说明无可用日期"
- [x] **F4** 新增 governance 钉 `test_calendar_tail_boundary_sourcing.py`：
      runner seam 必须 `future=True`，walk-forward seam 必须**不带**
      `future`（否决方案 #2 此前无任何测试拦截）
- [x] **F5** 注入哨兵 freq 绑定 `run()` 读的是
      `request.exchange_config.freq` 而非写死 `"day"`
- [x] **F6** 加载失败 / 空日历 / 解析器一致性写入 delta spec；补加载
      失败测试（此前 spec 无、测试也无）
- [x] **F7** 三日期测试改为日历末位 ≠ `evaluation_end`，三值互异并
      各自断言（原测试丢掉 `evaluation_end` 插值仍会绿）

## 2c. blast-radius 回修（第二轮；该轮多数反驳环节死于配额，
##     以下每条均由本人独立复核，未采信未验证声明）

- [x] **P1 CI 红灯** ISO 周日期探针在 Python 3.10 抛 `ValueError`
      而非 skip（`date.fromisoformat` 的扩展解析 3.11 才有），CI 矩阵
      含 3.10（`test.yml:16`）→ 改为 try/except 后 skip
- [x] **F1 只修了一半** 合约的 `parse_iso_date` 先 `.strip()` 再解析；
      原实现用裸 `date.fromisoformat`，故空白填充的**合约有效**输入仍
      裸抛 `ValueError`。改为直接调用合约同一个函数
      （`_sv.parse_iso_date(..., error_cls=BacktestRunnerError)`），
      补空白填充回归测试
- [x] 去重：`test_end_beyond_calendar_tail_refused` 原与三日期测试
      输入完全相同，改为"远超日历末位"的独立场景
- [x] governance 测试补注：断言对象就是 seam 本身传给 `D.calendar`
      的参数，故拦截 `D` 是唯一能表达该断言的边界，非 PR7 反模式
- [x] **已反驳（本人变异验证）** "两个放行测试在守卫过度拒绝时不会
      红"——把守卫改成无条件拒绝后两者**都红**，声明不成立
- [x] **移交** operator UI 快捷预设把 `test_end` 钉在 `cal[-1]`：属实
      但是既有 UI 缺陷（被 `training_guards.py:457` 自身拦下，不达
      runner），已另开任务，不并入本变更

## 3. 收尾

- [ ] `openspec validate 2026-08-12-backtest-calendar-tail-boundary --strict`
- [ ] `pytest`（默认快速套件；E2E 不跑）
- [ ] `ruff check .`；`mypy`（strict 默认）
- [ ] PR 写明 `Closes #213`，并注明 #327 已覆盖 walk-forward 侧

## 4. 点火（并后，操作人，可选）

- [ ] 单折 Pipeline 复现阶段 B 案例：`test_end=` bundle 日历末位 →
      得到指名报错而非 raw IndexError
