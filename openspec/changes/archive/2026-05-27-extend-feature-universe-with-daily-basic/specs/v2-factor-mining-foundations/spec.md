## MODIFIED Requirements

### Requirement: Feature universe SHALL be exactly the six PIT bin fields per D3

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

#### Scenario: PURE ÷ PURE fundamental composites stay PURE
- **WHEN** a caller constructs `div_safe($pe, $pb)` (both PURE — value-ratio composite)
- **THEN** the result's `output_type` is `ExprType("FLOAT", "PURE")` per `_rule_div_safe`'s "same-taint → PURE" branch (scale_invariance.md §4)
- **AND** wrapping it in `cs_rank(...)` constructs cleanly as `ExprType("CSF", "PURE")`

#### Scenario: PURE-cap divided by ADJ_TAINTED adjusted close does NOT cancel taint
- **WHEN** a caller constructs `div_safe($total_mv, $close)` (mixed taints: cap is PURE because Tushare publishes it as a daily-recomputed product, NOT through the adjustment ladder; `$close` is ADJ_TAINTED because the qlib bundle stores adjusted closes)
- **THEN** the result's `output_type` is `ExprType("FLOAT", "ADJ_TAINTED")` per `_rule_div_safe`'s "different-taint → ADJ_TAINTED" branch — the ratio inherits `1/adj_factor` because the cap does NOT ride the same adjustment ladder as the close
- **AND** wrapping it in `cs_rank(...)` SHALL raise `GrammarError` (the cs_* gate rejects ADJ_TAINTED input). This pinned example documents an intuitive trap — "both are same-ticker daily quantities, surely adj cancels" is false unless BOTH sides ride the same adjustment ladder

#### Scenario: mixing PURE fundamentals with ADJ_TAINTED price is rejected at the inner additive op
- **WHEN** a caller constructs `cs_rank(add($pe, $close))` (one PURE input, one ADJ_TAINTED input to `add`)
- **THEN** `GrammarError` is raised at construction time
- **AND** the message names the taint mismatch on `add` (the inner failure surfaces before the `cs_rank` gate)
