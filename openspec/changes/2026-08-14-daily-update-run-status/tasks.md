# Tasks: 2026-08-14-daily-update-run-status

## 写侧（data_pipeline）
- [x] `DailyUpdateConfig` 新增 `status_path: Path | None = None`（None → 派生
      `provider_dir.with_name(provider_dir.name + ".daily_update_status.json")`）；
      CLI 加 `--status-path`
- [x] `run_daily_update` 开始时写 `state="running"`，每个终态写
      `state="finished"` + `exit_code` + `failed_stage` + `detail`；
      临时文件 + `os.replace` 原子落盘；`--dry-run` 不写
- [x] 状态写失败 → ERROR 日志，退出码不变（钉）
- [x] codex P1：`__post_init__` 拒绝与 provider/tushare 目录重叠或别名
      registry/reference 的 `--status-path`（ValueError → CLI exit 2，
      任何写入之前）
- [x] codex P1 第二轮：防护扩展到 `<provider>.new` / `<provider>.bak`
      交换暂存/回滚路径（含后代）；无名路径（`.`/根）同样在配置构造拒绝

## 读侧（web）
- [x] `web/operator_ui/update_status.py`：派生路径 + 读取 + 形状校验
      （纯函数；缺失/损坏/运行中三态区分）
- [x] codex P2：完整 schema 校验——`schema_version` 必须受支持、
      `run_date`/`started_at` 必备、finished 记录必备 `finished_at`；
      截断/未知版本记录一律 corrupt，绝不渲染成绿色成功
- [x] codex P2 第二轮：finished 记录必备 `failed_stage`/`detail` 键并
      类型校验，且强制 exit_code ⇔ failed_stage 成功/失败不变式
- [x] 数据检视页「上次数据更新」小节（完整性戳之前；只读；不 import、
      不调用 `daily_update` / `bundle_swap`——散文提名允许，r11 对齐）

## 测试与验证
- [x] `tests/data_pipeline/test_daily_update.py`：状态工件用例(终稿 +24,27→51)
      （成功/失败阶段键/干跑不写/写失败不改退出码/周末 no-op 记录 exit 0）
      + status-path 防护用例 ×7
- [x] `tests/logic/test_update_status_reader.py`：读取器纯函数用例
      终稿 39 个方法:严格 schema/三态分类/归属比对/读写两侧钉
- [x] 治理：`test_data_inspect_readonly.py` 保持绿；新增钉——状态工件不得
      被 `src/` 内除 `data_pipeline.daily_update` 外的任何模块引用
- [x] `openspec validate --strict`；目标测试 + `tests/logic/` +
      `tests/governance/` 全绿；`ruff check` 全绿  ← 每轮修复后重跑,
      末次:openspec 47/47,全量快速套件 4408 passed / 30 skipped(此计数为合并时点快照,权威以 CI 为准)
