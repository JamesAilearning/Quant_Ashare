# Proposal: disambiguate same-key double disclosures by announcement date (阶段8 Gate-3 Step-A follow-up)

## Why

The Gate-3 Step-A full ingest hit 27 provider "ambiguous duplicate" holes
(income 17 / balancesheet 7 / cashflow 3 — incl. 五粮液 000858.SZ income):
tushare returns, for a few `(ts_code, end_date, update_flag)` keys, TWO rows
with DIFFERENT content that `report_type`/`end_type` do NOT distinguish —
only `f_ann_date` differs (e.g. 五粮液 income 20250630/uf1: f_ann 20250828
revenue 527.7亿 vs a late row f_ann 20260430 revenue 235.1亿). The PR-1
ingest's logical key `(ts_code, end_date, update_flag)` cannot represent two
disclosures of one version, so it fail-louds and leaves the whole
instrument/endpoint as a hole — 2.7% of income names (incl. index
heavyweights) served all-NA. Honest, but fixable: the second row IS a
distinct, DATED disclosure event.

## What changes

Carry the announcement date in the versioned identity, per the
disclosure-of-record doctrine (#345):

- **Ingest logical key** extends to `(ts_code, end_date, update_flag,
  f_ann_date)` — two same-version rows with different `f_ann_date` are
  DISTINCT disclosure events, both retained (append-only, nothing dropped).
  A double-content collision on the FULL key (same announcement date) stays
  fail-loud — that is true ambiguity.
- **Disclosure of record** within a `(report_period, update_flag)` that has
  multiple dated disclosures = the EARLIEST-announced row (dated rows
  preferred over undated). Later same-version re-announcements are DATED
  restatements: recorded, never served over the record (original-first
  stays structural). `update_flag=0`-over-`update_flag=1` preference is
  unchanged.
- `resolve_current_versions` / `_assert_resolved` / the version-collapse
  audit operate on the extended key; the audit compares each version's
  RECORD row (earliest-announced), so a late re-announcement does not
  swap which rows are compared.

PIT safety is unchanged and structural: every served value is a period's
first/sole disclosure at its own `available_from_trade_date`; no restatement
— datable or not — is ever served over the record.

## Impact

- **Modifies** `v2-financial-pit-contract`: the versioned-ingest requirement
  (identity gains the announcement date) and the as-originally-reported
  requirement (within-version earliest-disclosure record rule).
- Touches `src/data/tushare/financial_statements.py` (LOGICAL_KEY + the
  ambiguity check keyed on the full identity),
  `src/data/pit/financial_pit_contract.py` (`_assert_resolved`,
  `select_disclosure_of_record`, `version_collapse_residual`).
- Research-only, NO factor; the view API and isolation are untouched.
- Re-ingest the 27 holey instruments; regenerate the Step-A canonical report
  (coverage on income fields rises ~1-3pp; floors re-derived by the SAME
  rule from the refreshed canonical measurement).

## Out of scope

- Any factor formula / Gate-3 Step-B pre-registration content.
- Announcement-date keying (`f_ann_date`→`ann_date` fallback), missingness,
  financial exclusion, view API — all unchanged.
- Canonical runtime / Alpha158 / `daily_recommend`.
