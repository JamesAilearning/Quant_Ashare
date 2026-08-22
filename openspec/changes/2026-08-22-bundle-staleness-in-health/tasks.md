# Tasks: 2026-08-22-bundle-staleness-in-health

## 实现

- [x] `days_until_stale_floor()`：把出单硬闸换算成「还剩几个自然日」
- [x] `BundleHealthSummary` 增字段 `days_until_stale_floor`
- [x] 健康文案总是写出这个数（绿黄红都写）
- [x] 预算用尽（`<= 0`）时状态降为 `error`——出单此刻就会被拒，不是新阈值
- [x] 待办队列按剩余预算把 `update:failed` 从 attention 升为 blocker
- [x] 界线 `BUDGET_HALF_SPENT_DAYS` **从下限推导**，不写字面量
- [x] 下限值沿用 #454 惯例：UI 侧钉常量 + 读生产者源码做字面对齐
- [x] 「今天」可注入（默认东八区），测试不依赖机器时钟

## 验证（每条要实测数字）

- [x] 既有 `tests/logic/test_today_decision_queue_helpers.py` 等 39 条不回归
- [x] 新守卫 13 条
- [x] 五条变异全部抓到（每条先断言变异确实落进文件）
- [x] mypy --strict + ruff
- [x] `openspec validate --strict`
- [ ] 全量 `tests/logic` + `tests/governance`（等本机数据重跑结束再跑，避免重活并发）
- [ ] codex CLEAN + CI 绿 → STOP 等 merge

## 实测数字（原样）

```
触发本 change 的现场（2026-08-22 本机）
  bundle 末日 2026-08-14   已 8 天   离出单下限只剩 6 天
  夜间更新连续三晚失败：08-17 22:51 / 08-20 23:09 / 08-21 22:07（均 exit 11）
  健康卡：状态 ok（绿）        待办队列：update:failed 归为 attention
  ——三晚里没有任何一处把「还剩几天」摆出来

既有测试不回归  39 passed / 13 subtests
新守卫          13 passed / 3 subtests

五条变异（每条先断言变异确实落进文件）
  A 健康不再因预算用尽变红    抓到 -> test_budget_exhausted_turns_red
  B 队列不再按预算升级        抓到 -> test_half_the_budget_gone_becomes_a_blocker
  C 界线改成写死的字面量      抓到 -> test_the_escalation_line_is_derived_from_the_floor
  D 下限值与生产者脱钩        抓到 -> test_the_floor_is_pinned_against_the_producer_source
  E 详情里不再写剩余天数      抓到 -> test_the_remaining_days_are_stated_in_the_item
```

**A 与 C 第一轮都没抓到，如实记**：

- A：规格里写了「越过下限→error」这个场景，实现也有，**但我没写测试**——
  变异把判定摘掉，13 条全绿。补 `HealthGoesRedOnlyWhenServingWouldRefuse`
  四条之后才红。
- C：`BUDGET_HALF_SPENT_DAYS = 7` 与 `14 // 2` **当前等值**，只断言相等分辨
  不出「派生」和「恰好写对的字面量」。改为同时断言源码里就是那个推导式
  （与 #444 用 `assertIn("canonical_dir_key(", body)` 钉「委托而非重写」同一
  手法）之后才红。

## 不做

- [x] 不引入交易日历判断「是否落后一个交易日」——剩余预算这个数已足够回答
      操作人的问题，健康判定是渲染路径，不该为此再依赖一份日历数据
- [x] 不改出单侧的闸值，不改任何运行时/交易语义
