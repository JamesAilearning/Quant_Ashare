# Tasks: 2026-08-17-ui-drift-p1

## W1 状态词汇归一

- [x] `_normalise_cli_entry`：`ok → completed`（同源于既有
  `success → completed`）；`partial` 原样保留；未知词汇透传

## W2 run_dir 与可检视判据

- [x] `JobSummary.run_dir`（UI 取 `run_dir`，CLI 取 `output_dir`；带默认
  值，既有构造点与 `to_dict` 同步）
- [x] `run_dir_is_inspectable`：纯路径判据（无 I/O），边界 = `output/`
  两棵树，相对路径锚定**仓库根**而非进程 CWD

## W3 搁置 + 报数

- [x] `list_all_jobs` 跳过产物在读边界外的 CLI 行
- [x] `count_cli_rows_outside_output_tree()`；作业页显示条数与根因
  （实测本机 3404 条搁置 / 117 条可用）

## W4 滚动验证页

- [x] 运行清单并入 CLI 行（`type_filter="walk_forward"` +
  `source_filter="cli"`），跳转不再是死路（实测 92 条可打开）
- [x] 运行身份行（含 anchor 高亮说明 + git 溯源，脏树显式标注）
- [x] `metric_status` 四分支，**缺失分支排在最前**且绝不落 official；
  `metrics_purpose` 不一致时并排展示

## W5 测试

- [x] `tests/logic/test_jobs_wf_reachability.py`（14 用例）：词汇归一
  三态 / 可检视五态（含相对路径不随 CWD 变、越界拒绝）/ run_dir 透传与
  默认值 / 四条页面源码钉（含缺失分支必须先于 official 判定的**顺序**钉）

## codex #444 r1（三条 P1 全实修）

- [x] **分页控件被吞**:披露段插在 pg_indicator 与 pg_next 之间,缩进把
  `with pg_next:` 一起吞进 `if _set_aside:`——无搁置行时「下一页」消失。
  修:披露整体移到分页块之后,只让 caption 受条件控制
- [x] **路径锚定不一致**:可检视判据把相对 output_dir 锚在仓库根,而页面
  下游 `Path(selected)` → guard_output_path 走进程 CWD → 在仓库根之外
  启动 UI 时,「判定可达」的运行反被守卫拒绝。修:选项存同样锚定后的路径
- [x] **治理含义写反(最严重)**:我把 iso_week 说成「认证胜者」。治理钉
  明写 winner=fold_phase(csi800_cadence5_conservative)、isoweek 复核=
  iso_week,两者是不同 schedule;写反会让**合法的认证证据被当成参照运行**。
  修:两个 anchor 各给一条准确说明(fold_phase=认证胜者锚 / iso_week=
  生产服务锚,经单独门控)
- [x] 三条各配钉,其中 anchor 那条**直接对着治理钉的事实断言**(读两份
  preset 的 rebalance_anchor),而不是断言某句措辞——钉子挪了测试会跟着响

## codex #444 r2（两条 P1 全实修）

- [x] 同目录多次运行被折叠且**静默**换成别的运行:同一 preset 反复跑会把
  报告写回同一个 output_dir(实测 92 条折叠成 20 个目录、8 个目录被反复
  覆盖),点旧行匹配不上就落到 index 0。修:保留「每目录取最新」(旧运行
  产物已被覆盖,列出来只会渲染同一份最新报告),但匹配失败**明确告警**+
  统计被覆盖条目数;钉住告警必须在 selectbox 之前
- [x] 治理说法只改了展示文案、漏改源码注释与 proposal:两处残留仍写
  「胜者只由 anchor=iso_week 决定」。修:全文一致化(fold_phase=认证胜者 /
  iso_week=生产服务锚),grep 复查无残留

## codex #444 r3（两条 P1 全实修）

- [x] UI 与 CLI **共用同一 output_dir**:UI 启动的滚动验证会同时留下一条
  UI 作业和一条 CLI 目录记录(JobManager 把结果目录写进 config["output_dir"],
  引擎再按它编目),而选择器每目录只存一个 id → 另一个 id 的跳转永远匹配
  不上。修:新增 `_run_id_to_dir` 索引(同时收 UI 与 CLI 两套 id),跳转先按
  id→目录定位、再退回旧比法
- [x] spec delta 仍写「anchor 单独决定谁是生产」:r2 只清了 proposal 与源码
  注释。归档时这句会把治理错误重新引回来。修:改述为两级链(认证胜者跑
  fold_phase;iso_week 是单独门控的复核,其净超额>0 是晋升条件之一,之后才是
  服务参数绑定),并显式写下「anchor 单独 SHALL NOT 被当作生产判据」

## codex #444 r4（两条 P1 + 一条 P2 全实修）

- [x] **治理文案只凭 anchor 推身份**——这正是本 change 的 delta 自己写下的
  禁令,而我的文案恰好犯了它:`stage7_daily_h5` / csi300 参照运行只因用
  fold_phase 就被标成「认证胜者」。修:先确认整族身份(csi800 + SH000906TR +
  N=5 + phase=0)才谈治理链;族外只解释 schedule 语义并明说不给治理判断
- [x] **r3 的别名表把被覆盖的历史 id 也收了**,点旧行反而 `_requested_found=
  True`、绕过 r2 的告警、静默渲染最新报告——等于把 r2 修的东西废掉。修:
  别名只覆盖**同一次调用**的 UI/CLI 两个 id(每目录第一条 CLI 记录),被覆盖
  的历史 id 留在告警路径。实测分流:20 可别名 / 72 走告警
- [x] 披露在两处空态 `st.stop()` **之后**:目录全是越界记录时(正是本改动
  针对的重污染场景),页面说「暂无作业」就停住,反而不提搁置了多少。修:
  披露前移到空态之前(且仍不在分页块内——r1 那条约束同时成立)

## codex #444 r5（一条 P1，修在共享层）

- [x] **CLI 流水线行仍是死链**:作业页把它们路由到 `results.py`,而该页选择器
  只由 `JobManager.list_jobs()` 构成,请求的 id 不在 UI 作业目录里就直接
  「运行未找到」。实测本机 13 条可检视的 CLI 流水线行,全部有
  `pipeline_report.json`,却一条都打不开——正是本 delta 禁止的死链。
  选「接纳」而非「从作业页排除」:产物真实存在,排除等于把能看的东西藏起来
- [x] **修在共享层而不是再抄一遍**:r1(锚定)/r2(被覆盖不静默换人)/r4(被覆盖
  不进别名)三轮改的是**同一个**算法,再抄一份到结果页必然分叉。折叠提到
  `job_io.fold_catalog_by_dir`(纯函数),两页共用;锚定与可检视判据共用
  `anchored_run_dir`,两处不同锚这个 bug 类被结构性消除
- [x] **等价性核验**:新旧算法在真目录上逐项比对——滚动验证 92 行 → 20 目录
  / 72 被覆盖,流水线 13 行 → 13 目录 / 0 被覆盖,`superseded` / `id→dir` /
  `run_options` 三张表全部逐键相等
- [x] 结果页把「产物被覆盖」与「运行未找到」分成两条路:前者明说是谁覆盖了它,
  后者才是记录不存在。且比较**解析后**的 id 而非原始请求——比原始请求会立刻
  改写 query_params → 重跑 → 告警只闪一下就没了
- [x] 测试改锚:两条源码钉升级为对 `fold_catalog_by_dir` 的**行为**测试
  (锚定 / 首条即最新 / 被覆盖者两表不相交 / 大小写折叠 / 空目录行 / 空输入 /
  真目录不变式),另加结果页四条钉

## codex #444 r6（两条 P1 + 一条 P2 全实修）

- [x] **灵敏度臂被标成认证胜者**:`csi800_cadence5_base` 四个旧谓词全中
  (csi800 / SH000906TR / N=5 / phase=0),却是 **5 bps** 灵敏度臂而非 20 bps 的
  认证胜者——base 与 conservative 恰差 `{output_dir, slippage_bps}`。修:入族
  条件补上成本口径,且**取自 `EVAL_PROFILES["csi800_n5"]` 本身**而不是抄字面量
  (晋升族语义钉在那里,治理测试也钉着它);去掉 `rebalance_anchor`——族**跨**
  两个锚,那是族内的区分维度。全 36 个预设扫一遍:**恰好 2 个入族**,就是认证对
- [x] **被覆盖的 id 被扔到全局第一条**:它们故意不进 `_run_id_to_dir`(那是静默
  别名的路),但也不能就这么丢——`_target_dir` 为空 → `_default_index` 落到 0 →
  渲染的是**另一个目录**的运行,告警还说不出是谁覆盖了它。修:用
  `_folded.superseded_dir_of_run` 定位到它**自己那个目录**,告警指名现在住在
  里面的是谁;定位成功**不算**「找到」,告警照发(否则退回 r2 修掉的静默换人)
- [x] **折叠键没折平 `..`**:`output/runs/a` 与 `output/x/../runs/a` 都判可达、
  指向同一份产物,却被当成两次运行,被覆盖的历史行于是静默渲染当前报告。修:
  `anchored_run_dir` 用 `os.path.normpath` 折平(纯词法、无 I/O);并让
  `run_dir_is_inspectable` **调用**它——此前我在注释里写「共用同一段代码」,
  其实是两份拷贝,那句话当时是假的
- [x] 判据迁到 streamlit-free 的 `pages/_walk_forward_helpers.py`:为了一个纯
  谓词让测试导入页面(连带 streamlit),正是 #442 r6 抓到的错法
- [x] 测试:新增 `GovernedFamilyPredicateTests`(判据源自 profile / 含成本口径 /
  认证对入族而 base 不入 / 全预设恰好两个 / int-float 拼写等价)与 `..` 折平、
  判据共用锚定、被覆盖 id 路由四组;两条失效的源码钉重新对准新实现

## 验证

- [x] 定向 + governance：490 passed / 1 skipped；jobs 源码钉 11 passed
- [x] mypy CI 精确命令 220 文件 Success；ruff 全过
- [x] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge
