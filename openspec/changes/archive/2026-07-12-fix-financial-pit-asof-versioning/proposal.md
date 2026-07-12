# Proposal: fix FinancialPITDataView as-of version selection (阶段8 Gate-2 correction)

## Why

Gate-2 shipped `v2-financial-pit-contract` with the as-of rule "serve the
`update_flag=0` originally-disclosed value." A 7-name smoke over the merged
view (as-of 2024-06-30) exposed a correctness bug: the view serves report
periods that are 1–2 YEARS stale (格力 2022-03-31, 宁德 2022-12-31,
茅台/中兴/恒瑞 2023-06-30, 平安 2023-09-30) — only 招行 was current
(2024-03-31).

**Mechanism:** the provider retains only `update_flag=1` for the most recent
1–2 years of report periods (no `update_flag=0` row exists for them). The
"`update_flag=0` filter" discards those periods and carry-forwards to a much
older `update_flag=0` period, lagging the served value 1–2 years.

This is a bug, not honest PIT: a 2024-Q1 report already publicly announced by
2024-06-30 is genuinely available, and the view must serve it. A quality
factor built on 1–2-year-stale fundamentals measures the wrong thing. Left
unfixed, every Gate-3 factor would be frozen on stale inputs.

**Root-cause evidence (smoke):** for `report_period`s that have BOTH
`update_flag` rows (7 names, 116 both-version periods), revenue
`update_flag=0` vs `=1` differ in 0.00% of cases — the provider's
`update_flag=1` is a version marker, not a real restatement (corroborating
the Gate-1 memo §3 finding that the 0/1 rows share one `ann_date` and one
value). Discarding `update_flag=1`-only periods is therefore pure
over-conservatism with zero PIT benefit.

## What changes

Reframe the as-of rule from an `update_flag=0` FILTER to a per-period
first/sole-disclosure selection keyed to `available_from_trade_date`:

- For each `report_period`, prefer the `update_flag=0` row; if none exists,
  serve the sole `update_flag=1` row (the period's original disclosure of
  record).
- As of a trade date, serve the LATEST `report_period` whose
  `available_from_trade_date` ≤ the date — never carry-forward from an older
  period when a newer one is already available.
- A both-version period ALWAYS resolves to `update_flag=0` — a restated value
  is never served over its original.

The rule's PIT safety is **structural** (only first/sole disclosures served,
never a restatement over its original), so it does NOT depend on
`update_flag=0`==`update_flag=1` generalizing. A new governance audit measures
the differing-version fraction across all charter fields / full CSI300-ever to
SIZE the restatement residual for the honesty envelope; the one undetectable
case (a recent `update_flag=1`-only that silently corrects a no-longer-stored
original) is documented as an inherent provider limitation, bounded by the
audited restatement rate.

## Scope decision (operator + reviewer signed)

The Gate-1 memo §3 and the Gate-2 smoke both show `update_flag` is a version
marker, not a datable restatement. Architect cross-review confirmed the fix
and that it must land as an OpenSpec **MODIFIED** change: it alters the
observable semantics of a signed, archived requirement, and a silent bugfix
would be governance drift. The prior "serve `update_flag=0`" wording was an
architect spec error — caught by the Gate-2 smoke before any factor was built
(exactly why Gate-2 precedes Gate-3).

## Impact

- **Modifies** `v2-financial-pit-contract`: the "as-originally-reported"
  version-selection requirement; **adds** a version-collapse audit requirement.
- Touches `src/research/financial_pit_view.py` +
  `src/data/pit/financial_pit_contract.py` (`resolve_current_versions` /
  `build_contract_frame`) — research-only, still NO factor, still isolated
  from canonical runtime (the isolation governance test stays green).
- Re-run the Gate-2 smoke: as-of 2024-06-30 must serve 2024-Q1 for the 7
  names (staleness eliminated); confirmed in the exploratory fix.

## Out of scope

- Any quality-factor formula or Gate-3 work (this only corrects the data
  bridge the factors will read).
- Ingest (both `update_flag` rows are already preserved), announcement-date
  keying, missingness, and the financial-sector exclusion — all unchanged.
- Canonical runtime / Alpha158 / `daily_recommend`.
