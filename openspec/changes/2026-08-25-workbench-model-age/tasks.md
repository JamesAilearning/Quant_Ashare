# Tasks: 2026-08-25-workbench-model-age

## 实现

- [x] `model_age_rows`：措辞层翻译 `retrain_window` 的字段，零自造判定
- [x] 身份卡 ensemble 分支接线 `model_age_rows(retrain_window(incumbent, cn_today()))`
- [x] 窗口行可见文案自报「推导」：label 带（推导），值带 spacing pin 数值
      与「仓库无机器可读的重训到期锚」告白（codex #467 P1）
- [x] `known=False` 如实说推导不了并带原因

## 验证（每条要实测数字）

- [x] 新守卫 4 条：known 三行 / unknown 如实 / 接线源码钉 / 披露三件套
      （label 推导字样 + pin 数值 + 无锚告白）
- [x] 集中 import 守卫登记 `retrain_window`（`test_bundle_staleness_budget`）
- [x] logic 全量 4266 passed / 28 skipped / 572 subtests；定向 26 passed /
      17 subtests；ruff / mypy --strict 全过

## 流程说明

实现与规格同 PR（#467）：codex 首轮判披露缺失（已修），次轮判规格缺席
（本 change 补齐）——两条意见都收，按仓的 spec-first 纪律回填并全文如实。
