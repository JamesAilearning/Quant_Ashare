# Design: 问既有的那套，而不是再写一套

## 为什么这份设计文档在讲「不要做什么」

首版把这件事做成了「新增一套剩余预算算法」，于是同一个问题在仓库里有了两份
答案，而且新的那份在时钟、边界、末日来源三处都与出单侧不一致。三处**各自**
都有测试钉着，只是它们钉的是既有那份，管不到新写的这份。

这正是本仓库整场在打的那个病：**同一件事写两处**。

## 判据只有一处：`_ops_cockpit_helpers.bundle_freshness`

它已经做到：

- `recommender_today()` —— 宿主本地日。注释写明：CN 本地日会在 UTC 宿主上把
  页面推早一天，**恰在阈值边界给出相反结论**。
- `refuses_today = behind > limit` —— 与 `_bundle_is_stale` 逐字一致，落后
  正好 `limit` 天仍然接受。
- `limit = serving_bundle_max_age_days()` —— **运行时读** `RecommendationConfig`，
  不在 UI 侧另写字面量（首版的「钉常量 + 源码对齐测试」因此是多余的）。
- 末日由调用方传入，生产运维页传的是 `bundle_calendar_tail()` 的严格解析结果
  ——即出单侧日历所建之于的同一份 `calendars/day.txt`；字节有歧义时它宁可
  报「无法判定」，也不给一个 qlib 未必认同的绿。
- 还接了完整性闸（`integrity_accepted`），首版完全没考虑这一维。

今日工作台照抄这一整套取法，一个字节都不自己算。

## 严重度规则

```
refuses_today            -> blocker   （事实：出单此刻就会被拒）
headroom <= limit // 2   -> blocker   （策略：见下）
否则                      -> attention（并把余量写进详情）
```

「一半」是**策略**，不是调出来的数字：到那一步，剩下的时间已经不比已经损失的
多。从裁决报出来的 `max_age_days` 推导，阈值一改它跟着改。

它与生产运维页 ⑤ 的 `headroom <= 3` 黄条**不是同一个问题**，所以不共用一个数：
那条描述 bundle 本身「快过期了」；这条描述一次**没人管的失败**正在吃预算。
两者可以、也应该有不同的触发点。

## 「未知」不许被当成任何一边

日历字节有歧义时 `known=False`，`refuses_today` 与 `headroom_days` 都是
`None`。此时严重度**不升**——既不假装新鲜，也不假装陈旧。

## 守卫怎么写

两条独立的结构守卫，都不比数值：

1. 页面必须 **import 并调用**那三个既有 helper（AST 断言）。数值相等在本机
   可能只是巧合——本机就在 +08:00，CN 时钟与宿主时钟一致，带 bug 也能过。
   这条教训是既有测试 `test_the_clock_matches_the_recommenders_not_the_operators`
   的注释里写着的（且记了是变异 C20 抓出来的）。
2. 队列模块的源码里**不许出现** `date.today(` / `datetime.now(` /
   `BUNDLE_MAX_AGE_DAYS` / `timezone(timedelta` —— 任何一样出现，就说明陈旧度
   算术又搬回来了。
