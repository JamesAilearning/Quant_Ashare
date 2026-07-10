# Proposal: financial-statement PIT contract + research data bridge (阶段8 Gate-2)

## Why

阶段8's quality-factor line (H8-Q1, "profit real and sustainable") needs
PIT-correct fundamentals, and the repo does not have them yet:

1. **Gate-0 charter (signed)** —
   `docs/prereg/quality_profitability_charter.md` registers a ≤3-candidate
   profitability family (C1 GPA, C2 PROF, C3 cash-based OP) as ONE FWER
   batch. Every candidate consumes financial-statement inputs.
2. **Gate-1 feasibility memo (passed)** —
   `docs/prereg/quality_profitability_gate1_pit_preflight.md`: tushare
   income/balancesheet/cashflow expose every charter-required field WITH
   `end_date`/`ann_date`/`f_ann_date`/`update_flag`; announcement lags the
   period end ~3–4 months; the full-sample n=627 (incl. 21 delisted)
   coverage holds and shows NO survivorship gap (delisted ≥ active on most
   fields). All three candidates are feasible ex-financials; none
   not-feasible.
3. **The gap is real** — `docs/pit/pit_universe_design.md` §4.5 lists
   Fundamentals (financial statements) as **Phase-E.2 backlog**: "Not
   currently used as features … PIT join uses publication date." This change
   delivers exactly Phase-E.2, research-only.

This change ships the DATA CONTRACT + BRIDGE only. It computes NO factor;
the candidate formulas, C2's interest-term / R&D-window choice, and C3's
accrual set are Gate-3 decisions under confirmatory pre-registration.

## Scope decision (operator-signed via the Gate sequence)

Gate-0 signed H8-Q1; Gate-1 passed. The four Gate-1 caveats are resolved
here as CONTRACT defaults (candidate-definition choices deferred to Gate-3):

- **Restatement** → serve the `update_flag=0` as-originally-reported value
  keyed to `f_ann_date`. tushare assigns NO independent announcement date to
  later restatements, so undatable revisions are NOT back-applied; the limit
  is recorded as a known PIT risk.
- **Financial-sector exclusion** → a stable industry list (banks/brokers/
  insurers), cross-checked against `oper_cost` absence; field-absence is the
  cross-check, NOT the primary rule.
- **Missingness** → keep missing, fail loud, report explicitly; NEVER fill
  0/median/latest/future. `rd_exp`-missing stays NA (the −21pp delisted gap
  is a survivorship-sensitive signal, not a fill target).
- **Field exposure** → the view EXPOSES `fin_exp` (and all charter inputs);
  whether C2 uses `fin_exp` vs drops the interest term is Gate-3.

## What (the contract + the bridge)

- **Versioned raw ingest + provenance** for income/balancesheet/cashflow:
  per-record source endpoint, fetch batch, content hash, `update_flag`;
  BOTH `update_flag` 0/1 rows preserved (no silent dedup/overwrite).
- **The PIT contract** — every observation carries `report_period`
  (`end_date`), `announcement_date` (`f_ann_date`, fallback `ann_date`),
  `available_from_trade_date` (the first trading day strictly after
  announcement), and the revision linkage. Joins use
  `available_from_trade_date`; the period-end date is NEVER an availability
  date.
- **`FinancialPITDataView`** — the SOLE research-side access path. As-of
  carry-forward of the latest already-announced statement (NOT fillna);
  missing stays missing; financial issuers excluded; exposes the charter
  input columns PIT-keyed (incl. `adv_receipts` AND `contract_liab` both
  raw — the 2020 预收→合同负债 reclassification is DOCUMENTED, the coalesce/
  differencing left to Gate-3). Physically and semantically isolated from
  the canonical feature registry and production runtime.
- **Governance tests** — announcement look-ahead refusal; next-trading-day
  effect; original-disclosure-first; missing→fail-loud; direct-raw-read
  rejection; delist/membership boundary reuse of the existing PIT universe;
  coverage acceptance per the Gate-1 n=627 table.

## Explicitly NOT in this change (Gate-3)

- Any quality-factor formula (GPA / PROF / cash-based OP).
- C2's `fin_exp`-vs-drop-interest and the `rd_exp` window/variant choice.
- C3's final accrual set (the view exposes the raw inputs; the coalesce and
  the Δ are factor logic).
- Any Alpha158 / training-config / production / `daily_recommend` change.
- CSI800 universe expansion; any candidate backtest.

## Known PIT limitation (recorded, not fixed here)

- **Restatement undatable**: a later-revised period cannot be dated to its
  restatement announcement; the contract serves the original disclosure and
  flags the risk (charter §7 falsifier).
- **`rd_exp` survivorship (directional)**: the delisted cohort reports R&D
  less (59% vs 80% active, Gate-1 §4b). Because C2/PROF needs `rd_exp`,
  dropping missing-`rd_exp` names would preferentially drop losers → C2
  selection bias. The contract keeps such names in the universe with
  `rd_exp`=NA; how C2 handles them (no-R&D variant, or coverage-conditional
  reporting) is a Gate-3 decision. Recorded so Gate-3 cannot forget it.

## Anchor / production impact

None. Research-only, isolated from the canonical runtime; no factor, no
model, no `daily_recommend` change; the REGEN-2 anchor and the Alpha158 path
are untouched. CI judges.

## Out of scope

- Factor computation and the Gate-3 confirmatory pre-registration.
- CSI800 expansion (parked; its data foundation is green, but the sequencing
  puts quality-factor validation on CSI300 first).
