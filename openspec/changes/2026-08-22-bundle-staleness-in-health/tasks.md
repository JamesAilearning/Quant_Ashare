# Tasks: 2026-08-22-bundle-staleness-in-health

## 实现

- [x] 今日工作台按生产运维页 ⑤ 的同一取法拿新鲜度裁决
- [x] 队列 `update:failed` 按裁决定严重度（出单会拒 / 余量用掉一半 → blocker）
- [x] 余量写进条目详情
- [x] 裁决未知时严重度不升，也不写余量
- [x] `bundle_health.py` **整个还原**，不新增字段、不改状态语义

## 验证（每条要实测数字）

- [x] 首版打红的 `tests/logic/test_bundle_health_banner.py` 26 条全部恢复
- [x] 新守卫 7 条（含两条结构守卫：页面必须调既有 helper；队列模块不许有陈旧度算术）
- [x] 边界用例直接驱动既有判据：落后 14 天不说「会被拒」，15 天说
- [ ] 全量 `tests/logic` + `tests/governance`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge

## 首版（3a585e3）错在哪 —— 留档

CI 六腿全红 + 三条 P1，根因是**同一件**：仓库里已有一套与出单侧逐字对齐的
新鲜度判据，我没找它，另写了第二份。

| | 首版 | 既有 |
|---|---|---|
| 时钟 | 东八区 | 宿主本地 `date.today()` |
| 边界 | `remaining <= 0` 判红（落后 14 天就说会被拒） | `behind > limit`，14 天整仍接受 |
| 末日 | `coverage_end_date`（偏好 `_fetch_integrity` 身份戳） | `calendars/day.txt` |
| 阈值 | UI 侧钉常量 + 源码对齐测试 | 运行时读 `RecommendationConfig` |
| 完整性闸 | 没考虑 | `integrity_accepted` |

外加两处首版自己造成的破坏：

- 撑破了被 `test_dataclass_fields_locked` 钉死的 `BundleHealthSummary` 字段集
- 把 `status` 改红后流进生产运维页的 `_fresh.usable`（它要求
  `health_status == "ok"`），于是落后正好 14 天时那页会打出「日期虽新…前置
  校验未通过」——**一条自相矛盾的红字**。这一条是 codex 三条之外的第四个缺陷。

## 我的三条过程失误

1. **没先看仓库里已有什么**。这正是本仓库整场在打的「同一件事写两处」，而我
   是在刚帮别人修完这个病之后自己犯的。
2. **改了 `bundle_health.py`，却没跑 `test_bundle_health_banner.py`**——文件名
   就摆在那里。规则应该是：**改哪个模块，先跑与它同名的测试文件**。
3. **主动推迟全量测试**（理由是避免与本机数据补跑并发），而推迟的正是唯一能
   拦住这次的那道闸。「skip 的测试等于没测」这条教训本周已用过一次，这次换了
   个形式又栽。

## 第二轮（codex 一条 P1）

```
守卫 7 → 10 条
  F 队列不再看完整性      抓到 -> test_a_refused_integrity_stamp_blocks_even_with_fresh_dates
  G 未知完整性也当被拒    抓到 -> test_an_unknown_verdict_does_not_fabricate_severity（连带 4 条）
  H 页面不再传完整性      抓到 -> test_the_page_hands_the_queue_every_dimension_of_the_verdict
```

**我算了完整性闸、喂进了裁决，却只把年龄那一半读回来。** 年龄与完整性是出单侧
两道**独立**的门：`_fetch_integrity.json` 缺失/损坏/标了 holey 时
`_assert_bundle_fetch_complete` 拒绝这个 bundle，而 `summarise_bundle_health`
刻意宽容（吞掉坏戳回落 legacy 元数据）可能仍报 ok。于是一个日期很新的 bundle
会显示成「注意」，而出单此刻其实拒绝它。

**没有直接用 `BundleFreshness.usable`**：它在完整性**未知**时也是 False，直接
拿来当判据会把「不知道」升级成「阻塞」，与本 change 已钉住的「未知不许伪造严重
度」冲突。改为 `refuses_today is True or integrity_accepted is False` ——两者都是
**确定的**拒绝。

**H 第一轮没抓到**：我的用例直接调队列函数，页面的接线没人守——把页面调用里的
`bundle_integrity_accepted=` 删掉，9 条照样全绿。补了一条 AST 守卫盯住页面
**实际传了哪些关键字**之后才红。这与上一轮「改 `bundle_health.py` 没跑同名测试」
是同一类疏漏：**测了单元，没测接线**。
