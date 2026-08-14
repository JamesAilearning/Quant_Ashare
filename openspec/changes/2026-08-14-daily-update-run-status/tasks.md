# Tasks: 2026-08-14-daily-update-run-status

## 写侧（data_pipeline）
- [x] `DailyUpdateConfig` 新增 `status_path: Path | None = None`（None → 派生
      `provider_dir.parent / "daily_update_status.json"`）；CLI 加 `--status-path`
- [x] `run_daily_update` 开始时写 `state="running"`，每个终态写
      `state="finished"` + `exit_code` + `failed_stage` + `detail`；
      临时文件 + `os.replace` 原子落盘；`--dry-run` 不写
- [x] 状态写失败 → ERROR 日志，退出码不变（钉）

## 读侧（web）
- [x] `web/operator_ui/update_status.py`：派生路径 + 读取 + 形状校验
      （纯函数；缺失/损坏/运行中三态区分）
- [x] 数据检视页「上次数据更新」小节（完整性戳之前；只读；源码不出现
      `daily_update` / `bundle_swap` 字样）

## 测试与验证
- [x] `tests/data_pipeline/test_daily_update.py`：状态工件用例 ×5
      （成功/失败阶段键/干跑不写/写失败不改退出码/周末 no-op 记录 exit 0）
- [x] `tests/logic/test_update_status_reader.py`：读取器纯函数用例
- [x] 治理：`test_data_inspect_readonly.py` 保持绿；新增钉——状态工件不得
      被 `src/` 内除 `data_pipeline.daily_update` 外的任何模块引用
- [x] `openspec validate --strict`；目标测试 + `tests/logic/` +
      `tests/governance/` 全绿
