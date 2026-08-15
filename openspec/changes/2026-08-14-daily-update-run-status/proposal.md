# Proposal: daily_update 落盘运行状态工件，数据检视页展示「上次更新」

## Why

每日数据更新（`scripts/daily_update.py`，阶段5 PR-P 起由 Windows 计划任务
驱动）在 UI 之外运行。操作人今天要知道「上次更新什么时候跑的、成功没有、
死在哪一阶段」，只能去翻 `D:\qlib_data\logs\daily_update.log` 或
`schtasks /Query` 的 Last Result —— 两处都在 UI 之外，且日志是滚动纯文本，
没有一行机器可读的终态记录。

退出码表（0/2/10..17）已经把「死在哪」编码得很清楚，但它只存在于进程
退出的一瞬间；没有任何持久化。运维 UI 因而无法回答最基本的问题：
**昨晚的更新成功了吗？**

## What changes

1. **`src/data_pipeline/daily_update.py`**：`run_daily_update` 在运行开始与
   每个终态（成功或任一失败退出码）各写一次状态工件
   `<provider_dir>.daily_update_status.json`（provider 的兄弟位、按名派生,
   兄弟 bundle 不共用同一份；临时文件 + `os.replace`
   原子落盘）：

   ```json
   {
     "schema_version": 1,
     "state": "running" | "finished",
     "run_date": "2026-08-14",
     "started_at": "2026-08-14T20:43:00+08:00",
     "finished_at": "2026-08-14T21:58:12+08:00",
     "exit_code": 0,
     "failed_stage": null,
     "detail": ""
   }
   ```

   - `failed_stage` 为失败阶段的阶段键（`fetch` / `snapshot` / `registry` /
     `bins` / `membership` / `universe` / `benchmark` / `validate` / `swap` /
     `startup_repair`），成功时为 `null`；`detail` 携带一句人读摘要。
   - `--dry-run` 不写（它不产生任何副作用的语义不变）。
   - **状态写失败绝不改变退出码**：写盘出错记 ERROR 日志后继续——状态工件
     是可观测性，不是 canonical 行为；让可观测性故障反转数据更新的成败是
     反向耦合。
   - CLI 新增 `--status-path`（默认即上述同级派生路径，与全链「路径端到端
     显式、无环境变量耦合」一致——派生默认值来自已显式的
     `--provider-dir`，不引入新环境变量）。

2. **`web/operator_ui/`**：新增只读读取器 `update_status.py`（路径由
   provider_uri 同级派生 + 解析 + 形状校验，纯函数可测）；数据检视页新增
   「上次数据更新」小节（位于完整性戳之前）：运行中/成功/失败三态 +
   退出码 + 失败阶段 + 起止时间。文件缺失 → 「从未记录」info 态（新机
   器/首跑前）；形状违约 → 醒目 error（与本页既有 fail-loud 风格一致）。
   页面**不 import、不调用** `daily_update` / `bundle_swap`（治理扫描查
   import 行与进程派生原语；散文提名是允许的——操作人需要被告知 bundle
   由谁产出）。

## Non-goals

- 不改任何阶段的执行逻辑、退出码取值、swap 语义——本变更只新增观察面。
- 不引入调度（计划任务注册是运维动作，见 runbook_daily_update_scheduling.md）。
- 不做历史运行序列（只保留最近一次；历史在滚动日志里）。
- 不把状态工件接入 canonical 运行时/官方指标——它仅供操作人查看，
  `src/` 侧除 `data_pipeline.daily_update` 自身外无任何消费者。
- 不动 `web/` 其他页面（生产运维驾驶舱是并行分支
  `2026-08-14-ui-ops-cockpit` 的职责，两边不重叠）。

## Impact

- 行为兼容：不读状态工件的既有调用方完全不受影响；工件是新增文件。
- 治理风险低：写侧只在 `data_pipeline` 层（数据层允许 I/O），web 侧只读；
  状态工件永不进入 metrics/backtest/training 路径（治理测试钉守）。
- 测试：`tests/data_pipeline/test_daily_update.py` 新增状态工件用例
  （成功/失败/干跑/写失败不改变退出码/周末 no-op 也记录）；UI 读取器新增
  纯函数用例；既有数据检视只读治理测试保持绿。
