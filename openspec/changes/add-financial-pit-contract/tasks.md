# Tasks: financial-statement PIT contract + research data bridge (阶段8 Gate-2)

## OpenSpec (propose stage)

- [x] Gate-0 charter signed (H8-Q1, ≤3-candidate profitability family) —
      `docs/prereg/quality_profitability_charter.md`.
- [x] Gate-1 PIT feasibility memo passed, full-sample n=627 + survivorship
      split — `docs/prereg/quality_profitability_gate1_pit_preflight.md`.
- [x] Operator sign-off on the four contract defaults (restatement=original;
      financial exclusion=industry list; missingness=fail-loud, no 0-fill;
      `fin_exp` exposed, use deferred to Gate-3). Signed 2026-07-10; Step-0
      recon (`design.md`) + 3 impl decisions signed (research namespace +
      machine-enforced gate; append-only store w/ read-time latest-batch;
      StaticTradingCalendar from the canonical `day.txt`).
- [x] `openspec validate add-financial-pit-contract --strict` green.

## PR — Gate-2 (spec + implementation; research-only, NO factor)

_Split PR-1 (ingest + contract) / PR-2 (view + isolation), stacked._

- [x] **(PR-1)** Versioned raw ingest for income/balancesheet/cashflow with
      per-record provenance (endpoint, fetch batch, content hash, `update_flag`);
      preserve BOTH `update_flag` 0/1 rows; a re-fetch with changed content
      is recorded, never silently overwritten.
      → `src/data/tushare/financial_statements.py` + append-only store;
      `tests/data_pipeline/test_financial_statements_ingest.py`.
- [x] **(PR-1)** Contract fields: `report_period`, `announcement_date`
      (`f_ann_date`→`ann_date` fallback, recorded), `available_from_trade_date`
      (first trading day strictly after announcement), revision linkage.
      → `src/data/pit/financial_pit_contract.py` +
      `src/data/trading_calendar.py` (`next_trading_day_after` +
      `load_static_calendar_from_file`, canonical `day.txt`);
      `tests/pit/test_financial_pit_contract.py`. CLI:
      `scripts/data_pipeline/08_fetch_financials.py`.
- [x] **(PR-2)** `FinancialPITDataView` (sole access path): as-of carry-forward (NOT
      fillna); missing→NA; financial-sector exclusion via stable industry
      list + `oper_cost`-absence cross-check; expose charter input columns
      PIT-keyed incl. `adv_receipts`/`contract_liab` raw (reclassification
      documented); research-only, isolated from the canonical registry /
      runtime. → `src/research/financial_pit_view.py` (new research namespace).
- [x] **(PR-2)** Governance tests (BLOCKING): (a) value unreadable before
      `announcement_date`; (b) post-close announcement → next-trading-day
      effect; (c) original-disclosure-first (undatable restatement not
      backfilled); (d) missing field → fail-loud, never 0/median/latest/
      future (explicit `rd_exp`=NA test); (e) direct raw-filing read is
      rejected (no view bypass); (f) delist/membership boundary reuses the
      existing PIT universe; (g) coverage acceptance floor per Gate-1 §4
      (n=627) table — a field below floor fails loud.
- [x] **(PR-2)** Isolation test: the view is not importable from / wired into the
      canonical feature registry, training, or `daily_recommend`.
      → `tests/governance/test_financial_pit_view_isolation.py` (AST reverse
      scan: no non-research src/ imports `src.research.*`; forward + sole-path
      checks); `src/research/` documented in CLAUDE.md layout.
- [x] **(PR-2)** Whitelist/governance sweep: no new canonical-runtime qlib call sites;
      the research view stays outside the canonical import graph (qlib-free at
      import; the isolation reverse-scan is the sweep).

## Must-not-touch

- No factor computation (GPA/PROF/OP), no candidate universe, no backtest.
- No Alpha158 / training-config / canonical-runtime / `daily_recommend`
  change; REGEN-2 anchor stays green.
- No CSI800 expansion.
