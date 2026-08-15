# Proposal: 数据检视页 06 PIT 校验改为子进程运行

## Why

数据检视页当前在 UI 进程内直接 `PITValidator(...).validate()`。qlib 是
进程级单例：一旦本 UI 进程用某个 provider 初始化过 qlib，再校验另一个
provider_uri 就会以 `QlibRuntimeInitError` 受控失败，页面只能提示
「重启 UI 后再校验这个 bundle」（既有 codex P2 受控降级）。这意味着页面的
核心功能之一——按需校验任意 bundle——在真实使用中随时可能不可用，且恢复
手段是重启整个 UI 会话。

进程隔离是消除这一类故障的最小改动：06 校验本来就有独立 CLI
（`scripts/data_pipeline/06_validate_pit_data.py`，daily_update 编排也正是
这样调它），支持 `--report-json` 出结构化报告。子进程每次拿到全新解释器，
单例限制自然消失；页面也不再需要 import validator / qlib runtime。

## What changes

1. **新增 `web/operator_ui/pit_validation_runner.py`**（只读 helper）：
   以子进程运行 06 CLI（`sys.executable`，可覆盖）+ `--report-json` 指向
   `TemporaryDirectory`，解析结构化报告返回 `PITRunResult`：
   - `ok`（报告解析成功；`exit_code` 即报告自身结论 0/1/2——注意进程
     退出码 2 **且**报告可解析时是「校验发现失败」的结果，不是运行器错误）；
   - `run_failed`（CLI 未产出报告即非零退出 / 脚本缺失，带 stderr 尾部）；
   - `corrupt_report`（报告缺失/非法 JSON/形状违约，fail-loud 绝不默认化）；
   - `timeout`（默认上限 900s，子进程被终止）；
   - `launch_failed`（解释器无法启动）。
   text-mode 调用钉 UTF-8（沿袭 e7504f6 约定）；临时目录随返回即删，
   bundle 本身只被只读打开。
2. **`data_inspect.py` Section 3**：改用 runner；删除进程内
   `PITValidator` / `QlibRuntimeInitError` 路径与 import；渲染逻辑不变
   （徽章 + 逐项表格 + expander 明细），失败分支统一为醒目的
   `校验无法运行(kind)`。按钮/说明文案标明子进程语义。
3. **治理钉**：`test_data_inspect_readonly.py` 新增——页面 import 行不得
   出现 `PITValidator` / `qlib_runtime`（钉住子进程架构，防回退）。
4. **逻辑测试** `tests/logic/test_pit_validation_runner.py`：fake
   `subprocess.run` 钉命令形状、UTF-8 text-mode、全部结果分支（含
   「exit 2 + 报告存在 = 结果而非错误」这一关键区分）、临时目录不泄漏；
   另有 06 CLI 路径存在性的防重构漂移钉。

## Non-goals

- 不改 06 CLI、`PITValidator` 的任何检查逻辑或退出码语义。
- 不做异步/后台任务化——按钮仍是同步 spinner 等待，交互不变。
- 不引入跨页面的 qlib 进程管理；其他页面是否初始化 qlib 与本变更无关。

## Impact

- 行为变化（即修复点）：同一 UI 会话可对任意 provider_uri 重复运行校验，
  不再触发「重启 UI」受控失败。
- 治理风险低：web/ 侧只新增只读 helper；写盘仅限系统临时目录且即删；
  页面只读扫描保持绿并新增架构钉。
- 测试：新逻辑测试 11 例 + 治理钉 1 例；影响面为数据检视页单页。
