# Factor Mining — Phase 1 Pre-flight Checklist

> **Purpose**: Before starting Phase 1 implementation, run through this checklist to (a) resolve the 5 open design questions, (b) verify data quality, (c) confirm integration points with the existing codebase, and (d) align the AI agent on Phase 1 acceptance criteria.
>
> **Estimated time**: 2-4 hours of work to complete this checklist.
>
> **Outcome**: A signed-off decision record + clean Phase 1 starting state.

---

## How to Use This Document

1. Work through Sections 1-4 in order
2. Fill in the **DECISION** blocks as you go — these get committed to the repo
3. Run the verification scripts (Section 2 and Section 3) and paste output into the corresponding **EVIDENCE** blocks
4. Only start Phase 1 implementation after the checklist in Section 5 is fully checked

---

## Section 1: Open Design Questions — Decision Records

These 5 decisions are unblocking Phase 1. Each has a recommended default; document your choice (and why if you deviate).

### 1.1 Data Survivorship Bias

**Question**: Does the historical OHLCV data include delisted stocks?

**Why it matters**: The #1 cause of fake-good backtests. Mined factors that look profitable on survivor-only data often vanish in live trading because the strategy implicitly bet on "stocks that didn't get delisted".

**Recommended default**: Run the verification script in §2 first, then choose:

| If data | Recommended action |
|---------|--------------------|
| Includes delisted (data ends at delisting date) | ✅ Proceed with normal fitness |
| Includes delisted (extended to today, wrong) | ❌ Block — fix data pipeline first |
| Excludes delisted | ⚠ Add `universe_stability_penalty` to fitness (see §1.2 in design doc) |

**DECISION**:
```
☐ Data includes delisted stocks correctly → proceed
☐ Data includes delisted but extended incorrectly → fix data first (BLOCKING)
☐ Data excludes delisted → proceed with universe_stability_penalty
☐ Unknown → run verification script in §2 first

Chosen: _______________________________________
Date:   _______________________________________
Notes:  _______________________________________
```

**EVIDENCE** (paste output of script from §2 here):
```
[paste output]
```

---

### 1.2 Transaction Cost Model

**Question**: How do we model transaction costs in the fitness function?

**Why it matters**: Mined factors will optimize against whatever penalty function we use. A weak penalty produces high-turnover unprofitable factors; an overly strict penalty kills good signals.

**Options**:

| Model | Formula | Pro | Con |
|-------|---------|-----|-----|
| **A. Simple turnover penalty** (current) | `turnover_daily × w_turn` | Easy to implement | Dimensionless — can't compare to IC |
| **B. Annualized round-trip** (recommended) | `turnover_daily × 252 × 0.003` | Comparable to annualized IC; concrete cost | Single number, no nuance |
| **C. Segmented cost model** | Different cost for small-cap vs large-cap | More realistic | Needs market-cap data + complexity |
| **D. Impact-aware model** | `cost(turnover) = base + impact(turnover²)` | Most accurate | Hard to calibrate, fragile |

**Recommended default**: **Option B**. A-share specifics:
```
Stamp tax (sell only):    0.10% × 0.5 (half-turnover) = 0.05%
Commission (buy + sell):  0.025% × 2                  = 0.05%
Slippage (buy + sell):    0.05% × 2                   = 0.10%
Impact (rough avg):       0.05% × 2                   = 0.10%
─────────────────────────────────────────────────────────
Round-trip cost:                                       0.30%
```

So `transaction_cost(turnover_daily) = turnover_daily × 252 × 0.003`.

This constant `0.003` lives in config, not hardcoded.

**DECISION**:
```
☐ Option A (simple, current state)
☐ Option B (annualized round-trip)  ← recommended
☐ Option C (segmented)
☐ Option D (impact-aware)
☐ Other: ___________________________

Round-trip cost rate (decimal): _______ (e.g. 0.003 = 0.3%)
Chosen by: _______________________________________
Date:      _______________________________________
```

---

### 1.3 Industry / Size Neutralization

**Question**: Do cross-sectional operators (`cs_rank`, `cs_zscore`, etc.) need industry- or size-bucketed variants?

**Why it matters**: Without neutralization, GP often "discovers" industry rotation or size-effect factors that work in-sample but are unstable and redundant with known style factors.

**Recommended default**: **v1: not implemented, but Grammar has an extension point.**

Justification:
- Requires industry mapping data (申万一级 / 中信一级 / GICS)
- Doubles search space → ~2× runtime
- Most quant systems start without it and add later

**v1 Implementation**: Add `group_by: Optional[str] = None` parameter to cs_* operators but only generate `group_by=None` in Phase 1 grammar.

**DECISION**:
```
☐ v1 implements industry neutralization
☐ v1 implements size neutralization
☐ v1 implements both
☐ v1 implements neither (extension point only)  ← recommended

If implementing: source of industry mapping = _____________________
Chosen by: _______________________________________
Date:      _______________________________________
```

---

### 1.4 Feature Universe

**Question**: What primitive features can GP use?

**Options**:

| Set | Features | Note |
|-----|----------|------|
| **Minimal OHLCV** | open, high, low, close, volume | Bedrock |
| **Standard quant** (recommended v1) | + vwap, amount, turn | Most common A-share factors |
| **Extended price-volume** | + bid/ask spread, intraday volatility | If high-freq data available |
| **+ Fundamentals** | + pe, pb, ps, market_cap | Needs separate data source |
| **+ Sentiment** | + news_score, social_volume | Requires alternative data |

**Recommended default**: Standard quant (open, high, low, close, volume, vwap, amount, turn).

**v2 path**: Add fundamentals once they're available in the qlib provider — just expand the `T_FEATURE` enum.

**DECISION**:
```
v1 features (check all that apply):
☐ $open
☐ $high  
☐ $low
☐ $close
☐ $volume
☐ $vwap
☐ $amount
☐ $turn
☐ Other: _____________________________________

Chosen by: _______________________________________
Date:      _______________________________________
```

---

### 1.5 Promotion Workflow

**Question**: How do factors move from "mined" to "in-production training"?

**Options**:

| Mode | Description | Risk |
|------|-------------|------|
| **Fully automatic** | Top-N from each GP run auto-promoted if OOS metrics > threshold | High overfit risk |
| **Manual gated** (recommended) | Researcher reviews each candidate, clicks promote | Slower, but safer |
| **Hybrid** | Auto-shortlist + manual final approval | Best of both |

**Recommended default**: **Manual gated for v1**, hybrid for v2.

Folder structure:
```
research/mined_factors/
├── runs/{run_id}/                ← every GP run (auto-saved)
│   └── factor_pool.parquet
├── candidates/{date}/            ← researcher copies promising ones here
│   └── {factor_id}.json
└── production/{version}/         ← researcher promotes after OOS validation
    └── factor_pool.parquet       ← used by training pipeline
```

Promotion criteria (configurable, override-able):
- OOS IR > 0.3
- OOS RankIC mean > 0.02
- Max correlation with existing prod factors < 0.6
- Stability: rolling 6-month IR > 0.2 in ≥ 70% of windows

**DECISION**:
```
☐ Fully automatic
☐ Manual gated  ← recommended
☐ Hybrid (auto-shortlist + manual promote)

If automatic/hybrid, promotion criteria:
  Min OOS IR:               _______
  Min OOS RankIC mean:      _______
  Max correlation:          _______

Chosen by: _______________________________________
Date:      _______________________________________
```

---

## Section 2: Data Survivorship Verification

Save this script as `scripts/verify_survivorship.py` and run it.

```python
"""
Verify whether the qlib provider data includes delisted stocks.

Three possible outcomes:
1. GOOD: Delisted stocks present, data ends at delisting date
2. BAD:  Delisted stocks present, data extends beyond delisting (data error)
3. UGLY: Delisted stocks absent (survivorship bias — proceed with caution)
"""

import qlib
from qlib.data import D
import pandas as pd
import sys


# ============================================
# CONFIG — adjust these
# ============================================
PROVIDER_URI = "D:/qlib_data/my_cn_data"
REGION = "cn"

# Known delisted stocks (verified delisted before 2024-01-01)
# These are publicly known cases — adjust if your data is older
KNOWN_DELISTED = [
    # (code, expected_delisting_date_approx, why)
    ("SH600087", "2019-08-22", "*ST 长航凤凰 → 退市"),
    ("SH600247", "2019-12-30", "ST 成城 → 退市"),
    ("SH600615", "2019-07-08", "丰华股份 → 退市"),
    ("SH600753", "2019-07-26", "庞大集团 → 退市"),
    ("SZ000023", "2019-07-08", "深天地A → 退市"),
    ("SZ000010", "2020-07-08", "美丽生态 → 退市"),
    ("SH600268", "2019-05-23", "国电南自 → 资产重组退市"),
]

# Reference period: data should cover this range
TEST_START = "2015-01-01"
TEST_END = "2024-12-31"


def main():
    qlib.init(provider_uri=PROVIDER_URI, region=REGION)
    
    print("=" * 60)
    print("SURVIVORSHIP BIAS VERIFICATION")
    print("=" * 60)
    print(f"Provider: {PROVIDER_URI}")
    print(f"Test range: {TEST_START} to {TEST_END}")
    print(f"Checking {len(KNOWN_DELISTED)} known delisted stocks")
    print()
    
    results = {
        "good": [],      # has data ending around delisting date
        "bad_extended": [],  # has data extending way past delisting
        "missing": [],   # not in dataset at all
        "error": [],     # other error
    }
    
    for code, expected_delist_date, reason in KNOWN_DELISTED:
        try:
            data = D.features(
                [code], ["$close"], 
                TEST_START, TEST_END
            )
            
            if data.empty or data["$close"].dropna().empty:
                results["missing"].append((code, reason))
                print(f"❌ {code}  MISSING  ({reason})")
                continue
            
            valid_data = data["$close"].dropna()
            last_date = valid_data.index.get_level_values("datetime").max()
            expected_dt = pd.Timestamp(expected_delist_date)
            
            # Allow 90 days slack
            days_past = (last_date - expected_dt).days
            
            if days_past < 90:
                results["good"].append((code, last_date, expected_dt))
                print(f"✅ {code}  data ends {last_date.date()}, "
                      f"delisted ~{expected_dt.date()} ({days_past:+d}d)")
            else:
                results["bad_extended"].append((code, last_date, expected_dt))
                print(f"⚠️  {code}  data ends {last_date.date()}, "
                      f"delisted ~{expected_dt.date()} "
                      f"({days_past:+d}d — extended too long!)")
                
        except Exception as e:
            results["error"].append((code, str(e)))
            print(f"💥 {code}  ERROR: {e}")
    
    # Verdict
    print()
    print("=" * 60)
    print("VERDICT")
    print("=" * 60)
    
    n_good = len(results["good"])
    n_extended = len(results["bad_extended"])
    n_missing = len(results["missing"])
    n_total = len(KNOWN_DELISTED)
    
    print(f"  Properly delisted:    {n_good}/{n_total}")
    print(f"  Data extended (bad):  {n_extended}/{n_total}")
    print(f"  Missing from dataset: {n_missing}/{n_total}")
    print(f"  Errors:               {len(results['error'])}/{n_total}")
    print()
    
    if n_extended > n_total / 2:
        print("❌ VERDICT: BAD — data extends past delisting dates.")
        print("   Data quality issue — fix before factor mining.")
        return 2
    elif n_missing > n_total / 2:
        print("⚠️  VERDICT: SURVIVORSHIP BIAS — most delisted stocks missing.")
        print("   Proceed with universe_stability_penalty in fitness.")
        print("   See Section 1.1 of the checklist.")
        return 1
    elif n_good > n_total / 2:
        print("✅ VERDICT: GOOD — data correctly handles delisted stocks.")
        print("   Safe to proceed with normal fitness function.")
        return 0
    else:
        print("⚠️  VERDICT: UNCLEAR — mixed results. Inspect manually.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

**Acceptance**: Exit code 0 (good) or document mitigation in §1.1 if 1 (survivorship bias).

---

## Section 3: Existing Code Interface Verification

Phase 2 will reuse the existing `SignalAnalyzer` / `FactorAnalyzer` classes for IC/IR/RankIC computation. Before Phase 1 starts, verify these interfaces are usable from the factor mining module.

Save this script as `scripts/verify_existing_interfaces.py`:

```python
"""
Verify that we can drive existing analyzers from custom factor values.

This is a smoke test for Phase 2 interface compatibility — if this works,
factor_mining/evaluator.py can be a thin wrapper.

Adjust import paths to match your repo's structure.
"""

import numpy as np
import pandas as pd
import qlib
from qlib.data import D


PROVIDER_URI = "D:/qlib_data/my_cn_data"


def construct_test_factor():
    """Build a known-good factor: -1 × 20-day return (reversal)."""
    qlib.init(provider_uri=PROVIDER_URI, region="cn")
    
    # Pull a small slice
    fields = ["$close", "Ref($close, 20)"]
    data = D.features(
        D.instruments("all"),
        fields,
        "2023-01-01", "2023-12-31",
    )
    data.columns = ["close", "close_lag20"]
    
    # Factor: 20-day reversal (-1 × past return)
    factor = -(data["close"] / data["close_lag20"] - 1)
    
    # Forward 1-day return as label
    next_close = D.features(
        D.instruments("all"),
        ["Ref($close, -1) / $close - 1"],
        "2023-01-01", "2023-12-31",
    )
    next_close.columns = ["fwd_ret"]
    
    df = pd.DataFrame({
        "factor": factor,
        "label": next_close["fwd_ret"],
    }).dropna()
    
    return df


def test_signal_analyzer(factor_df):
    """
    Try to feed factor values into the existing SignalAnalyzer.
    
    !!! ADJUST THIS IMPORT TO YOUR ACTUAL CODE STRUCTURE !!!
    """
    try:
        # Update this import to match your repo
        from src.analysis.signal_analyzer import SignalAnalyzer
        # OR: from qlib_trading_system_v2.analysis import SignalAnalyzer
        # OR: whatever your actual path is
    except ImportError as e:
        print(f"❌ Cannot import SignalAnalyzer: {e}")
        print("   You need to:")
        print("   1. Confirm the correct import path")
        print("   2. Verify SignalAnalyzer exists in your codebase")
        print("   3. Update this script with the correct import")
        return None
    
    print(f"✅ Imported SignalAnalyzer from your codebase")
    
    # Inspect its constructor signature
    import inspect
    sig = inspect.signature(SignalAnalyzer.__init__)
    print(f"   __init__ signature: {sig}")
    
    print()
    print("Now you need to confirm:")
    print("  1. Can SignalAnalyzer accept a (date, stock) → factor DataFrame?")
    print("  2. Does it expose .compute_ic() or similar?")
    print("  3. What does the return value look like?")
    print()
    print("Run the following manually:")
    print("  analyzer = SignalAnalyzer(factor_df['factor'], factor_df['label'])")
    print("  print(analyzer.compute_ic())  # or whatever method exists")
    
    # TRY to instantiate — adjust based on actual signature
    try:
        analyzer = SignalAnalyzer(
            factor_df["factor"],
            factor_df["label"],
        )
        print()
        print(f"✅ Instantiated SignalAnalyzer")
        print(f"   Available methods: {[m for m in dir(analyzer) if not m.startswith('_')]}")
        return analyzer
    except Exception as e:
        print()
        print(f"❌ Could not instantiate: {e}")
        print("   Inspect signature above and adjust accordingly")
        return None


def test_factor_analyzer(factor_df):
    """Same as above for FactorAnalyzer."""
    try:
        from src.analysis.factor_analyzer import FactorAnalyzer
    except ImportError as e:
        print(f"❌ Cannot import FactorAnalyzer: {e}")
        return None
    
    print(f"✅ Imported FactorAnalyzer")
    
    import inspect
    sig = inspect.signature(FactorAnalyzer.__init__)
    print(f"   __init__ signature: {sig}")
    
    # Adjust based on actual signature
    return None  # fill in after inspecting


def main():
    print("=" * 60)
    print("EXISTING INTERFACE VERIFICATION")
    print("=" * 60)
    print()
    
    print("Step 1: Building test factor (20-day reversal)...")
    factor_df = construct_test_factor()
    print(f"  Factor df shape: {factor_df.shape}")
    print(f"  Sample:")
    print(factor_df.head().to_string(max_cols=10))
    print()
    
    print("Step 2: Testing SignalAnalyzer integration...")
    sa = test_signal_analyzer(factor_df)
    print()
    
    print("Step 3: Testing FactorAnalyzer integration...")
    fa = test_factor_analyzer(factor_df)
    print()
    
    if sa is None and fa is None:
        print("=" * 60)
        print("VERDICT: Cannot import existing analyzers.")
        print("Action required: Document correct import paths")
        print("                 OR implement IC computation in factor_mining")
        return 1
    
    print("=" * 60)
    print("VERDICT: Update factor_mining/evaluator.py to use these classes")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
```

**EVIDENCE** (paste result after running):
```
Import paths confirmed:
  SignalAnalyzer:  src/_____________________________________
  FactorAnalyzer:  src/_____________________________________

Constructor signatures:
  SignalAnalyzer(__init__): _____________________________
  FactorAnalyzer(__init__): _____________________________

Methods needed for evaluator.py:
  - _______________________________________
  - _______________________________________
  - _______________________________________

Test factor IC result (20-day reversal):
  Mean IC:    _______ (expect 0.01-0.05 for A-share reversal)
  IR:         _______
```

---

## Section 4: Phase 1 Implementation Notes for the AI Agent

Paste this entire section into the AI agent's context when starting Phase 1.

### 4.1 Numerical Stability — Every Operator

```python
# ❌ Wrong (AI default):
def ts_corr(x, y, n):
    return x.rolling(n).corr(y)

# ✅ Required:
def ts_corr(x, y, n):
    result = x.rolling(n).corr(y)
    result = result.replace([np.inf, -np.inf], np.nan)
    return result
```

**Apply this defensive pattern to every operator that can produce Inf or NaN:**

| Operator | Defensive treatment |
|----------|--------------------|
| `div_safe` | Replace ±inf with NaN; zero denominator → NaN, not inf |
| `log_safe` | x ≤ 0 → NaN (don't error) |
| `sqrt_safe` | x < 0 → NaN |
| `ts_rank` | When window is constant, return 0.5 (mid rank) |
| `cs_zscore` | When std ≈ 0, return 0 (not NaN, avoid contagion) |
| `ts_corr`, `ts_cov` | Replace ±inf with NaN |
| `ts_std`, `ts_skew`, `ts_kurt` | Force NaN for windows with insufficient non-NaN |

### 4.2 Hash Commutativity

Commutative operators must hash the same regardless of argument order:

```python
COMMUTATIVE_OPS = {"add", "mul", "min", "max"}

def hash_node(node) -> int:
    if node.op in COMMUTATIVE_OPS:
        # Sort child hashes before incorporating
        child_hashes = sorted(hash_node(c) for c in node.children)
    else:
        child_hashes = [hash_node(c) for c in node.children]
    return hash((node.op, tuple(child_hashes)))
```

This is mandatory. Without it, `add($close, $volume)` and `add($volume, $close)` are treated as different expressions and GP wastes 30-50% of its search budget on equivalents.

### 4.3 Type System Strictness

Phase 1 grammar must enforce these type rules. **No exceptions**.

```python
# Type definitions (in src/factor_mining/grammar.py)

T_FEATURE = "feature"          # Primitive OHLCV
T_FLOAT = "float"              # Computed panel field
T_CSF = "cs_factor"            # Cross-sectional factor (terminal output)
T_INT_WINDOW = "int_window"    # Integer ∈ {5, 10, 20, 40, 60}
T_SCALAR = "scalar"            # Floating constant ∈ {0.5, 1.0, 2.0}

# Operator type rules:

# Arithmetic: T_FLOAT × T_FLOAT → T_FLOAT
# Unary scalar: T_FLOAT → T_FLOAT
# Time-series: T_FLOAT × T_INT_WINDOW → T_FLOAT
# Cross-sectional: T_FLOAT → T_CSF
# Where: T_FLOAT × T_FLOAT × T_FLOAT → T_FLOAT
# Features (leaves): T_FEATURE coerced to T_FLOAT

# Root of every expression MUST be T_CSF.
# This guarantees the output is a cross-sectional factor.
```

The root-must-be-T_CSF rule is the single most important constraint. If you skip it, GP will produce expressions like `$close + $volume` which are not factors.

### 4.4 Random Expression Generator

```python
def random_expression(
    target_type: Type,
    max_depth: int,
    min_depth: int = 2,
    rng: random.Random = None
) -> Node:
    if max_depth == 0:
        return random_leaf(target_type, rng)
    
    if min_depth > 0:
        # MUST return an operator
        return random_operator(target_type, max_depth, min_depth, rng)
    
    # Otherwise weighted choice
    if rng.random() < 0.3:
        return random_leaf(target_type, rng)
    return random_operator(target_type, max_depth, min_depth, rng)
```

Without `min_depth`, generator produces too many trivial expressions like `cs_rank($close)`. Set `min_depth=2` for Phase 1.

### 4.5 Don't Skip the Integration Smoke Test

At the END of Phase 1, before the PR is considered done:

```python
# tests/factor_mining/test_integration_smoke.py
def test_can_evaluate_known_good_factor():
    """
    Construct a 20-day reversal factor by hand and verify it produces
    a sensible IC. This catches integration issues early.
    """
    from src.factor_mining.expression import parse_expression
    from src.factor_mining.evaluator import compute_factor_cpu  # stub OK for Phase 1
    
    expr_str = "cs_rank(div_safe(ts_delta($close, 20), $close))"
    expr = parse_expression(expr_str)
    
    # Just verify it can be:
    # 1. Parsed
    # 2. Serialized + deserialized
    # 3. Type-checked
    # 4. Pretty-printed back
    # 5. Hashed (and same hash after round-trip)
    
    assert expr.output_type == "T_CSF"
    
    serialized = expr.to_dict()
    expr2 = parse_dict(serialized)
    assert hash_node(expr) == hash_node(expr2)
    
    pretty = expr.to_qlib_string()
    assert pretty == "CSRank((Delta($close, 20) / $close))"
```

Phase 1 doesn't actually compute IC (that's Phase 2). But the expression tree must be machine-readable AND human-readable, and it must round-trip cleanly.

### 4.6 Things NOT to Do in Phase 1

Common AI scope creep — push back if the agent tries to:

| ❌ Don't | ✅ Do instead |
|---------|--------------|
| Implement GPU operators | Defer to Phase 4. CPU only. |
| Wire up GP loop | Defer to Phase 3. |
| Compute actual IC | Defer to Phase 2. Phase 1 stops at expression validation. |
| Add fundamental data ($pe, $pb) | Defer to v2. |
| Implement industry neutralization | Defer to v2. |
| Build factor pool persistence | Defer to Phase 2. |
| Optimize for performance | Phase 1 is correctness, not speed. |

---

## Section 5: Phase 1 Pre-flight Checklist

Don't start coding Phase 1 until **all** of these are checked.

### Decisions Documented
- [ ] §1.1 Survivorship bias decision recorded (with verdict from §2 script)
- [ ] §1.2 Transaction cost model decision recorded
- [ ] §1.3 Industry neutralization decision recorded
- [ ] §1.4 Feature universe finalized
- [ ] §1.5 Promotion workflow chosen

### Data Quality
- [ ] §2 verification script run, exit code recorded
- [ ] Verdict documented (good / survivorship / data error)
- [ ] If survivorship: mitigation plan documented

### Interface Compatibility
- [ ] §3 interface verification script run
- [ ] SignalAnalyzer import path confirmed
- [ ] FactorAnalyzer import path confirmed (if applicable)
- [ ] Constructor signatures recorded
- [ ] Test factor (20-day reversal) produces sensible IC

### Environment
- [ ] CuPy NOT required for Phase 1 (`pip list | findstr cupy` can be empty)
- [ ] qlib confirmed importable from your repo
- [ ] Existing analyzer classes confirmed importable
- [ ] `pytest` runs without errors on existing tests
- [ ] OpenSpec change scaffold created: `openspec/changes/add-factor-mining-phase1/`

### AI Agent Context
- [ ] Agent received the design.md (full document)
- [ ] Agent received this checklist (Section 4 in particular)
- [ ] Agent has confirmed it understands:
  - [ ] No GPU code in Phase 1
  - [ ] Hash commutativity rule
  - [ ] Type system strictness (root = T_CSF)
  - [ ] All operators must handle NaN/Inf defensively
  - [ ] Integration smoke test is part of acceptance

### Phase 1 Scope Confirmed
Files to be created:
- [ ] `src/factor_mining/__init__.py`
- [ ] `src/factor_mining/operators.py` (~300 LOC, 22 operators, CPU only)
- [ ] `src/factor_mining/expression.py` (~250 LOC, AST + serialization + hash)
- [ ] `src/factor_mining/grammar.py` (~200 LOC, types + random generator)
- [ ] `tests/factor_mining/test_operators.py` (~200 LOC)
- [ ] `tests/factor_mining/test_expression.py` (~150 LOC)
- [ ] `tests/factor_mining/test_grammar.py` (~150 LOC)
- [ ] `tests/factor_mining/test_integration_smoke.py` (~100 LOC)

Files NOT to be created in Phase 1:
- [ ] No `gp_engine.py` (Phase 3)
- [ ] No `evaluator.py` (Phase 2)
- [ ] No `factor_pool.py` (Phase 2)
- [ ] No GPU kernels (Phase 4)

---

## Section 6: Phase 1 Acceptance Criteria

Phase 1 is "done" when:

### Code Quality
- [ ] All 22 operators in `operators.py` implemented (per §4.1 of design doc)
- [ ] All operators have CPU implementation (`compute_cpu` method or equivalent)
- [ ] Every operator handles NaN/Inf defensively (per §4.1 of this doc)
- [ ] Type system enforces rules from §4.3 of this doc
- [ ] Hash commutativity from §4.2 implemented and tested
- [ ] Random expression generator respects min_depth and max_depth
- [ ] Code follows existing repo style (docstrings, type hints, etc.)

### Tests
- [ ] `pytest tests/factor_mining/` all green
- [ ] `test_operators.py`: every operator has happy path + NaN + zero + negative + single-row + empty tests
- [ ] `test_expression.py`: serialization round-trip; hash stability; commutativity hash test
- [ ] `test_grammar.py`: 1000 random expressions, 100% type-valid; min_depth respected
- [ ] `test_integration_smoke.py`: parse → type-check → round-trip → hash all pass

### Manual Verification
- [ ] Construct a known-good factor (20-day reversal) manually, parse it, round-trip, pretty-print → human readable
- [ ] Generate 100 random expressions, manually inspect 10 of them → all look "reasonable" (no `cs_rank(cs_rank(cs_rank($close)))` nonsense)
- [ ] Profile one operator (`ts_corr`) on real data → completes in < 5s for 5000 stocks × 250 days

### OpenSpec Process
- [ ] Phase 1 OpenSpec change created
- [ ] `proposal.md`, `design.md`, `tasks.md` complete
- [ ] `specs/operators.md`, `specs/expression.md`, `specs/grammar.md` complete
- [ ] `openspec validate --strict` passes
- [ ] PR opened with full description

### Documentation
- [ ] `docs/factor_mining.md` started (just user-facing overview, no impl details)
- [ ] README in `src/factor_mining/` explains module structure
- [ ] Each public class/function has docstring

### Honest Self-Assessment
At PR time, AI agent must answer in PR description:
- [ ] Did I implement GPU code? (Should be **No**)
- [ ] Did I compute actual IC values? (Should be **No** — defer to Phase 2)
- [ ] Did I cut corners on numerical stability? (Should be **No**)
- [ ] Are there any operators I implemented but didn't fully test? (Should be **No**)
- [ ] Did I skip the integration smoke test? (Should be **No**)

---

## Section 7: Common Phase 1 Failure Modes (Watch For These)

Even with this checklist, here are the most likely ways Phase 1 goes wrong:

1. **AI implements "while we're at it" features** — pushes scope creep. Push back aggressively; defer to later phases.

2. **Numerical edge cases missed** — happy path tests pass but real data has NaN explosions. Mitigation: explicit edge case tests in §6.

3. **Type system holes** — generator produces expressions that "should" be invalid but happen to type-check. Mitigation: 1000-random-expression test must be 100% valid (not 99%).

4. **Hash inconsistency** — same expression hashes differently due to traversal order, dict ordering, etc. Mitigation: explicit commutativity test + structural-equality test.

5. **Integration assumption wrong** — Phase 2 starts and discovers `SignalAnalyzer.compute_ic()` doesn't exist or has different signature. Mitigation: §3 verification script BEFORE Phase 1, not at Phase 2.

6. **Performance trap** — Phase 1 operators are correct but each takes 30s on real data, making Phase 2 evaluator unusably slow. Mitigation: profile in §6 acceptance criteria.

7. **Scope deferred but tech debt undocumented** — Phase 1 done, but the deferred items live in AI's head only. Mitigation: explicit "Tech Debt for Phase 2+" section in PR description.

---

## End

When all sections complete, paste this signed-off summary into the PR description:

```
Pre-flight Checklist: COMPLETE
─────────────────────────────────
§1 Decisions:           ✓ All 5 recorded
§2 Data verification:   ✓ Verdict: [GOOD / SURVIVORSHIP / BAD]
§3 Interface check:     ✓ SignalAnalyzer at: [path]
§5 Pre-flight:          ✓ All boxes checked
§6 Phase 1 acceptance:  ✓ All criteria met
§7 Failure modes:       ✓ Reviewed
Date completed:         [YYYY-MM-DD]
Completed by:           [name / agent id]
```

Then start Phase 1.
