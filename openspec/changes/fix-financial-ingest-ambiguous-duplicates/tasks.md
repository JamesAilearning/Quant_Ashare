# Tasks: disambiguate same-key double disclosures by announcement date

## OpenSpec (propose stage)

- [x] Step-A report documented the 27 holes + the disambiguation direction
      (key gains `f_ann_date`; earliest disclosure = record) — operator
      approved this order of work.
- [x] `openspec validate fix-financial-ingest-ambiguous-duplicates --strict` green.

## Implementation (research-only, NO factor)

- [x] `financial_statements.py`: `LOGICAL_KEY` gains `f_ann_date` (non-blank
      strictness stays on the original three columns via `NONBLANK_KEY_COLS` —
      `f_ann_date` MAY be NA); the one-fetch ambiguity check fires only on a
      full-identity double-content collision (`dropna=False` so NA announcement
      dates group as one key).
- [x] `financial_pit_contract.py`: `_assert_resolved` keys on the extended
      identity; `_per_version_records` + `select_disclosure_of_record` pick per
      `(ts_code, report_period)`: prefer `update_flag=0`, within a version the
      EARLIEST-announced dated row (dated preferred over undated);
      `version_collapse_residual` compares each version's RECORD row.
- [x] Tests: same-triple different-`f_ann_date` rows both ingest (no hole);
      full-identity double content still refused; record selection serves the
      earliest-announced row and never the late re-announcement (contract +
      end-to-end view); audit compares records; existing suites green.
- [x] Re-ingested the 23 holey instruments (union of the 27 holes):
      income 17→0, cashflow 3→0, balancesheet 7→1. The single residual hole
      (000627.SZ 天茂, delisted) is a TRUE ambiguity — same full identity
      (same `f_ann_date`), `comp_type` 3(保险) vs 1(工商) dual-format filing;
      which format to trust is a semantic decision the key deliberately does
      not carry — stays a loud hole, recorded in the report.
- [x] Regenerated the Step-A canonical report (floors PASS across the full
      window incl. coalesce); re-derived `COVERAGE_FLOORS` by the SAME rule —
      every floor tightened or held (revenue/total_revenue .95→.98,
      oper_cost/fin_exp .95→.97, sell_exp .94→.96, admin_exp .95→.98,
      rd_exp .88→.90, total_assets/equities/n_cashflow_act .97→.98; others
      unchanged). Candidate windows UNCHANGED: C1 2018 / C2 2019 / C3 2019.
- [x] ruff + mypy --strict green; logic/governance/data_pipeline suites green;
      isolation gate green.

## Must-not-touch

- No factor computation; no Gate-3 Step-B content.
- Announcement-date keying (`f_ann_date`→`ann_date` fallback), missingness,
  financial exclusion, view API — unchanged.
- Canonical runtime / Alpha158 / `daily_recommend` untouched.
