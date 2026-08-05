# Proposal: sleeve 覆盖界升级 — demonstrated coverage stamp 落盘

## Why

sleeve 覆盖守卫（guard-2，codex P1 on #366）的覆盖界 = 成员构成
**最后一次可证变更日**（`_coverage_bound`：span 起止取 max，2099
哨兵排除）。这是当时唯一可证的界：span 文件格式表达不了"解析器已
看到 X 日快照且确认无变更"，demonstrated coverage 这一事实在工件
里丢失，守卫只能用最保守的代理，且文档明示"churn-free covered
tail is refused too"。

实际撞墙（2026-08-05，重锚定自举 m3' 点火）：快照已解析至
2026-07-31，但 6 月调样（2026-06-30）后 7 月无变更——界停在
06-30，m3' 的 test as_of 2026-07-10 被拒。结构性后果更大：
`retrain_gate.py` ensemble 门同调 `resolve_sleeve_map`（as_of=
window_start），**未来每个季度轮换**的 trailing-quarter 窗都会在
调样间的无变更尾巴里被拒——协议级运转缺口，与本次自举无关也必须修。

## 原则（操作人裁决 A 时钉定）

- **更准确不更宽松**：覆盖 = 解析器**可证看到的快照终点**（它本就
  逐 index 算出 `latest_snapshot`，只是从未落盘）。stamp 声称的
  覆盖若早于 span 中可见的变更 = 工件自相矛盾，拒。
- **无 stamp 回退旧语义**：legacy bundle（无 stamp 文件）行为逐字
  不变；**缺失=合法 legacy，畸形=腐坏必拒**（畸形 stamp 静默回退
  会把腐坏洗成保守，不许）。
- **#393 冻结几何原样保留**：本 change 不触碰任何窗口/门判据。

## What Changes

1. **resolver 落盘 stamp**（`src/data/pit/index_membership.py`）：
   `resolve()` 在写完各 instruments 文件后原子写/合并
   `instruments/membership_coverage.json`：
   `{"schema_version": "membership_coverage_v1", "sleeves":
   {"csi300.txt": {"last_snapshot": "YYYY-MM-DD"}, ...}}`。
   部分重解析（`--indices` 子集）合并保留其他 sleeve 的既有条目；
   既有 stamp 腐坏时 WARN 并以本次条目重建（resolver 是该工件的
   属主，重建即修复路径）。
2. **loader 消费 stamp**（`src/core/attribution_sleeve_loader.py`）：
   per-sleeve 界 = stamp 值（若该 sleeve 有条目）否则 last-change
   （legacy）；stamp < last-change → 拒（自相矛盾）；stamp 文件
   畸形/schema 不符 → 拒；整体 `coverage_end` 仍取各 sleeve 界的
   min（#366 r2 的 per-sleeve 语义不变）。拒绝消息随语义更新。
3. 测试：resolver 侧（stamp 写入=latest_snapshot、子集合并、腐坏
   重建）+ loader 侧（stamp 延界放行 churn-free 尾/缺失回退 legacy
   拒绝/矛盾拒/畸形拒/单 sleeve stamp 混合 min 语义）。

## Non-goals

- 不改 #393 冻结窗口与任何门判据/阈值；
- 不改 span 文件格式与 2099 哨兵语义；
- 不动 daily_update 编排（stamp 由 03 阶段自然产出并随原子 swap
  进 bundle）。

## 落地后序（不在本 change 内执行）

merge → 重跑 `03_resolve_index_membership`（instruments 内容不变，
仅新增 stamp，last_snapshot=2026-07-31）→ 重点火 m3' → 成员门×3
→ 候选 manifest → ensemble 门 → 数字 STOP。
