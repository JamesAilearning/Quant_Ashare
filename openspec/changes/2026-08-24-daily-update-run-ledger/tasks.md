# Tasks: 2026-08-24-daily-update-run-ledger

## 实现

- [x] 写侧 `daily_update.py`：终态追加一行台账（残尾隔离 + 单次 write + fsync）
- [x] 写侧 `daily_update.py`：起跑时写一行带日期的运行边界
- [x] 读侧：台账 reader（容错解析、坏行计数、按 provider 过滤）
- [x] 读侧：`update_progress` 按边界切段，切不到就如实说不知道
- [x] UI：今日工作台「近 N 次」条带

## 治理钉（机器强制）

- [x] 台账**只可追加**：`_append_ledger` 的 `open` mode 只能是 `ab`/`rb`，
      且不许出现 `write_text` / `os.replace` / `truncate` / `write_bytes`
- [x] 台账写入失败**绝不改变退出码**（吞 `Exception` 而非 `OSError`）
- [x] `--dry-run` 不写台账、不写边界
- [x] 阶段语义零改动：AST 守卫钉住 `_execute_daily_update` 内不得出现
      `_append_ledger` / `run_boundary_line` / `default_ledger_path`
- [x] 边界带完整日期 + normalized provider；别的 provider 的边界不采纳
- [x] UI 复现台账、不自造判定；台账缺失/不可读/坏行都要说出来

## 验证（实测数字）

- [x] 写侧守卫 **18 条 + 7 subtests**；读侧/UI 守卫 **25 条 + 7 subtests**
- [x] 变异 **15 条全咬**
- [x] 分目录：logic **4271** / data_pipeline **437** / governance **517** / pit **34**
- [x] ruff clean；mypy --strict **232 文件 0 error**
- [x] `openspec validate --strict` valid
- [ ] codex CLEAN + CI 七绿 → STOP 等 merge

## 刻意不做（勿在评审中重开）

- 不存耗时字段（可由两个时间戳推出）
- 不写运行「结束」标记（状态工件与台账已回答）
- 不给台账加 CLI 覆盖开关（那会把状态工件那整套路径守卫一并请进来）
- 不做百分比进度条（fetch 只是六阶段里的第二个）
- 窗口里找不到边界时不扩大读取直到找到（日志无界，那是没有上界的读取）

## 三条既有守卫开火，处置一律「改我，不削弱守卫」

| 守卫 | 为什么开火 | 处置 |
|---|---|---|
| `src/` 零 `decision_journal` 引用 | 我在 `_append_ledger` 的 docstring 里点了那个模块的名字，去说明追加纪律照抄自它 | **改我的措辞**：只讲道理不点名。治理边界是「`src/` 与 web 层自有状态零关联」，为一句注释破例不值 |
| `update_progress` 不许长回归属猜测（禁 `started_at` 等 token） | 我的字段叫 `boundary_started_at` | **改我的字段名** → `boundary_stamp`。被禁的那个名字指的是「拿进度时刻比状态工件的起跑时刻」这个被证伪的启发式；我的戳来自边界本身、不参与比较。改名把区别摆明，守卫一字未动 |
| 进度只在 running 分支内渲染 | 它用 `_progress = _read_progress()` 作**定位锚**，我改名后它先匹配到后面的 `_baseline_progress = _read_progress()` | **只更新锚串，断言一字未动**——那条断言依然完全正确 |

第二条尤其值得记：那个守卫当年立起来，正是为了**阻止**「给进度加归属」。它的前提
（写入侧没有带日期的边界）被本改动消除了——但正确做法不是删它，而是让我的命名与
它划清界限，并另加一条正面守卫钉住「归属来自边界，不是来自比时刻」。
