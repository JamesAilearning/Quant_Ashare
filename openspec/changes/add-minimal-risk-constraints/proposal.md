## Why

`src/core/risk_constraints.py` ships a fail-closed stub:
``RiskConstraintEngine.apply()`` raises on any call. There is no
risk-control layer in the canonical backtest path. A portfolio
that holds 100% of capital in a single stock, 100% in a single
board, or runs without any cash buffer all pass every existing
guard and produce official metrics. For a system the operator
could conceivably point at real money, "no risk constraints"
is the highest-priority outstanding gap in the V2 governance
baseline. Audit P0-1.

## What Changes

Introduce ``MinimalRiskConstraints`` — a frozen dataclass that
declares four constraints plus an enforcement mode, and a method
``apply(positions_map)`` that returns the violations + a
potentially-clipped positions map. Constraints (with documented
defaults):

| Constraint | Default | Semantics |
|---|---|---|
| ``max_per_name`` | 0.05 | Single-instrument weight ≤ 5% NAV |
| ``max_per_board`` | 0.40 | Aggregate weight per A-share board ≤ 40% NAV (uses ``board_heuristic``) |
| ``cash_buffer_min`` | 0.01 | Cash share of NAV ≥ 1% |
| ``max_leverage`` | 1.00 | Sum of absolute instrument weights ≤ 1.0 (long-only assumption + no leverage) |

Two enforcement modes:

* ``RAISE`` — any violation surfaces as ``RiskConstraintError`` at
  the end of the backtest. The error message lists every
  violation across days so the operator sees the full picture
  in one shot rather than fixing one and getting the next.
* ``WARN_AND_CLIP`` — each violation logs a WARN, and a
  CLIPPED positions map is returned alongside the original.
  Use case: live deployment where a single-day single-name
  violation should not abort the whole run.

Integration into ``BacktestRunner.run``:

* New optional kwarg ``risk_constraints: MinimalRiskConstraints | None = None``.
* ``None`` preserves existing behaviour **but emits a WARN**
  ("backtest ran with NO risk constraints active") so operators
  can't be unaware they're running without a safety net.
* When supplied, ``apply()`` runs against the qlib-produced
  positions_map AFTER the official return_series / risk_analysis
  are computed (so those numbers reflect what qlib actually
  ran — clipping is post-hoc and informational, not a
  retroactive rewrite of official metrics).
* The clipped positions map (when ``WARN_AND_CLIP``) becomes the
  ``positions`` field on ``CanonicalBacktestOutput``; the
  unclipped map is preserved on a new sibling field for downstream
  diff / audit.

Governance test asserting ``risk_constraints.py`` exports
``MinimalRiskConstraints``, ``RiskConstraintMode``, and the four
default constants — so a future refactor cannot silently remove
the public surface.

### What this change does NOT do

* Does NOT replace ``TopkDropoutStrategy`` with a constraint-aware
  one. Pre-trade clipping requires either bypassing qlib's strategy
  or forking it; both are too invasive for a "make risk
  constraints exist at all" PR. Pre-trade integration belongs to
  a follow-up under the same capability.
* Does NOT retroactively adjust ``return_series`` /
  ``risk_analysis`` based on clipping. The official metrics
  reflect what qlib's executor actually ran with the unclipped
  signal. Post-hoc clipping at the positions layer is informational.
* Does NOT model sector / industry caps. The existing
  ``board_heuristic`` is the only zero-cost classifier available
  today; a real industry constraint waits on Phase E industry
  artifacts.
* Does NOT add stop-loss, drawdown circuit-breakers, or
  turnover caps. Those are dynamic constraints (depend on
  per-day P&L history) and a separate spec change.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- ``v2-canonical-backtest-contract``: the runtime gains an
  optional post-trade risk-constraint layer. The canonical
  official-metrics anchor, the qlib ``backtest.backtest``
  callable, ``return_series``, and ``risk_analysis`` are
  unchanged. Only the ``positions`` field and the optional
  ``BacktestRunner.run`` kwarg change.

## Impact

- **Migration**: No breaking changes for existing callers. The
  new ``risk_constraints`` kwarg defaults to ``None``, which
  preserves the previous unconstrained behaviour. A WARN log
  prompts the operator to opt in.
- **Numeric drift**: When ``risk_constraints`` is omitted, NONE.
  When supplied with ``RAISE`` mode, runs that previously
  silently violated now raise — surfacing existing portfolio
  concentration issues. When supplied with ``WARN_AND_CLIP``,
  the ``positions`` field changes but ``return_series`` and
  ``risk_analysis`` are unchanged.
- **Backwards-compatible API**: existing
  ``RiskConstraintEngine.apply()`` stub stays in place
  unchanged — its fail-closed contract is preserved so any
  code reaching it today (none in this repo) keeps working
  exactly as before.
