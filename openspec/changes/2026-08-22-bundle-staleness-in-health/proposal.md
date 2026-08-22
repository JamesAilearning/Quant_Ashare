# 更新失败连续三晚只说「注意」：队列没问出单侧还剩多少余量

## Why

夜间数据更新连续三晚失败（2026-08-17 / 08-20 / 08-21，均 exit 11），今日待办
队列全程把它归为 `attention`，余量从 14 天掉到 6 天，第一视图始终不变。

一个只会说「注意」的条目，在第一晚和第三晚说的是同一句话。而且没有任何一处
把「距出单拒绝阈值还剩几天」这个数摆出来。

生产运维页 ⑤ 早就在算这个数了（`bundle_freshness`），今日工作台没用它。

## What Changes

- 今日工作台按生产运维页 ⑤ 的同一取法拿到新鲜度裁决
  （`bundle_calendar_tail` + `recommender_integrity_check` + `bundle_freshness`）。
- 待办队列的 `update:failed` 用这个裁决定严重度：出单侧今天会拒 → blocker；
  余量已用掉一半 → blocker；否则 attention，并把余量写进条目详情。

## 本 change **不新造**任何新鲜度判据

这一条是重点。首版（`3a585e3`）在 `bundle_health` 里另写了一份，三个决策全错：

| | 首版写的 | 既有且与出单侧对齐的 |
|---|---|---|
| 时钟 | 固定东八区 | 宿主本地 `date.today()` |
| 边界 | `remaining <= 0` 判红 | `behind > limit`，落后 14 天整**仍接受** |
| 末日 | `ProviderMetadata.coverage_end_date`（偏好 `_fetch_integrity` 身份戳） | `calendars/day.txt` |

三条各自都有守卫钉着（`test_the_clock_matches_the_recommenders_not_the_operators`、
`test_the_boundary_day_is_the_recommenders_boundary`、
`test_the_tail_comes_off_the_recommenders_calendar_file`），首版还把
`BundleHealthSummary` 那个被 `test_dataclass_fields_locked` 钉死的字段集撑破、
打红既有 26 条。

所以本版把 `bundle_health.py` 整个还原，改为**调用**既有判据。

## Capabilities

### Modified Capabilities

- `v2-today-workbench`：待办队列按出单侧自己的新鲜度裁决给更新失败定严重度。

## Impact

- `web/operator_ui/pages/today_workbench.py`（取裁决）
- `web/operator_ui/pages/_today_decision_queue_helpers.py`（定严重度）
- `web/operator_ui/bundle_health.py` **不改**
- 不改任何运行时/出单/交易语义
