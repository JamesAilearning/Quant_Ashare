# Proposal: 生产运维只读驾驶舱 —— 一屏看清「现在能不能出单、什么该做了」

## Why

生产自 2026-08-05 起服务 csi800 N5 三成员 ensemble。维持它正常运转需要操作人
周期性地回答五个问题，而**今天这五个答案分散在五个互不相干的地方**，没有一处
汇总，也没有一处告诉操作人「现在该做什么」：

| 问题 | 今天要去哪找 |
|---|---|
| 生产在服务哪个模型？ | 今日推荐页顶横幅（#430 刚做） |
| 四份 gate 工件各是什么状态？ | `docs/research/evidence/.../bootstrap_v2_gates/*.json` 手动读 JSON |
| recert 是 WIN 还是 LOSE？还剩多久？ | 跑 `git log` + 手算 15 个月 |
| 季度重训该做了吗？ | **无处可查**（见下） |
| 数据 bundle 新不新？ | 作业页/结果页横幅（只说 ok，不说落后几天） |

其中两条值得单独说：

**① 季度重训在仓库里没有任何机器可读的到期锚。** 全仓只有散文「每季度末
SHALL 训练一名新成员」。真正机器可读的，是 serving 校验器自己的间距硬 pin
——`ensemble_serving.py:194` 逐对校验相邻成员 `fit_end` 间隔必须落在
`[MEMBER_SPACING_DAYS_MIN, MEMBER_SPACING_DAYS_MAX]` = `[75, 100]` 天。由它可
以**推导**出下一名成员 `fit_end` 的可接受窗口，而这不是新造判据——它就是轮换
产出的新 manifest 将被真实校验的那条规则。

按当前 manifest（最新成员 `fit_end=2026-04-01`）推：可接受窗口
`[2026-06-15, 2026-07-10]`，**已于 35 天前关闭**。用今天的数据训一名新成员，
间距 135 > 100，`load_ensemble_manifest` 会直接拒绝，轮换后的 manifest 加载
不了。这是一条今天没有任何界面会告诉操作人的事实。

**② gate 证据链的权威在入库的 baseline，不在 gate 文件本身。**
`docs/promotion/csi800_n5_bootstrap_baseline.json` 的
`authorized_by.gate_artifacts` 记录了四份工件的 `sha256`；工件本体在
`output/`（gitignore，本机可删）与 `docs/research/evidence/.../bootstrap_v2_gates/`
（入库）各有一份。已实测两处四份哈希与 baseline **逐一相符**。所以驾驶舱读
入库副本、用 baseline 的哈希绑定内容——不是「读到 PASS 就显示 PASS」。

## What changes

新增只读页面 **「生产运维」**（`web/operator_ui/pages/ops_cockpit.py`），一屏
五张卡。**不执行任何东西**：每张卡旁边给出可复制的 CLI 命令文本，跑不跑由操作
人在终端决定。

### W1 — 现任身份（复用，不重写）

把 #430 落地的 `resolve_incumbent()` / `IncumbentIdentity` /
`load_ensemble_manifest_identity()` 及其环境变量常量，从
`pages/_daily_decision_helpers.py` 上移到 `web/operator_ui/incumbent.py`
（与 `bundle_health.py` / `anchor_health.py` 同层），原处 re-export 保持
今日推荐页与其 63 个钉子一字不改。

**两页共用同一个解析器是硬要求**：两处各写一份，就会出现「今日推荐说现任是 A、
驾驶舱说是 B」的可能——而这一页存在的意义正是消灭这种分歧。

### W2 — 四份 gate 工件（转录 + 哈希绑定，不重新判定）

新增只读读取器。它**不重新推导任何 verdict**：

* 权威 = 入库 baseline 的 `authorized_by.gate_artifacts[*].sha256`；
* 逐份校验入库副本的实际 sha256 == baseline 记录 → 不符即「证据链断裂」，
  **不显示 PASS**；
* 校验通过后，**逐字转录** `overall` 与 `gates.<门名>.verdict`；
* 用 `retrain_gate_lib.expected_gates(scope)` 只做一件事：检出**缺门**。

「五道具名门 vs 四份工件」两个粒度都要说清：member 作用域 2 道
（`trainer_integrity` / `ic_direction`），ensemble 作用域 3 道
（`degeneracy` / `constraint_dry_run` / `serving_veto`）；工件是 3 份 member
+ 1 份 ensemble。页面按**工件**分四张卡，卡内展开其具名门。

`serving_veto` 的 `csi500_weight` 当前 0.7484、上限 0.75，**余量 0.0016** ——
这种贴边余量要显示出来，不能只显示 PASS。

### W3 — recert 状态与 15 个月有效期（复用执行器的判定，不手算）

复用 `scripts.rotation_lib` 的 `parse_recert_status()` / `recert_validity()`
与三个 git argv 构造器。runbook 第 114-117 行原文要求「执行器会机器校验，操作
人无须也**不得**以口头断言替代」——所以页面显示的有效期必须来自真跑
`recert_validity()`，不得手填、不得常量化。

**rev 必须先 pin**：`git rev-parse origin/main^{commit}` 取一次 rev，再用**同一
rev** 读正文与读 tip 日期。`origin/main` 是移动 ref，分两次读会把旧 WIN 正文配
上新 commit 的日期。页面显示所 pin 的 rev，让操作人能自己判断本地 `origin/main`
是否新鲜。

### W4 — 季度重训窗口（推导自间距硬 pin，并明说没有仓库锚）

显示：最新成员 `fit_end` 及距今天数；下一成员 `fit_end` 的可接受窗口
`[fit_end+75, fit_end+100]`；窗口开/关及关闭天数；以及**若用今天的数据训，
间距是多少、会不会被 serving 校验器拒绝**。

页面 MUST 写明这是**由间距硬 pin 推导**，而非仓库里存在的到期日——本仓库反复
被挑的就是 UI 自己造事实，这里不能再犯。

### W5 — 数据新鲜度（每一道都用出单侧自己的读取器/谓词）

尾部日期读自 **`calendars/day.txt`** —— 出单侧 `calendar[-1]` 所建之于的同一份
文件。**不用** `bundle_health.summarise_bundle_health()` 偏好的 `_fetch_integrity`
identity tail：两者在不完整换库后会分歧，恰在年龄边界给出相反的 accept/refuse。
该健康摘要在此仅用于健康信号 —— 它刻意宽容（会吞掉损坏的 stamp），因此只能
**收回**「可用」，不能**授予**。

拒绝阈值取自 `RecommendationConfig.bundle_max_age_days`，并显式写进晨跑命令
（CLI 有自己独立的 argparse 默认值）。完整性这一道由出单侧自己的
`read_bundle_integrity` 加该门的三条规则评定。

页面显示尾部日期、落后天数、距阈值余量，并写明：这两道门只是出单侧前置条件的
**子集**，全绿不等于今天一定能出单。

> 本节在 codex #431 r5/r6/r12/r14/r17 之后重写。初稿曾写「tail 取自 provider
> 元数据（与出单侧是两条不同取数路径）」—— 那句在承认分歧的同时仍把该值当作
> 拒绝预测用，是自相矛盾的；且与最终规格/实现相反，归档后会把维护者指回被
> 否决的来源。

## 边界（本 change 不做）

* **不执行任何命令**：无按钮触发作业/训练/GPU/轮换；命令一律纯文本供复制。
* **不写任何文件**：本页零写侧 API（由 source-pin 测试守）。
* **不改生产执行器**：`scripts/rotation_lib.py`、`retrain_gate_lib.py` 只读复用。
* **不定义季度重训到期日**：仓库没有这个锚，本 change 也不新造一个——只显示
  由间距硬 pin 推导出的窗口，并明说来源。真要一条到期日规则，那是另一个
  change（要动 runbook/spec，不该由一个 UI 页面顺手确立）。
* **不碰 2025 holdout、不揭盲。**
