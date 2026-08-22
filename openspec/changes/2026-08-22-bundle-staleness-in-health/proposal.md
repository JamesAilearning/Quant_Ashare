# 数据包健康不看新鲜度：连续三晚更新失败，第一视图一直是绿的

## Why

出单侧对数据包陈旧有硬闸（`RecommendationConfig.bundle_max_age_days = 14`）：
bundle 的末日落后「外部今天」超过 14 个自然日，`recommend()` 拒绝出单。

而控制台的健康判定**完全不看这件事**。`summarise_bundle_health` 的状态只由
**结构完整性**决定（有 error → error，有 warning → warning，否则 ok）——
`stale` / `age` / `days` 在整个模块里只出现在文档里。

实测（2026-08-22 本机）：

```
数据包健康   末日 2026-08-14   状态 ok   5795 个标的
```

同一时刻的真相是：**bundle 已 8 天没更新，离出单下限只剩 6 天**，而夜间更新
已经**连续三晚失败**（08-17 / 08-20 / 08-21，均 exit 11）。

三晚里操作人的第一视图始终是绿的：

- 健康卡：`ok`
- 今日待办队列：`update:failed` 归类为 `attention`（不是 `blocker`）
- 没有任何一处把「**离下限还剩几天**」这个数摆出来

`BundleHealthSummary` 的 docstring 写着 "One-line description of a qlib
bundle's **freshness state**" —— 名不副实。

## What Changes

- `BundleHealthSummary` 增加 `days_until_stale_floor`：距出单硬闸还剩几个自然日
  （末日未知时为 `None`）。
- 状态在**已经越过硬闸**时降为 `error`——这不是新阈值，是「出单此刻就会被拒」
  这个既成事实。
- 健康文案总是带上这个天数，无论绿黄红。
- 今日待办队列把 `update:failed` 从 `attention` 升为 `blocker`——当剩余天数已
  用掉一半预算时（见 design.md 的取值理由）。

## Capabilities

### Modified Capabilities

- `v2-operator-ui-console`：健康判定纳入新鲜度，并把「距下限天数」作为一等字段。
- `v2-today-workbench`：待办队列按剩余天数升级更新失败项的严重度。

## Impact

- `web/operator_ui/bundle_health.py`（判定与摘要）
- `web/operator_ui/pages/_today_decision_queue_helpers.py`（升级规则）
- `web/operator_ui/pages/today_workbench.py`（把天数传进队列）
- 下限值 14 **不重新发明**：沿用 #454 立下的惯例——UI 侧钉一个具名常量，
  用一条测试读生产者源码做字面对齐，从而不必导入绑定 qlib 的模块。
- 不改任何运行时/出单/交易语义；本 change 只影响「显示什么、排多严重」。
