# Factor Mining — Scale-Invariance Type Rules

> **Normative**. Phase 1's grammar MUST implement the type system
> below. This is a hard correctness requirement, not a stylistic
> preference — the alternative is mined factors whose IC is inflated
> by a per-ticker constant the operator never authored.
>
> **Status**: Decided 2026-05-22 as part of Phase 0 outcome O2
> (`decisions.md` §"Phase 0 outcomes").

---

## 1. Why this exists

`src/data/pit/qlib_bin_builder.py:39-44` documents the contract that
forces this entire document:

> The bin stores PRE-ADJUSTED prices (close × adj_factor and same for
> open/high/low). adj_factor is Tushare's as-of-today snapshot per
> §4.3.1, so **absolute adjusted prices are NOT PIT-correct
> features**. Downstream consumers MUST use within-ticker ratios /
> returns only.

For any expression that the GP synthesises and the fitness
evaluator scores as a cross-sectional factor, the value at
`(ticker, date)` MUST NOT depend on `adj_factor[ticker]` — because
`adj_factor[ticker]` is a per-ticker constant whose value is fixed
**at the time the bin was last rebuilt**, not at the evaluation
date `t`. A factor whose cross-sectional ranking depends on that
constant is ranking tickers by "how much their as-of-today corporate
events stretched their historical bin", not by anything causal.

The dangerous part is that such a factor's **IC on backtest looks
plausible** — the as-of-today adj_factor is correlated with future
outcomes via survivorship-adjacent pathways. So this is exactly the
kind of bug that produces high in-sample IC and silent live failure.
We must rule it out at the grammar level, not catch it at fitness
time.

---

## 2. The math (one paragraph)

For a single (ticker, date) cell, the bin value of a price field is:

```
$close[t, ticker] = real_close[t, ticker] × adj_factor[ticker]
```

`adj_factor[ticker]` is a **per-ticker constant** at the time of bin
build. Within-ticker arithmetic that cancels the constant (ratios,
percent-changes, rank-of-window) produces values free of
`adj_factor`. Cross-sectional reduction (`cs_rank`, `cs_zscore`,
`cs_demean`, `cs_winsorize`) applied to an expression that still
carries `adj_factor[ticker]` produces a ranking that depends on the
constant — and is therefore not PIT-correct.

Volume and money are NOT multiplied by `adj_factor` (Tushare ingests
them as raw absolute quantities), so they enter the type system as
scale-free.

---

## 3. Type system

The Phase 1 grammar defines four output kinds; for each, the type
system tracks **scale taint** as an additional attribute. The
combination of kind + taint is what the operator registry validates
against.

```python
# src/factor_mining/grammar.py  (Phase 1)

from dataclasses import dataclass
from typing import Literal

OutputKind = Literal[
    "FEATURE",     # leaf terminal
    "FLOAT",       # computed scalar/panel value
    "INT_WINDOW",  # window-size literal
    "SCALAR",      # numeric constant (0.5, 1, 2)
    "CSF",         # cross-sectional factor — the REQUIRED root kind
]

ScaleTaint = Literal[
    "PURE",        # value does not depend on adj_factor[ticker]
    "ADJ_TAINTED", # value depends on per-ticker adj_factor constant
]

@dataclass(frozen=True)
class ExprType:
    kind: OutputKind
    taint: ScaleTaint = "PURE"   # default for non-price terms
```

### Terminal types

| Terminal | `kind` | `taint` |
|----------|--------|---------|
| `$open`, `$high`, `$low`, `$close` | `FEATURE` | `ADJ_TAINTED` |
| `$volume`, `$money` | `FEATURE` | `PURE` |
| Integer window literal (5/10/20/40/60) | `INT_WINDOW` | `PURE` |
| Scalar constant (0.5/1/2) | `SCALAR` | `PURE` |

---

## 4. Operator type rules

For each operator the table gives: input types, output kind, and how
the operator propagates `taint`.

### Arithmetic (T_FLOAT × T_FLOAT → T_FLOAT)

| Op | Input taints → Output taint | Notes |
|----|------------------------------|-------|
| `add(a, b)`, `sub(a, b)` | `(PURE, PURE) → PURE`, `(ADJ, ADJ) → ADJ`, `(PURE, ADJ) → ILLEGAL` | Mixing scale-free with adj-tainted is a units error; reject at type-check time. |
| `mul(a, b)` | `(PURE, PURE) → PURE`, `(PURE, ADJ) → ADJ`, `(ADJ, ADJ) → ADJ` (quadratic) | Multiplying two adj-tainted values produces `adj²` pollution; not useful but legal. |
| `div_safe(a, b)` | `(PURE, PURE) → PURE`, `(ADJ, ADJ) → PURE`, `(PURE, ADJ) → ADJ`, `(ADJ, PURE) → ADJ` | Ratio of two adj-tainted with the **same ticker** cancels — this is the *only* way price-derived expressions become scale-free. |

### Unary (T_FLOAT → T_FLOAT)

| Op | Taint propagation | Notes |
|----|-------------------|-------|
| `neg(x)`, `abs(x)` | preserves input | linear in `adj_factor`, sign-preserving |
| `sign(x)` | `PURE` always | sign of `a×x` = sign of `x` when `a > 0` |
| `log_safe(x)` | preserves input | `log(a × x) = log(a) + log(x)` — the `log(a)` term still varies per ticker, contaminating cross-section |
| `sqrt_safe(x)` | preserves input | `sqrt(a × x) = sqrt(a) × sqrt(x)` — same problem |

### Time-series (T_FLOAT × T_INT_WINDOW → T_FLOAT)

Time-series ops are evaluated **per ticker**, so a per-ticker
constant `adj_factor[ticker]` factors through linearly unless the op
involves a self-ratio.

| Op | Taint propagation | Notes |
|----|-------------------|-------|
| `ts_mean(x, n)`, `ts_std(x, n)`, `ts_max(x, n)`, `ts_min(x, n)`, `ts_sum(x, n)` | preserves input | linear ops — `mean(a×x) = a × mean(x)` etc. |
| `ts_delta(x, n)` | preserves input | `ts_delta(a×x, n) = a × ts_delta(x, n)` |
| `ts_pctchange(x, n)` | **always `PURE`** | `x[t] / Ref(x, n) - 1` — same-ticker ratio cancels `a` |
| `ts_rank(x, n)` | **always `PURE`** | ranking within a window is invariant to monotone rescaling (positive `a`) |
| `ts_argmax(x, n)`, `ts_argmin(x, n)` | **always `PURE`** | index-of-extreme is invariant to scaling |
| `ts_corr(x, y, n)` | **always `PURE`** | Pearson correlation invariant to linear rescaling of either input |
| `ts_skew(x, n)`, `ts_kurt(x, n)` | **always `PURE`** | standardized moments — invariant to linear rescaling |
| `ts_decay_linear(x, n)` | preserves input | weighted linear mean |
| `ts_cov(x, y, n)` | **NOT IN v1 GRAMMAR** | `cov(a×x, y) = a × cov(x, y)` — would pollute; redundant with `ts_corr` for v1; **dropped from operator registry** |

### Cross-sectional (T_FLOAT → T_CSF) — the gate

| Op | Input requirement | Output |
|----|-------------------|--------|
| `cs_rank(x)` | `x.taint == PURE` | `(CSF, PURE)` |
| `cs_zscore(x)` | `x.taint == PURE` | `(CSF, PURE)` |
| `cs_demean(x)` | `x.taint == PURE` | `(CSF, PURE)` |
| `cs_winsorize(x)` | `x.taint == PURE` | `(CSF, PURE)` |

**This is the single gate the entire type system exists to enforce.**
Any expression whose root is `cs_*` whose input has `taint ==
ADJ_TAINTED` is rejected at type-check time.

### Conditional

| Op | Inputs / output |
|----|-----------------|
| `where(cond, a, b)` | `cond.taint == PURE`; require `a.taint == b.taint` (same as add/sub); output taint = `a.taint` |

Conditions whose threshold compares against adj-tainted values would
themselves be adj-tainted, so `cond` is restricted to `PURE`.

### Root rule

The output of every Phase 1 expression must satisfy:

```
expr.type.kind  == "CSF"
expr.type.taint == "PURE"
```

(Both are implied by the cs_* gate above, but the grammar SHALL
assert them at the top level too — defence in depth against operators
added later.)

---

## 5. Pass / fail examples

Eight worked examples. The Phase 1 grammar test suite should pin
these directly.

| # | Expression | Verdict | Reason |
|---|------------|---------|--------|
| 1 | `cs_rank(ts_pctchange($close, 20))` | ✅ PASS | `ts_pctchange` is PURE; `cs_rank` accepts |
| 2 | `cs_rank($close)` | ❌ FAIL | `$close` is ADJ_TAINTED; cs_rank requires PURE |
| 3 | `cs_rank(ts_delta($close, 20))` | ❌ FAIL | `ts_delta` preserves taint; result is ADJ_TAINTED |
| 4 | `cs_rank(div_safe(ts_delta($close, 20), $close))` | ✅ PASS | `(ADJ, ADJ) → PURE` via `div_safe`; same-ticker `a` cancels |
| 5 | `cs_zscore($volume)` | ✅ PASS | `$volume` is PURE |
| 6 | `cs_rank(div_safe($money, $volume))` | ✅ PASS | both PURE → PURE; this is the `$vwap` expression |
| 7 | `cs_rank(log_safe($close))` | ❌ FAIL | `log_safe` preserves taint; result is ADJ_TAINTED |
| 8 | `cs_rank(log_safe(div_safe($close, Ref($close, 20))))` | ✅ PASS | inner div produces PURE; log of PURE is PURE |

Additional rejected forms (grammar should also enumerate these so
the test suite asserts the failure mode is correct):

- `cs_rank(add($close, $open))` — both ADJ → ADJ → fails gate
- `cs_rank(mul($close, $volume))` — `(ADJ, PURE) → ADJ` → fails gate
- `cs_rank(ts_mean($close, 20))` — `ts_mean` preserves ADJ → fails

---

## 6. Implementation hooks (Phase 1)

The grammar enforces this by combining three mechanisms:

### 6.1 Type-checked operator registry

Each operator's spec carries its taint propagation rule. The
`Operator` dataclass (Phase 1 §1.1) gains a method:

```python
def output_type(self, *input_types: ExprType) -> ExprType:
    """Compute output ExprType from input ExprTypes; raise on illegal combinations."""
```

A registry-validation test asserts that every operator declares
`output_type` and that the rules above hold (e.g. by feeding
representative input pairs and asserting the returned `taint`).

### 6.2 Random expression generator respects taint

The Phase 1 random generator (Phase 1 §1.3) takes `target_type:
ExprType` not just `OutputKind`. When generating under a `cs_*`
operator, the recursive call carries `taint=PURE` as a constraint,
and the leaf / operator picker filters by that constraint. This
guarantees the 1000-random-expression test produces 100%
type-valid, scale-pure factors.

### 6.3 Type-check pass at expression construction

Any hand-built `Expression` (e.g. test fixtures, the manual 20-day
reversal smoke test in Phase 1 §1.4) is type-checked at
`Expression.__post_init__`. Constructing an illegal expression
raises `GrammarError`. There is no "build now, validate later" mode.

### 6.4 Unit tests pinned to the §5 table

The exact 8 pass/fail expressions from §5 are pinned in
`tests/logic/factor_mining/test_scale_invariance.py`. Adding a new
operator without updating that table is a code smell; the test fixture
is the source of truth that the grammar matches operator authors'
intent.

---

## 7. What this DOES NOT solve

This document handles **multiplicative per-ticker contamination from
the adj_factor snapshot**. Out of scope (deferred to later phases or
v2):

- **Look-ahead bias** more broadly (e.g. using a future-dated label
  in fitness). Handled by the Phase 2 forward-return contract; the
  grammar's role here is only to forbid `Ref($close, -N)` with
  `N > 0` at terminal-registry time.
- **Survivorship bias**. Handled by the PIT post-delist mask; the
  grammar does not need to reason about it.
- **Look-ahead bias from intraday data**. v1 grammar is daily-only.
- **Style-factor leakage** (industry rotation, size effect). D2
  (no neutralization in v1) accepts this risk; the OOS validation
  gate in Phase 6 is the safety net.
- **Volume-normalization regime shifts** (volume in the 2007 bull
  market vs 2024 mean-reversion regime). Not a scaling issue per se;
  factor stability across regimes is a validation concern not a
  grammar concern.
- **Numeric overflow / underflow in deep arithmetic chains**. Handled
  by the `_safe` operator variants (div_safe, log_safe, sqrt_safe)
  per the operator-library design.

---

## 8. References

- `inventory.md` §F.2 — surfacing of the constraint during Phase 0.
- `decisions.md` §D3 + §"Phase 0 outcomes" §O2 — decision record.
- [src/data/pit/qlib_bin_builder.py:39-44](src/data/pit/qlib_bin_builder.py:39) — the bin contract that drives all of this.
- `factor_mining_claude_code_design.md` §4.2 — original type-system sketch (this document extends it with the `taint` dimension).
- `factor_mining_phase1_preflight.md` §4.3 — the original "root = T_CSF" rule (this document strengthens it).
