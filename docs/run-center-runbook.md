# 运行中心 Runbook（operator UI「运行中心」页）

openspec `2026-08-16-ui-run-center`。本页把两个例行动作从终端搬进 UI，
**只触发既有 CLI 的子进程**，参数与驾驶舱印出的命令同源绑定；驾驶舱
（生产运维页）保持「只展示不代跑」的承诺不变。

## 职责边界

| 动作 | 执行形态 | 背后 CLI | 观测 |
|---|---|---|---|
| 数据更新手动补跑 | **后台**（detached 子进程，约 2 小时） | `scripts/daily_update.py`，argv 镜像调度器 | #434 状态工件 + 共享日志 |
| 今日出单 | **同步**（分钟级，spinner 等待；数据更新进行中不可用——换库不与读者并发） | `scripts/daily_recommend.py`，ensemble 五参数；产物经暂存目录逐文件发布并带回滚账本——超时/退出≠0 只清暂存、已发布工件逐字节不变；发布中途失败先整体回滚，**仅当回滚本身不完整**才是撕裂态（页面逐名列出残留、保留暂存与 `.prior` 待手工处置） | 页面直接展示 exit code 与输出尾部 |

- 自动通道不动：每晚 20:30 的计划任务（`run_daily_update.bat`）仍是数据
  更新的主通道；本页只用于漏跑/失败后的补跑。
- **并发权威是 `daily_update` 自身的单飞锁**。页面的「正在运行」预检只是
  读状态工件的 advisory 判断（running 且 6 小时内新鲜才拦）；真撞上并发，
  输掉的那次以 exit 17 落日志，无损。
- 「已启动」≠「更新成功」：成败以状态工件（`<provider>.
  daily_update_status.json`）与日志为准。日志落点 =
  `<provider 父目录>/logs/daily_update.log`（与调度器同一条流；UI 启动
  会先写入一行带完整日期的标记，因为既有日志行只有时分秒）。
- 出单是 fail-loud 的：bundle 过期/完整性戳/ST 快照陈旧/绑定不等，一律
  exit≠0 且原因经本仓 logger 落 **stdout**（stderr 多为 import 期环境
  噪音）——页面优先展示 stdout 尾部，不存在静默错单。
- 本页**不代下单**：出单终点是落盘工件；清单与 HOLD 披露到「今日推荐」
  页看，**每次必读打印的 `entry_date`**（它是已收盘会话）。

## 前置条件

1. 生产 checkout 已更新到含本页与 #434 状态工件的版本。
2. 环境变量 `TUSHARE_TOKEN` 对 UI 进程可见——缺失时启动按钮会拒绝
   （fetch 阶段本来也会立刻失败，页面把失败提前到能读懂的地方）。
   下面的 UI 启动器模板**自带** HKCU 注册表回读；tracked 的
   `run_daily_update.bat` 模板**没有**这段回读——调度任务是靠以登录用户身份
   运行、继承 HKCU 用户级环境变量拿到 token 的。若把任务 `/RU` 换成别的账号、
   或 token 只存在于某个 shell 会话里，夜间更新会在 fetch 阶段 exit 11。

## UI 启动器模板（`.bat`，放在仓库外，如 `D:\qlib_data\start_operator_ui.bat`）

本机绝对路径**只进这份部署模板，不进 tracked 代码**（与
`run_daily_update.bat` 同惯例）。按机器实际路径改前两行：

```bat
@echo off
set "REPO=D:\stock\prod\Quant_Ashare"
set "PY=D:\stock\_canonical_venv\Scripts\python.exe"
if not defined TUSHARE_TOKEN (
  for /f "usebackq tokens=2,*" %%A in (`reg query HKCU\Environment /v TUSHARE_TOKEN 2^>nul`) do set "TUSHARE_TOKEN=%%B"
)
cd /d "%REPO%"
"%PY%" -m streamlit run web\operator_ui\app.py
```

双击后浏览器打开 UI；「运行中心」在「运行」分组。

## 故障速查

| 症状 | 含义 | 动作 |
|---|---|---|
| 启动被拒 `no_token` | UI 进程没继承到 `TUSHARE_TOKEN` | 用上面的 `.bat` 启动，或先在环境里设好 |
| 启动被拒 `already_running` | 状态工件显示一次更新正在进行且新鲜 | 等它结束；若确认已死，等记录按 reader 语义变陈旧（>6h）后再试 |
| 日志见 exit 17 | 与另一次运行撞了单飞锁 | 无损；让先跑的那次跑完 |
| 日志出现乱码 | 该行的写入者没钉编码，落在了控制台代码页（zh-CN 机器上是 cp936）。读侧只在 UTF-8 解码失败时回退 GBK——**不猜**，所以「字节恰好也是合法 UTF-8」的那类历史行救不回来 | 先确认调度器 `.bat` 里有 `set "PYTHONIOENCODING=utf-8"`（见 [调度 runbook](runbook_daily_update_scheduling.md) 的模板；**旧部署可能没有，要手工补**）。补上之后新写入的行一律 UTF-8；此前的历史行只能从日志文件本身追溯 |
| 出单提示「回滚不完整，发布目录可能混着两次运行的文件」 | 发布中途失败且回滚也没做干净——**这才是撕裂态** | 按页面逐名列出的残留手工处置；`staging/` 与 `.prior` 已保留为证据。处置前**不要**把该批清单当今日结果 |
| 出单 exit≠0 | CLI 的 fail-loud 拒绝 | 读页面转述的输出尾部（拒绝原因在 stdout），修好数据再跑 |
| 启动被拒 `unusable_path` | 某个路径为空/相对/异约定拼写 | 修 `config.yaml` 或对应 `QUANT_*` 环境变量 |
| 出单被拒 `blocked_by_update` | 更新器的 provider 单飞锁正被持有（权威判定；状态展示只是参考） | 等更新结束（锁随对方进程退出自动释放）再跑 |
| 出单超时(900s)且日志无进展 | 已知风险：qlib kernels 在非交互子进程可能挂死（`init_qlib_canonical` 未钉 kernels） | 超时整树终止（含 joblib 工作进程）是兜底，无损；复现则升级处理（在 CLI init 侧钉 kernels，另行提案） |
| 出单超时且提示「进程树终止不完整」 | 极端情形：taskkill/killpg 失败 | 按提示的顶层 pid 手工排查；暂存目录已保留为证据 |
| 状态记录「属于另一个 provider」 | 状态文件被别的部署顶替 | 检查两台调度是否把 `--status-path` 指到了同一文件 |
