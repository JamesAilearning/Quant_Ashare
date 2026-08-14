# Proposal: 回测日历末尾边界在 runner 层统一守住（纯 RAISE，零配置键）

闭合 issue #213（2026-06-03 开立；外部仓库审核复列为 P1）。

## Why

`evaluation_end` 落在执行日历最后一根 bar 上时，回测抛：

```
BacktestRunnerError: qlib backtest execution failed:
  index 1942 is out of bounds for axis 0 with size 1942
```

**根因**（issue 原文写"需要 T+1 成交 bar"，不准确；照录实测）：
`qlib/backtest/utils.py:131`，`TradeCalendarManager.get_step_time`：

```python
calendar_index = self.start_index + trade_step - shift
return self._calendar[calendar_index], epsilon_change(self._calendar[calendar_index + 1])
```

qlib 以**闭区间**表示交易区间，取右端点时**无条件**读
`calendar[index + 1]` —— 读的是**时间戳**（区间右界），不是数据 bar。
`self._calendar` 是 `Cal.calendar(freq, future=True)` 的完整日历
（`reset()`）；末步 `calendar_index == end_index`，当
`end_index == len(calendar) - 1` 时该读必然越界。

### 已修的一半与剩下的一半

fold-22 类在 **walk-forward 引擎侧已由 PR #327 修复**（`db4614c`）：
`_generate_windows` 拒绝产出末日无尾部余量的折，WARNING 具名，bundle
前滚后该折自然回来。#327 同时消灭了更险的伴生 bug —— per-fold 错误
隔离曾把这个 crash 吞成静默 NaN 占位折（历史上"22/23 valid"无具名
原因）。

但 issue #213 的原话是"**在回测层统一修一次，不要靠每个 caller 手动
回收 test_end**"。#327 只覆盖了一个 caller。现状：

| 路径 | 状态 |
|---|---|
| walk-forward engine | 已守（#327） |
| `src/core/pipeline.py` 单折 | 无守卫（#213 阶段 B 案例） |
| `scripts/regen/replay_frozen_baseline*.py` × 2 | 无守卫 |
| `scripts/retrain_gate.py` | 无守卫 |
| `scripts/eval_frozen_model_oos.py` | 靠写死的 `--guard-end` 默认值绕开 |

`BacktestRunner.run` 是所有路径的汇合点 —— 守卫放在这里，一次覆盖全部。

### 顺带堵死的回绕洞

`Cal.locate_index` 对不在日历内的 `end_time` 做
`calendar[bisect_right(calendar, end_time) - 1]`。当 `evaluation_end`
**早于日历首日**时该下标为 `-1`，numpy 回绕到 `calendar[-1]` ——
请求的窗口被**静默读成整个日历**。同一守卫一条分支拒绝。

## What Changes

- `BacktestRunner.run` 在 qlib 回测执行前加**纯 RAISE 守卫**：
  - `evaluation_end` 定位到执行日历末位（或其后）→ 拒绝，报文指名
    `evaluation_end`、日历末位日期、**可用的最后一天**（末位前一根
    bar），并提示 walk-forward 引擎对此类折的自动跳过语义（#327）；
  - `evaluation_end` 早于日历首日 → 拒绝（回绕洞）。
- 日历经新 seam `_load_execution_calendar` 取得：
  `D.calendar(freq=<exchange_config.freq>, future=True)` ——
  `future=True` 显式传入，与 `TradeCalendarManager.reset` 逐参镜像，
  使守卫的判定条件与崩溃条件**按构造相等**（bundle 无
  `day_future.txt` 时 qlib 回落 data 日历，守卫继承同一回落）。
  seam 形状沿用 `WalkForwardEngine._load_trading_calendar` 的测试
  注入惯例。
- **无配置键、无 fingerprint 变化、不动 resume、无 provenance 新键。**

## 复核中否决的两个候选动作（记录在案）

1. **opt-in 收窗（`clip_to_last_executable`）—— 不做。** 收窗的消费者
   不存在：walk-forward 已在 #327 选定"跳折"，同一代码库再引入第二种
   末尾策略即语义分叉；新键须进 config fingerprint（否则 resume 混折），
   代价是既有可 resume 跑全失效 —— 为假想场景付真实代价。单折 caller
   的出路是照报文显式改日期：请求窗口与实际窗口**始终一致**，连
   provenance 分叉都不产生。将来真有战役需要自动收窗，届时带真实消费者
   再提案。
2. **`_load_trading_calendar` 切 `future=True` 对齐 #327 守卫 ——
   不做。** 那份日历还喂 embargo 回拉、coverage 校验、整个窗口生成；
   切到 future 日历后，一旦 bundle 带上 `day_future.txt`，窗口生成可能
   把**无数据的未来交易日**纳入 fold。#327 守卫用 data 日历是**保守
   方向**（最多提早一个 bundle-roll 跳掉末折，绝不放行会崩的折），
   在此记录该已知保守性，不动代码。

## 同类既存物照录

`scripts/eval_frozen_model_oos.py` 的 `--guard-end=2026-06-12` 默认值
**不改**：它与 `docs/promotion/` 已提交的对比基线 JSON 锁步（"a default
run reproduces the committed origin"），是冻结的对比原点而非待清除的
手动回收；改成日历推导反而破坏复现性。误传 bundle 末位时，本守卫的
指名报错即是兜底。

## Impact

- 既有不触边界的跑：行为逐字节不变（守卫只读日历、只比较下标）。
- 触边界的跑：从 qlib 深处的 raw `IndexError` 变为执行前的具名
  `BacktestRunnerError`，附可用的最后一天。
- 回测前多一次 `D.calendar(freq, future=True)`；qlib 按
  `f"{freq}_future_{future}"` memcache，成本可忽略。

## Non-goals

- 不改 qlib 上游（闭区间语义是 qlib 的设计）。
- 不加任何收窗/回退路径（见"否决记录"1）。
- 不动 walk-forward 引擎侧日历来源（见"否决记录"2）。
- 不改 `evaluation_start` 侧：起点用 `bisect_left` 且越尾会抛带提示的
  `IndexError`，无同类静默洞。
- 不顺手拆 `BacktestRunner.run`（独立重构，需回归基线再签名护航）。
