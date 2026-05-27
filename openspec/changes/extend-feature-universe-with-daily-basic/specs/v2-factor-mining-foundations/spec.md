## MODIFIED Requirements

### Requirement: Feature universe SHALL be exactly the twelve PIT bin fields per D3 (extended)

The terminal feature registry SHALL expose exactly twelve features partitioned into two groups:

**Group A — six OHLCV PIT bin fields (existing per D3):**
- `$open`, `$high`, `$low`, `$close` — daily OHLC closing prices, **`taint = ADJ_TAINTED`** (qlib adjusts via `adj_factor`).
- `$volume`, `$money` — daily traded volume and yuan amount, **`taint = PURE`**.

**Group B — six daily_basic fundamental / microstructure fields (new):**
- `$pe`, `$pb`, `$ps` — value ratios (price/earnings, price/book, price/sales). **`taint = PURE`**. Ratios of two same-ticker quantities cancel the `adj_factor` ladder identically (per `scale_invariance.md` §4 same-ticker-ratio rule).
- `$turnover_rate` — daily volume / float_share. **`taint = PURE`**. Already a ratio, scale-free.
- `$circ_mv`, `$total_mv` — circulating and total market capitalisation in yuan. **`taint = PURE`**. Tushare publishes the cap by recomputing `shares × current_price` each day, NOT by scaling a static reference through the adjustment ladder. (Operators applying this proposal MUST verify this against a sample of historical split events; if the cap ladders, downgrade to `ADJ_TAINTED` and update this requirement.)

The following terminals SHALL NOT be added in this iteration:
- `$vwap` — expressible as `div_safe($money, $volume)` (existing decision D3).
- `$turn` (Tushare turnover absolute) — `$turnover_rate` is the per-cent normalisation we want.
- `$amount` — duplicates `$money` (kept under the PIT bin name `money`).
- `$pe_ttm`, `$ps_ttm`, `$float_share`, `$total_share` — held back for a future iteration; the six chosen above are the highest-impact categories per the v1 empirical follow-up.
- `$pe`, `$pb`, `$ps` use the same-tier (non-TTM) Tushare daily_basic columns by default.

#### Scenario: a developer enumerates the feature registry
- **WHEN** a developer iterates `FeatureRegistry.V1`
- **THEN** exactly the set `{"$open", "$high", "$low", "$close", "$volume", "$money", "$pe", "$pb", "$ps", "$turnover_rate", "$circ_mv", "$total_mv"}` is returned
- **AND** `$vwap`, `$turn`, `$amount`, `$pe_ttm`, `$float_share`, `$total_share` are absent

#### Scenario: a developer queries the taint of each terminal
- **WHEN** the taint of each terminal in `FeatureRegistry.V1` is read
- **THEN** `$open`, `$high`, `$low`, `$close` return `ADJ_TAINTED`
- **AND** `$volume`, `$money`, `$pe`, `$pb`, `$ps`, `$turnover_rate`, `$circ_mv`, `$total_mv` return `PURE`

#### Scenario: `cs_rank` directly accepts the new PURE terminals
- **WHEN** a caller constructs `cs_rank($pe)`, `cs_rank($pb)`, `cs_rank($turnover_rate)`, or `cs_rank($circ_mv)`
- **THEN** construction succeeds with `output_type == ExprType("CSF", "PURE")`
- **AND** no `GrammarError` is raised

#### Scenario: a same-ticker ratio of cap and adjusted close cancels taint
- **WHEN** a caller constructs `div_safe($total_mv, $close)`
- **THEN** the result's `output_type` is `ExprType("FLOAT", "PURE")` (cap is PURE; adjusted close is ADJ_TAINTED; the ratio is PURE per the same-ticker-ratio cancellation rule of `scale_invariance.md` §4)
- **AND** wrapping it in `cs_rank(...)` passes the cs_* gate

#### Scenario: mixing PURE fundamentals with ADJ_TAINTED price is still rejected
- **WHEN** a caller constructs `cs_rank(add($pe, $close))` (one PURE input, one ADJ_TAINTED input to `add`)
- **THEN** `GrammarError` is raised at construction time
- **AND** the message names the taint mismatch on `add` (the inner failure surfaces before the `cs_rank` gate)
