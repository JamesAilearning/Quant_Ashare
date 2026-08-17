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

## 验证

- [x] 定向 + governance：490 passed / 1 skipped；jobs 源码钉 11 passed
- [x] mypy CI 精确命令 220 文件 Success；ruff 全过
- [ ] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge
