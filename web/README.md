# Web Layer — Operator UI (Streamlit)

Purpose:
- Operator-facing console for the qlib trading system: configure/launch runs,
  inspect results and data health, review daily recommendations, record
  decisions. Launch: `python scripts/run_ui.py` (see `web/operator_ui/app.py`).

Pages (navigation groups):
- 运行 / 作业 (`pages/jobs.py`) — job list, filters, stop action, cleanup.
- 运行 / 配置运行 (`pages/config_run.py`) — pipeline & walk-forward launch form
  with training guards and presets.
- 运行 / 今日推荐 (`pages/daily_decision.py`) — read-only view of the dated
  `daily_recommendation_*.json` artifacts (model-meta banner + candidate
  table) plus the operator decision journal.
- 分析 / 结果 (`pages/results.py`) — single-run dashboard (KPIs, NAV, charts,
  exports).
- 分析 / 滚动验证 (`pages/walk_forward.py`) — fold-by-fold walk-forward
  inspection.
- 分析 / 数据检视 (`pages/data_inspect.py`) — read-only PRODUCTION bundle
  inspector (governance-test enforced read-only).

Boundary:
- No runtime trading logic in this layer.
- This layer must consume explicit services/contracts from `src/`.
- Official metrics governance remains canonical-path-only and must not be
  redefined in UI code.
- **Decision journal** (`web/operator_ui/decision_journal.py`,
  `QUANT_DECISION_JOURNAL_DIR`): append-only JSONL owned by this layer. It is
  operator state — NEVER an input to official metrics, backtests, training or
  promotion decisions; no module under `src/` may reference it (a source-scan
  test in `tests/logic` enforces zero references). Apart from journal appends,
  今日推荐 is read-only and triggers no jobs.
- Concurrency boundary: the console is single-operator; journal appends are
  single small binary-append writes, cross-process locking deliberately not
  implemented.
