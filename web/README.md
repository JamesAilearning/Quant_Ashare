# Web Layer — Operator UI (Streamlit)

Purpose:
- Operator-facing console for the qlib trading system: configure/launch runs,
  inspect results and data health, review daily signals, record
  decisions. Launch: `python scripts/run_ui.py` (see `web/operator_ui/app.py`).

Pages (navigation groups):
- 日常决策 / 今日工作台 (`pages/today_workbench.py`) — read-only summary of
  data health, update status, serving identity, daily signal provenance, and
  current run exceptions; it never launches work or grants trading authority.
- 日常决策 / 运行中心 (`pages/run_center.py`) — manual daily update and dated
  daily-signal artifact generation through the audited runners.
- 日常决策 / 日度信号与人工决策 (`pages/daily_decision.py`) — read-only view of
  the dated `daily_recommendation_*.json` artifacts (model-meta banner +
  candidate table) plus the operator decision journal.
- 研究与验证 / 配置运行 (`pages/config_run.py`) — pipeline & walk-forward launch form
  with training guards and presets.
- 研究与验证 / 作业 (`pages/jobs.py`) — job list, filters, stop action, cleanup.
- 研究与验证 / 结果 (`pages/results.py`) — single-run dashboard (KPIs, NAV, charts,
  exports).
- 研究与验证 / 滚动验证 (`pages/walk_forward.py`) — fold-by-fold walk-forward
  inspection.
- 生产治理 / 生产运维 (`pages/ops_cockpit.py`) — serving identity, approval,
  recertification, and production readiness checks.
- 生产治理 / 数据检视 (`pages/data_inspect.py`) — read-only PRODUCTION bundle
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
  日度信号与人工决策 is read-only and triggers no jobs.
- Concurrency boundary: the console is single-operator; journal appends are
  single small binary-append writes, cross-process locking deliberately not
  implemented.
