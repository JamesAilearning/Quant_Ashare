# Tasks: 2026-08-22-manual-rerun-range

## 实现

- [x] `build_update_argv` 接受 start/end；不传时 argv 与调度器逐字相同
- [x] `date_input_problem` / `range_problem`：格式、真实日期、首尾顺序
- [x] `launch_daily_update` 以 `bad_range` 拒绝，且不创建进程
- [x] `calendar_gate_warning`：复现闸的**三条件合取**，只预警不拦截
- [x] 页面：范围输入（折叠，缺省即调度器那组）、校验提示、预警横幅
- [x] 页面展示的参数改为从 `build_update_argv` 派生，删掉手抄的那份

## 验证（每条要实测数字）

- [x] 既有 `test_update_runner.py` + `test_run_center_page_source.py` **原样 51 条全绿**
      —— 含那条 FULL-LIST 相等守卫，即「缺省没动」的证明
- [x] 新守卫 37 条 + 20 subtests
- [x] 变异 15 条全咬（argv 夹带与空开始日期两条**同时**被既有守卫抓到）
- [x] ruff clean；mypy --strict 231 文件 0 error（本机 mypy 1.20.2，CI 是 2.3.1）
- [x] 全量分目录 pytest：logic 4222 / governance 446 / data_pipeline 419 / pit 34
- [x] `openspec validate --strict` valid
- [ ] codex CLEAN + CI 绿 → STOP 等 merge

## 自审抓到两条，其中一条是重犯 #461 的病

**① 时钟用了东八区，而编排器用宿主本地 `date.today()`。** 生产 CLI 没有 `--now`
可以覆盖它，所以 run_date 就是 `date.today()`。东八区是本仓库给**操作人可见
时间戳**的约定，不是这个判定的时钟——混用正是 #461 三条 P1 里的第一条。

本机就在 +08:00，两个时钟数值恰好一致，首版 29 条守卫**全绿**。这正是 #461 那条
记忆的原话：「数值相等在本机可能只是巧合」。所以补的守卫钉的是**取法**（页面必须
调 `gate_today()`，`gate_today` 的 return 必须是 `date.today()`），不是数值——数值
断言在这台机器上恒真，证明不了任何东西。

**② 纯空白日期漏进 argv。** `range_problem("  ")` 判它「未指定」（合法），而
`"  " or START_DATE` 会取到那两个空格（非空即真），argv 里就出现
`--start-date "  "`。页面恰好先 `.strip()` 过所以碰不到，但启动器才是被审计的
边界，它必须自己站得住——与「范围校验要在启动器里再做一遍」同一个理由。

## 重述为什么必须配穷尽等价守卫

`update_runner` 不许 import `src.*`（模块 docstring 与
`test_update_runner.py::test_update_runner_never_imports_the_orchestrator`
都钉着）。所以日历闸的两个判据只能在 UI 侧**重述**，而这个仓库刚为「同一件事
写两处」付过学费（#461 首版三个决策全错、三条 P1）。

因此不是抽几个用例看看，而是把输入域穷尽：非交易日判据拿真判据**逐日比一整年**
（含闰年 2/29），live bundle 判据取三个路径在/不在的**全部 2³ 组合**。
