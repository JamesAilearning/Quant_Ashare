# Proposal: 运行中心页——数据更新与出单的 UI 触发（参数同源、子进程执行、驾驶舱只读承诺不动）

## Why

生产进入日常运行后，操作人的两个例行动作仍然只能开终端：

1. **数据更新补跑**。夜间 20:30 计划任务（`run_daily_update.bat`）是自动通道，
   但漏跑/失败后的手动补跑要在终端拼六个参数。#434 已把运行状态落成
   工件（`<provider>.daily_update_status.json`），数据检视页只读展示——
   观测有了，触发没有。
2. **每日出单**。驾驶舱①printed 命令是权威文本（`morning_command`，参数
   与 serving config 两级绑定同源），但仍需操作人复制到终端执行。

驾驶舱（`v2-ops-cockpit-page`）的设计承诺是「只展示不代跑」，这条承诺有
source-pin 测试钉死（`tests/logic/test_ops_cockpit_page_source.py` 禁
`st.button`/`subprocess`），**不应也不必推翻**。仓库已有「audited runner」
先例：#435 的 `pit_validation_runner`（页面按钮 → 子进程跑 06 校验 CLI，
argv 钉死 + 治理豁免 + logic 测试钉 argv 形状）。缺的只是一个承担「代跑」
职责的新页面和两个比照先例的 runner。

关键实测约束（2026-08-14 日志）：完整一轮 daily_update ≈ **1 小时 50 分**——
同步 spinner 不可行，更新触发必须是 **detached 后台子进程**，观测交给
#434 状态工件；daily_recommend 为分钟级，可同步执行。

## What changes

### W1 `web/operator_ui/update_runner.py` — 数据更新 detached 启动器

- argv 钉死镜像调度器形状：`<python> scripts/daily_update.py --tushare-dir
  <provider 父目录/tushare_raw> --provider-dir <provider> --delisted-registry
  <registry> --reference-cases <repo>/tests/pit/reference_cases.yaml
  --start-date 20180101`。
- **detached**：`stdin=DEVNULL`，stdout/stderr 追加到调度器同一条日志流
  （`<provider 父目录>/logs/daily_update.log`，追加前写一行带日期的
  launch 标记——既有日志行只有时分秒）；Windows 下
  `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`，POSIX 下
  `start_new_session=True`；子进程 env 经 `utf8_child_env()` 钉 UTF-8。
- 启动前预检（均 fail-loud，不启动）：argv 三路径必须是绝对路径（空串
  会被解析成当前工作目录）；`TUSHARE_TOKEN` 缺失；状态工件 running 且
  新鲜（advisory——并发权威是 `daily_update` 自身单飞锁，撞锁 exit 17
  会落日志）。
- 返回 `UpdateLaunch(kind, pid, log_path, error)`；`launched` 只表示
  「进程已起」，不代表成功——成败由状态工件与日志承载。
- 附 `log_tail()` 只读小工具供页面展示日志尾部。

### W2 `web/operator_ui/recommend_runner.py` — 出单同步 runner

- 比照 `pit_validation_runner`：同步 `subprocess.run`，`capture_output +
  text + encoding="utf-8" + errors="replace"`，`timeout` 默认 900s，
  `cwd=repo 根`，env 经 `utf8_child_env()`。产物写入
  `output/daily_recommend/` 下每次一新的**暂存目录**（`--out-dir`），
  exit 0 后逐文件同卷 `os.replace` 原子发布——超时杀在
  `write_outputs` 中间绝不撕裂已发布的当日工件；发布中断保留暂存
  （唯一完整副本）并指名（codex #440 r1）。
- argv：`<python> scripts/daily_recommend.py --ensemble-manifest <m>
  --provider-uri <p> --delisted-registry <r> --name-source <n>
  --bundle-max-age-days <d>`——与驾驶舱 `morning_command` ensemble 分支
  逐参数同源；**绝不**注入 `--model/--fit-*/--topk/--instruments/
  --rebalance-cadence-days`（宇宙/节奏/topk 留给 serving config 两级绑定）。
- 返回 `RecommendRunResult(kind, exit_code, stdout_tail, stderr_tail,
  elapsed_s, error)`：`ok`（exit 0）/`failed`（exit≠0；拒绝原因经本仓
  logger 落 **stdout**——`StreamHandler(sys.stdout)`+`propagate=False`，
  stderr 多为 import 期环境噪音，页面优先展示 stdout 尾部）/`timeout`/
  `launch_failed`/`run_failed`（脚本缺失）。

### W2b `web/operator_ui/provider_lock.py` — 权威串行化（codex r2）

- 状态工件闸门是 advisory（写失败不改更新器退出码；>6h「陈旧」可能仍是
  活进程）。出单执行 SHALL 持有更新器自身的 provider 单飞锁——web 侧
  镜像模块（不 import 管线层），镜像正确性由与
  `src.data_pipeline.single_flight` 的双向互斥行为测试实证。锁忙/锁
  文件不可用 → `blocked_by_update` fail-closed 拒绝；持锁期间真更新器
  以其正常 exit 17 快速拒绝。锁只覆盖子进程读窗口。

### W3 `web/operator_ui/pages/run_center.py` + `app.py` 注册

- 新页「运行中心」，注册进 `st.navigation` 的「运行」组 + `_ICON_MAP`。
- 区块一 数据更新：状态工件三态展示（复用 `v2-operator-ui` reader 语义，
  外来 provider 记录拒绝）+ 刷新按钮 + 「手动启动」按钮（running 新鲜时
  禁用）+ 日志尾部 expander + 调度说明（20:30 自动通道不动）。
- 区块二 出单：`st.code` 展示 `morning_command` 权威命令文本（终端复制
  仍可用）；现任为 ensemble、命令可渲染、**且数据更新未在进行**
  （`bundle_swap` 两段 rename 不与读者并发，codex #440 r1）时给
  「跑今日出单」按钮（spinner 同步）；单模型/不可解析现任不给按钮只给
  说明。结果按 exit code fail-loud 展示，成功列出已发布工件并引导去
  「今日推荐」页看清单与 HOLD 披露。
- 页面自身 **不 import subprocess/编排器**——spawn 只发生在两个 runner。

### W4 测试

- `tests/logic/test_update_runner.py`：fake `Popen` 钉 argv 形状与
  detach/日志/env kwargs；token 缺失拒；running-fresh 拒（陈旧 running/
  外来 provider 记录不拒）；OSError → launch_failed；脚本与
  reference-cases 存在性漂移守卫；runner 源码目标钉（含
  `daily_update.py`、不含 `06_validate`/`daily_recommend.py`）。
- `tests/logic/test_recommend_runner.py`：fake `subprocess.run` 钉 argv
  （含五个同源参数、**不含**六个绑定/互斥旗标）与 UTF-8 kwargs；
  exit 0/非 0/timeout/OSError 四分支；源码目标钉（含
  `daily_recommend.py`、不含 `daily_update.py`）。
- `tests/logic/test_run_center_page_source.py`：页面源码禁
  `subprocess`/`Popen`/`os.system`/`open(`/写 API/`src.data_pipeline`/
  `JobManager`；必须经由两个 runner；app.py 注册行与图标存在。

### W5 runbook

- `docs/run-center-runbook.md`：页面职责、并发权威（单飞锁）与预检的
  advisory 性质、日志落点、UI 启动 `.bat` 模板（含 TUSHARE_TOKEN 注册表
  回读，比照 `run_daily_update.bat`；本机路径只进模板不进 tracked 代码）。

## 边界（本 change 不做）

- 不改调度器/计划任务；不在 UI 内建调度（自动通道 = 既有 20:30 任务）。
- 不改 `ops_cockpit`/`data_inspect` 及其 spec 的任何承诺；
  `pit_validation_runner` 一字不动（其「不含 `daily_update.py`」钉保持
  可满足——这正是两个 runner 必须新开模块的原因）。
- 不代下单：出单终点是落盘工件；今日推荐页 HOLD 拦截原样。
- 单模型（legacy）出单不提供按钮，只展示终端命令文本。
- 不做日志轮转/进程管理（kill 按钮等）——detached 进程的生命周期归
  操作系统与单飞锁。
