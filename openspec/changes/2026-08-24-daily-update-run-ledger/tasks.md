# Tasks: 2026-08-24-daily-update-run-ledger

## 实现

- [ ] 写侧 `daily_update.py`：终态追加一行台账（残尾隔离 + 单次 write + fsync）
- [ ] 写侧 `daily_update.py`：起跑时写一行带日期的运行边界
- [ ] 读侧：台账 reader（容错解析、坏行计数、按 provider 过滤）
- [ ] 读侧：`update_progress` 按边界切段，切不到就如实说不知道
- [ ] UI：今日工作台「近 N 次」条带

## 治理钉（必须机器强制）

- [ ] 台账**只可追加**：写侧不许出现覆盖/截断语义
- [ ] 台账写入失败**绝不改变退出码**（沿用状态工件的反向耦合契约）
- [ ] `--dry-run` 与 CLI 层退出（exit 2 / 17）不写台账、不写边界
- [ ] 阶段语义零改动：`_execute_daily_update` 逐字不变
- [ ] 边界格式：带完整日期时间 + normalized provider；别的 provider 的边界不采纳
- [ ] UI 复现台账、不自造判定；台账缺失/坏行要说出来，不许渲染成空条带

## 验证（每条要实测数字）

- [ ] 新守卫（写侧 / 读侧 / UI 三层）
- [ ] 变异全部被咬（含反向：dry-run 不写、外来 provider 边界不采纳）
- [ ] 分目录 pytest（CI 原命令）+ ruff + mypy --strict
- [ ] `openspec validate --strict`
- [ ] codex CLEAN + CI 七绿 → STOP 等 merge

## 刻意不做（写进 proposal，勿在评审中重开）

- 不存耗时字段（可由两个时间戳推出）
- 不写运行「结束」标记（状态工件与台账已回答）
- 不给台账加 CLI 覆盖开关（那会把状态工件那整套路径守卫一并请进来）
- 不做百分比进度条（fetch 只是六阶段里的第二个）
- 窗口里找不到边界时不扩大读取直到找到（日志无界，那是没有上界的读取）
