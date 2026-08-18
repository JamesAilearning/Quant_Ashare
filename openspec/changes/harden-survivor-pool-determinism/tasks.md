# Tasks

- [x] `validator.canonical_expr_digest`：表达式规范串的 sha256；
      `filter_correlated` 扫描顺序改 `(-fitness, canonical_expr_digest)`。
      回归：另起 `PYTHONHASHSEED` 不同的子进程，摘要必须逐字相同。
- [x] `validator.ValidationError`：求值失败与非 DataFrame 返回改为拒绝，
      不再保留因子。回归：制造非 KeyError 的求值失败 → 拒绝。
- [x] `evaluator.max_abs_corr_with_skips`：返回 (max, 不可比对数)；
      `max_abs_corr` 逐字委托（既有三处调用方零影响）。
      `filter_correlated` 见到任何不可比配对即拒。
      回归：稀疏对 (0.0, 1) vs 稠密对 (>0.99, 0)。
- [x] `gp_engine._has_run` + checkpoint 持久化 `has_run` / `allowed_terminals`。
      缺任一字段的老 checkpoint **拒绝加载**（codex #448 r1 P1）：把一次
      已建立的 run 当成全新引擎是**宽松**方向而非保守 —— 恢复的种群与
      缓存来自那次 run，池守卫却会被跳过。措辞沿用 `_verify_pit_binding`
      的先例（re-mine；挖掘便宜，无法核验的出处不便宜）。
      **行为破坏性**：早于本改动的 checkpoint 一律不可 resume。
- [x] `score_expression` 终端池校验（与算子池校验对称）；已跑过的引擎优先
      复用已建立池，`None` 解析为 V1 哨兵而非"未知"。
- [x] `run()` 拒绝改变已建立池的 resume、以及引用池外终端的预填种群。
- [x] 全量 + mypy + ruff。
- [ ] Spec delta 归档时并入 `openspec/specs/v2-factor-mining-foundations`。
