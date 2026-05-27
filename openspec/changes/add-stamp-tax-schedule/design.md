## Context

CN A-share stamp tax has changed twice in the period this codebase
realistically backtests: 2008-09-19 (the 0.1% sell-only era began)
and 2023-08-28 (halved to 0.05%). The canonical cost model holds a
single scalar applied to every sell across the entire backtest
period, which is biased on any window that spans either transition.
Audit P0-4 surfaced this on the two walk-forward shipped configs.

The simplest fix — emit the right rate per day — is not directly
supported by qlib's `Exchange` API, which takes `close_cost` as a
scalar. Designing around that constraint without falling into the
"silent fallback" anti-pattern this codebase is allergic to is the
core trade-off of this change.

## Goals / Non-Goals

**Goals:**
- Make the schedule the canonical representation of stamp tax across
  the contract, runtime, configs, and tests.
- Embed the two known historical transitions in a default so common
  configs don't have to spell them out.
- Surface every schedule-crossing run at the WARN level with both
  the per-segment rates AND the scalar weighted-average that was
  applied — so the operator can decide whether to split the
  backtest, override the schedule, or accept the approximation.
- Forbid silent regression to a single scalar via a governance test
  on field names in the public contract + a YAML-key sweep across
  all shipped configs.

**Non-Goals:**
- Do NOT teach qlib's `Exchange` to take time-varying cost. That
  would either fork qlib or vendor it; neither is in scope.
- Do NOT split the backtest into multiple qlib calls and reconcile
  the per-segment outputs. The reconciliation surface (matching
  portfolios across the join, summing risk_analysis tables,
  reconciling positions) is its own multi-PR redesign; the
  trading-day weighted scalar is a documented approximation that's
  strictly better than the current single-pre-reform rate.
- Do NOT model pre-2008-09-19 stamp tax (which was both-side, not
  sell-only). Pre-2008 backtests are out of scope for the shipped
  default; operators who need them can extend the schedule
  explicitly.
- Do NOT change `commission_rate`, `slippage_bps`, or `min_cost` to
  schedules. They have not changed historically; if they ever do,
  that's a separate spec change.

## Decisions

1. **Schedule as a tuple of frozen `StampTaxScheduleEntry`.**
   - Decision: `stamp_tax_schedule: tuple[StampTaxScheduleEntry, ...]`
     on `CanonicalExchangeCostModel`. Each entry is
     `@dataclass(frozen=True)` with `effective_from: date` and
     `bps: float`.
   - Rationale: tuples + frozen dataclasses match the rest of the
     contract layer; immutability composes with `asdict()` for
     provenance hashing.
   - Trade-off: tuple-of-dataclass is heavier than a flat
     `list[tuple[date, float]]`, but the named fields prevent
     ambiguity ("which side is the date?") and survive serialization
     intact.

2. **Single schedule, no separate pre/post fields.**
   - Decision: one ordered schedule covering all eras, not a
     `pre_reform_bps` + `post_reform_bps` pair.
   - Rationale: adding a third historical transition (or modelling
     pre-2008) is a schedule append, not a schema migration.
   - Trade-off: validators have to enforce monotonicity, which is
     ~5 lines of code.

3. **Trading-day-weighted scalar for cross-period runs.**
   - Decision: when the backtest period spans `K ≥ 2` schedule
     entries, the helper returns `sum(days_in_segment_i × rate_i) /
     sum(days_in_segment_i)`. Weight by trading days, not calendar
     days (a long weekend at the boundary shouldn't shift the
     weighting toward the holiday side).
   - Rationale: a single scalar is what `exchange_kwargs[
     "close_cost"]` accepts. Time-weighting is the unbiased
     single-scalar approximation under uniform turnover assumption.
     Strategy-specific weighting (by expected turnover) would
     require knowing the strategy's daily turnover profile, which
     is what we're trying to backtest in the first place — circular.
   - Trade-off: an operator running a strategy with extreme
     beginning-of-period or end-of-period turnover gets a slightly
     biased scalar. The transition WARN explicitly tells them to
     split the backtest if they want exact per-segment cost.

4. **Pre-schedule period is a hard error, not a silent extrapolation.**
   - Decision: when `period_start < schedule[0].effective_from`,
     `compute_effective_stamp_tax_bps` raises
     `CanonicalBacktestContractError`. The error message names both
     dates and tells the operator how to extend the schedule.
   - Rationale: silently extrapolating the earliest rate backwards
     would falsely model pre-2008 sell-only tax (it was both-side).
     Forcing an explicit opt-in matches the "no silent fallback"
     governance posture and pushes the historical correctness
     decision back to the operator who owns the backtest.
   - Trade-off: existing pre-2008 backtests (none in this repo) fail
     loudly on first run after this change.

5. **YAML migration: hard error on the legacy key.**
   - Decision: `PipelineConfig.from_yaml(...)` and
     `WalkForwardConfig.from_yaml(...)` detect `stamp_tax_bps` in
     the input mapping and raise a `ConfigError` whose message:
     (a) lists the new key `stamp_tax_schedule`,
     (b) shows the two-entry default as a copy-pasteable YAML snippet,
     (c) cites this change name and `audit P0-4`.
   - Rationale: silently dropping the legacy key would let an
     operator's existing YAML run with the wrong cost model and
     never notice. Auto-coercing the scalar into a single-entry
     schedule would silently apply the wrong rate to half the run.
   - Trade-off: every operator with a local YAML must edit it once.
     The migration is a 2-line diff and the error message contains
     the snippet to paste.

6. **PipelineConfig accepts a `Sequence[Mapping[str, Any]] | None`,
   not the dataclass directly.**
   - Decision: the user-facing config field accepts YAML-shaped
     data (list of `{effective_from, bps}` dicts) OR `None` (use
     `CN_STAMP_TAX_SCHEDULE_DEFAULT`). The conversion to the
     `tuple[StampTaxScheduleEntry, ...]` shape happens in
     `pipeline.py` at the point where `CanonicalExchangeCostModel`
     is constructed.
   - Rationale: keeps `PipelineConfig` deserialisable from plain
     YAML without the user having to spell out the dataclass.
   - Trade-off: `PipelineConfig` and the contract layer disagree on
     types for one field. Documented with a doc-string note + a
     pipeline-side conversion helper.

7. **WARN log emitted at most once per BacktestRunner invocation.**
   - Decision: the WARN about transitions is emitted exactly once
     per `BacktestRunner.run` call. Walk-forward runs that invoke
     `run()` per fold get one WARN per fold, which is the right
     granularity — an operator with 30 folds spanning the reform
     gets 30 reminders that the per-fold rate is an approximation.
   - Rationale: per-day WARN would flood logs (4000+ messages).
     Per-run WARN matches the existing logging conventions in this
     module (`signal_to_execution_lag=0`, PIT-bypass warnings).

## Risks / Trade-offs

- **Fixture regeneration**. The fold-0 baseline expected metrics
  were captured under `stamp_tax_bps=10.0`. After this change, the
  post-Aug-2023 portion of the fixture's test window (if it spans
  the reform) is corrected ~5 bp/sell. We address this by:
  (a) regenerating the fixture as part of task 5,
  (b) bumping the tolerance on `annualized_return` from 0.005 to
      0.010 — still tight enough to catch real regression, but
      loose enough to absorb the one-shot rate correction.
  If task 5 chooses (a), the fixture's git history captures the
  rate-correction commit; if (b), the bump is documented in the
  test docstring.

- **Existing local YAMLs**. Out-of-tree operator configs will fail
  to load until they migrate. The error message contains the
  migration snippet; CI of the repo itself is unaffected because
  shipped configs are migrated in this change.

- **Cross-period research approximation**. Some quant teams want
  exact per-segment cost. The schedule-cross WARN explicitly tells
  them to split the backtest; the right Phase E follow-up is to
  add a per-fold split in `WalkForwardEngine` so each fold's
  window either entirely precedes or entirely follows each
  transition. Out of scope here.
