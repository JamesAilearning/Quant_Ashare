# Tasks: 2026-08-14-data-inspect-pit-subprocess

## 子进程运行器（web 只读 helper）
- [x] `web/operator_ui/pit_validation_runner.py`：子进程调 06 CLI +
      `--report-json` 至临时目录；`PITRunResult` 区分
      ok / run_failed / corrupt_report / timeout / launch_failed；
      报告形状校验 fail-loud；UTF-8 text-mode；默认超时 900s；
      `python` 可覆盖（默认 `sys.executable`）
- [x] codex P2：临时目录创建失败 → 醒目的 run_failed（不再逃逸异常）；
      清理由 `rmtree(ignore_errors=True)` 兜底，绝不覆盖已算出的结果

## 页面
- [x] `data_inspect.py` Section 3 改用 runner；删除进程内
      `PITValidator` / `QlibRuntimeInitError` import 与分支；文案标明
      子进程语义；渲染（徽章/表格/expander）不变

## 测试与验证
- [x] `tests/logic/test_pit_validation_runner.py`：命令形状 + UTF-8 钉、
      ok / exit2+报告 / run_failed / corrupt_report ×3 / timeout /
      launch_failed / 临时目录不泄漏 / CLI 路径防漂移钉
      + tempdir 创建失败分类 ×1 + 清理不覆盖结果钉 ×1
- [x] 治理：`test_data_inspect_readonly.py` 新增钉——页面 import 行不得
      出现 `PITValidator` / `qlib_runtime`；既有只读扫描保持绿
- [ ] `openspec validate --strict`；目标测试 + `tests/logic/` +
      `tests/governance/` 全绿；`ruff check` 全绿
