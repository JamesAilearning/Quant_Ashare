# Tasks: 2026-08-22-stage-failure-reason

## 实现

- [x] `_capture_stage_errors()`：作用域内收集阶段自己的 ERROR 行
- [x] `_stage_detail()`：折成单行、有上限、截断必须声明、空捕获不编造
- [x] 四个阶段调用点全部套上（fetch / snapshot / 五个 rebuild / validate）
- [x] 工作台失败卡片与待办队列共用 `failed_update_summary`
- [x] 退出码 11 的文案（UI 常量 + 运维手册 + 编排器 docstring）

## 验证（每条要实测数字）

- [x] 生产者守卫 23 条 + 7 subtests
- [x] 消费端与退出码守卫 9 条 + 3 subtests
- [x] 变异 17 条全部被咬（含「捕获点挂错 logger」这条静默空转变异）
- [ ] 全量 `tests/data_pipeline` + `tests/logic` + `tests/governance`
- [ ] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge
