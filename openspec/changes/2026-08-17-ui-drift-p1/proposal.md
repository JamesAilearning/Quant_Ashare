# Proposal: 修 UI 漂移 P1——作业↔滚动验证的可达性、状态词汇、指标口径与运行身份

## Why

承 `2026-08-17-ui-drift-p0` 的同一轮审计，本 change 修「读侧」的四条：
页面把数据摆错地方、或把该说的话吞掉。全部经本机实测。

### D4 作业页的详情跳转对 CLI 滚动验证是死路

详情页 `walk_forward.py` 的运行清单只来自 `JobManager.list_jobs()`
（= UI 作业目录）。实测本机 UI 作业目录里 `walk_forward` 数量为 **0**
（只有 pipeline 5 / tushare_provider 7），而作业页列出的滚动验证行全部
来自 CLI 运行索引。于是：作业页刚列出几千条滚动验证 → 点「查看详情」→
详情页断言「暂无滚动验证记录」。操作人可能据此以为历史丢了。

### D5 状态词汇两侧对不上

CLI 索引写的是 `ok`(1055) / `partial`(2443)，**从不写 `completed`**；
而作业页的筛选下拉、标签、图标都说「已完成」。结果是筛选会静默吞掉
它自己刚标成「已完成」的行。

### D6 运行索引被测试污染（过程中查出的更深问题）

`src/core/run_catalog.py` 的 `_DEFAULT_CATALOG_PATH` 是 **CWD 相对**
路径。测试从仓库根跑时，其记录被追加进操作人的真实索引，而产物写在
随后被删除的临时目录里。实测：**3509 条记录里 3404 条（97%）的
`output_dir` 指向已不存在的临时目录**，真实运行只有 105 条（其中
92 条滚动验证）。这些行既打不开，也在本页读边界（`output/` 与
`output/operator_ui/` 两棵树）之外。

### D7 滚动验证页不说这批数字算不算数、也不说它来自哪个运行

- `metric_status`（引擎自 codex #406 起盖的戳：official /
  predictions_only / unverified）在该页**零引用**——被 RAISE 拒绝过的
  数字与认证数字长得一模一样。
- 页面不显示宇宙/基准/anchor。实测
  `csi800_cadence5_conservative`（**认证胜者**，`anchor=fold_phase`）与
  `…_isoweek`（**生产服务锚**的复核切片，`anchor=iso_week`）除
  `rebalance_anchor` 外字段全同、同为 23 折——anchor 决定这份报告属于
  **哪条证据链**，而两者在页面上无法区分。

## What changes

### W1 状态词汇归一（一处）

`_normalise_cli_entry` 把 CLI 的 `ok` 翻成 UI 的 `completed`，与既有
`success → completed` 同源。`partial` **原样保留**（它已经是下拉选项、
有标签、有图标、在 `_param_guard` 白名单里，归一会抹掉「部分折缺 IC」
这条信息）。未知词汇原样透传——只翻译已知同义词，发明映射会把没见过
的状态悄悄改写。

### W2 `JobSummary.run_dir` + 可检视判据

`JobSummary` 增 `run_dir`（UI 取 `run_dir`，CLI 取 `output_dir`；带
默认值，既有构造点不受影响）。新增**纯路径**判据
`run_dir_is_inspectable`：产物必须落在 `output/` 两棵树内——正是本页
spec 的读边界，且相对路径按**仓库根**而非进程 CWD 解析（索引里 1257
条是相对路径，按 CWD 解析会随启动目录变答案）。无 I/O，所以三千多行
的过滤是纯算术。

### W3 不可打开的行搁置 + **报数**

`list_all_jobs` 跳过产物在 output 树外的 CLI 行；作业页用
`count_cli_rows_outside_output_tree()` 显示搁置条数与根因。**报数**是
硬要求：静默截断会让页面看起来"覆盖了全部"，而实际上藏了 97%。

### W4 滚动验证页收 CLI 运行 + 补两块展示

- 运行清单并入 `list_all_jobs(type_filter="walk_forward",
  source_filter="cli")` 的行，跳转不再是死路。
- **运行身份**一行：宇宙 · 基准 · topk · N · **anchor** · ensemble ·
  label · 滑点，外加代码身份（`git_commit`，脏树运行显式标注）；
  两个 anchor 各给一条准确说明：`fold_phase` = 认证胜者所用的锚（战役
  主判据），`iso_week` = 生产服务锚（经单独门控；复核 run 净超额 > 0 是
  晋升门之一）。两者是不同 schedule，不可互相顶替（治理钉
  `test_csi800_n5_production_serving.py` 钉死 winner=fold_phase /
  isoweek=iso_week）。
- **指标口径判定**四分支，其中「键缺失」是**主路径**：本机 21 个真实
  运行里 16 个没有该键（含全部 csi800 战役运行，它们早于 #406）。缺失
  一律显式标注「未标注 ≠ official」，绝不落进 official 分支——否则
  #406 整套防线在 UI 上作废。`metrics_purpose` 与判定不一致时并排
  展示（声明只能让判定更差，不能更好）。

## 边界（本 change 不做）

- **不改 `run_catalog` 的默认路径**：让测试与真实运行共用一份索引是
  根因，但修它要动核心写入侧与测试基建，另行提案。本 change 只让 UI
  如实处理并**报出**污染规模。
- 不动 P2 档（预设分组、`production.yaml`、结果页毛/净口径标注）。
- 不改引擎、不改产物 schema、不新算任何指标（spec：UI 不得重算官方
  指标——本 change 只读既有字段）。
