# Factor Mining Module - Design Proposal

> Target: Add automated factor mining capability to Quant_Ashare V2 system using Genetic Programming.
> Focus: Cross-sectional alpha factors for stock selection, return-optimized, GPU-accelerated.
> Hardware: RTX 4060 Ti 16GB + i7 CPU

---

## 1. Why & What

### 1.1 Problem
Current V2 system uses **hand-crafted factor libraries** (Alpha158 etc.). These factors are:
- Limited in diversity (~158-360 factors, mostly well-known)
- Subject to crowding (anyone using Qlib has them)
- Hard to extend systematically (each new factor needs manual research)

### 1.2 Goal
Build an **Automated Factor Mining (AFM)** subsystem that:
1. Auto-generates cross-sectional alpha factor expressions using Genetic Programming
2. Evaluates them on historical data (IC, RankIC, turnover, decay)
3. Filters for non-redundant, high-quality factors
4. Persists them into a versioned factor pool
5. Plugs the mined factors back into the existing training pipeline

### 1.3 Non-Goals (out of scope, future work)
- RL-based factor mining (AlphaGen) — Phase 2
- Time-series factors for timing — different paradigm
- Fundamental factor mining — requires different data sources

---

## 2. System Integration

### 2.1 Where it fits in V2

```
[Existing Pipeline]                       [New: Factor Mining]
─────────────────────                     ─────────────────────────────
                                          
config.yaml ──► PipelineConfig            config_factor_mining.yaml ──►
                    │                                  │
                    ▼                                  ▼
              data_loader ◄──────────────────► raw_data (qlib)
                    │                                  │
                    ▼                                  ▼
              feature_handler ◄────────────── factor_pool.parquet
                    │                                  ▲
                    ▼                                  │
              model_trainer                    validator_writer
                    │                                  ▲
                    ▼                                  │
              backtester                       gp_engine
                                                       ▲
                                                       │
                                              operators + expr_tree
```

**Key design decision: Decoupled by file contract**, not in-process call.
- Factor mining writes `factor_pool.parquet` (and expressions to `factor_expressions.json`)
- Existing pipeline consumes mined factors via a new `MinedFactorHandler` that reads the pool
- This keeps the mining pipeline runnable independently (long-running, separate cadence)

### 2.2 Module Layout

```
src/
├── core/                       (existing)
├── data/                       (existing)
├── factor_mining/              (NEW)
│   ├── __init__.py
│   ├── operators.py            # Operator library (GPU-aware)
│   ├── expression.py           # Expression tree, AST node, serialization
│   ├── grammar.py              # Grammar rules: typed operators, arity, valid windows
│   ├── gp_engine.py            # GP main loop: population, selection, crossover, mutation
│   ├── evaluator.py            # Factor evaluation: IC, RankIC, turnover, IR
│   ├── fitness.py              # Composite fitness functions
│   ├── factor_pool.py          # Pool management: dedup, correlation, persistence
│   ├── validator.py            # In-sample / out-of-sample validation
│   ├── miner.py                # High-level orchestrator (main entry)
│   └── gpu_compute.py          # GPU compute kernels (CuPy/PyTorch)
├── handlers/                   (NEW or existing)
│   └── mined_factor_handler.py # Qlib-compatible handler reading from pool
└── ...

tests/
└── factor_mining/
    ├── test_operators.py
    ├── test_expression.py
    ├── test_gp_engine.py
    ├── test_evaluator.py
    └── test_integration.py

research/
└── mined_factors/              # Output directory
    ├── runs/
    │   └── {run_id}/
    │       ├── factor_pool.parquet
    │       ├── factor_expressions.json
    │       ├── evolution_log.csv
    │       └── validation_report.html
    └── production/             # Promoted factors after validation
        └── {version}/

config/
└── factor_mining/              (NEW)
    ├── default.yaml
    ├── smoke.yaml
    └── production.yaml
```

### 2.3 OpenSpec Proposal Structure

Following your spec-first workflow, this proposal maps to one OpenSpec change:

```
openspec/changes/add-factor-mining/
├── proposal.md       # This document, condensed
├── design.md         # Detailed design (see Section 3-6 below)
├── tasks.md          # Task breakdown (see Section 7)
└── specs/
    ├── operators.md         # Operator contracts
    ├── expression.md        # Expression validity rules
    ├── fitness.md           # Fitness function contracts
    ├── factor_pool.md       # Pool invariants
    └── handler_contract.md  # Mined factor handler contract
```

---

## 3. Core Concepts

### 3.1 Expression Tree

A factor is an expression tree where:
- **Leaves** are primitive features: `$open`, `$high`, `$low`, `$close`, `$volume`, `$vwap`, `$amount`, `$turn` (whatever exists in the qlib provider)
- **Internal nodes** are operators (binary or unary, see Section 4)
- **Constants** are restricted: integer window sizes from `{5, 10, 20, 40, 60}`, scalar constants from `{0.5, 1, 2}` (limited to prevent overfitting via magic numbers)

Each expression must produce a single `panel<date, stock> -> float` output (a cross-sectional factor value per stock per day).

Example expression tree:
```
                Rank (cs)
                   │
                   ▼
                  ───
                  / \
              Mean  Std
              /│    │\
             20 ▼    ▼ 20
                $vol $close/Ref($close,1)
```
Which evaluates to: `CSRank( Mean($volume, 20) / Std($close / Ref($close, 1), 20) )`

### 3.2 Type System (Critical for Search Efficiency)

Strongly typed expressions cut search space by 10-100x. Types:
- `T_FEATURE` — leaf features
- `T_FLOAT` — scalar-valued nodes (broadcast or panel result)
- `T_INT_WINDOW` — only used as 2nd arg of time-series operators
- `T_CSF` — cross-sectional factor (the desired output type at root)

Every operator declares input/output types. The GP engine only produces type-valid expressions.

### 3.3 Genetic Operations

- **Selection**: Tournament (k=3) with elitism (top 5%)
- **Crossover**: Sub-tree exchange between two parents (probability 0.7)
- **Mutation**: 
  - Sub-tree mutation: replace random subtree (prob 0.15)
  - Point mutation: swap operator with same-arity peer (prob 0.10)
  - Constant mutation: perturb window/scalar (prob 0.05)
- **Population**: 500 individuals × 50 generations (~25k evaluations per run)
- **Diversity preservation**: hash-based dedup each generation, niche sharing on correlation

---

## 4. Operator Library

### 4.1 Primitive Operators

```python
# Arithmetic (binary, T_FLOAT × T_FLOAT → T_FLOAT)
add, sub, mul, div_safe   # div_safe replaces NaN/Inf with 0

# Unary scalar (T_FLOAT → T_FLOAT)
neg, abs, log_safe, sqrt_safe, sign

# Cross-sectional (T_FLOAT → T_CSF)  -- applied per-date
cs_rank      # rank within day, normalized to [-0.5, 0.5]
cs_zscore    # (x - mean_day) / std_day
cs_demean    # x - mean_day
cs_winsorize # clip to [q05, q95] per day

# Time-series (T_FLOAT × T_INT_WINDOW → T_FLOAT) -- applied per-stock
ts_mean(x, n)
ts_std(x, n)
ts_max(x, n)
ts_min(x, n)
ts_rank(x, n)         # rolling rank, normalized
ts_delta(x, n)        # x - ref(x, n)
ts_pctchange(x, n)    # x / ref(x, n) - 1
ts_argmax(x, n)
ts_argmin(x, n)
ts_corr(x, y, n)      # rolling correlation
ts_cov(x, y, n)
ts_skew(x, n)
ts_kurt(x, n)
ts_decay_linear(x, n) # weighted by linear decay
ts_sum(x, n)

# Conditional (T_FLOAT × T_FLOAT × T_FLOAT → T_FLOAT)
where(cond, a, b)     # cond > 0 ? a : b
```

### 4.2 GPU Implementation Strategy

Bottleneck is time-series rolling operations on a `(n_dates, n_stocks)` panel. With ~5000 stocks × ~2500 days ≈ 12.5M cells.

**Two backends, swappable**:
1. **CPU (pandas/numpy)** — reference correctness, used for unit tests
2. **GPU (CuPy + custom kernels)** — production speed

```python
# operators.py interface
class Operator(ABC):
    name: str
    arity: int
    in_types: tuple[Type, ...]
    out_type: Type
    
    @abstractmethod
    def compute_cpu(self, *args, **kwargs) -> pd.DataFrame: ...
    
    @abstractmethod
    def compute_gpu(self, *args, **kwargs) -> cupy.ndarray: ...
```

**GPU optimization tips for RTX 4060 Ti 16GB**:
- Rolling operations: use `cupy.lib.stride_tricks` or custom CUDA kernel
- Batch evaluation: evaluate population in parallel (e.g., 64 expressions per batch)
- Memory: keep raw OHLCV on GPU permanently (~500MB), reuse across evaluations
- Cache intermediate subtree results within one generation (LRU cache, hash by expression)

Expected speedup: **20-50x over pandas** for typical expressions. A 500-individual population × 50 generations should complete in **~30-60 minutes** on this hardware.

### 4.3 Operator Spec Format (for tests)

Each operator must have a spec file:
```yaml
# specs/operators/ts_mean.yaml
name: ts_mean
arity: 2
inputs:
  - name: x
    type: T_FLOAT
  - name: n
    type: T_INT_WINDOW
output: T_FLOAT
properties:
  preserves_nan: true       # input NaN -> output NaN
  monotone_in_window: false # not necessarily
test_cases:
  - input: [[1, 2, 3, 4, 5], n=2]
    expected: [NaN, 1.5, 2.5, 3.5, 4.5]
```

---

## 5. Fitness Function

### 5.1 Multi-objective Composite

For a candidate factor `f(t, s)` and forward return `r(t+1, s)` (or `t+2` for T+1 adjustment):

```
fitness = w_ic * |mean_t( IC(f_t, r_t) )|                         # signal strength
        + w_ir * mean_t(IC) / std_t(IC)                           # signal stability  
        + w_rankic * |mean_t( RankIC(f_t, r_t) )|                 # robust signal
        - w_turn * mean_t( turnover(f_t, f_{t-1}) )               # cost penalty
        - w_corr * max_g( |corr(f, g)| for g in factor_pool )     # novelty
        - w_complexity * size(expression)                          # parsimony
```

Default weights (tunable):
```yaml
w_ic: 1.0
w_ir: 0.5  
w_rankic: 0.5
w_turn: 0.2
w_corr: 0.8       # heavy penalty for redundancy
w_complexity: 0.01
```

### 5.2 Validity Filters (Hard Constraints)

Before computing fitness, expression must pass:
1. **Coverage**: > 80% of (date, stock) cells must be non-NaN
2. **Variance**: cross-sectional std on > 70% of days must be > 1e-6 (not constant)
3. **No data leakage**: no operator uses `$close` at time `t` to predict return at `t` (use `Ref` if needed) — enforced by grammar, not at fitness time
4. **Sanity**: no Inf/extreme outliers in > 5% of cells

Failing any filter: fitness = -inf (won't survive selection).

### 5.3 Return Definition

```python
# Configurable, default uses T+1 buy at open, T+2 sell at open
forward_return = (Ref(open, -2) / Ref(open, -1)) - 1
```

This matches A-share T+1 settlement and avoids look-ahead bias.

---

## 6. Factor Pool Management

### 6.1 Pool Operations

```python
class FactorPool:
    def add(self, expr: Expression, metrics: FactorMetrics) -> bool:
        """Add factor if it passes novelty check. Returns True if added."""
    
    def is_novel(self, expr: Expression, factor_values: pd.DataFrame, 
                 corr_threshold: float = 0.7) -> bool:
        """Check if factor is sufficiently uncorrelated with existing factors."""
    
    def top_k(self, k: int, by: str = "ir") -> list[Expression]: ...
    
    def save(self, path: Path) -> None: ...
    
    @classmethod
    def load(cls, path: Path) -> "FactorPool": ...
    
    def to_qlib_handler_config(self) -> dict:
        """Generate a qlib DataHandler config consuming the pool factors."""
```

### 6.2 Persistence Format

**Two files per run:**

`factor_pool.parquet` — wide table:
```
date | stock | factor_001 | factor_002 | ... | factor_K
```

`factor_expressions.json` — metadata:
```json
{
  "version": "2026-02-15_run_a3f",
  "generation_complete": 50,
  "factors": [
    {
      "id": "factor_001",
      "expression": "CSRank(TS_Mean(div_safe($volume, $close), 20))",
      "expression_tree": { ... },
      "metrics": {
        "ic_mean": 0.0234,
        "ic_std": 0.041,
        "ir": 0.57,
        "rank_ic_mean": 0.0312,
        "turnover_daily": 0.18,
        "coverage": 0.94,
        "max_corr_with_pool": 0.42
      },
      "validation": {
        "in_sample": {...},
        "out_of_sample": {...}
      },
      "lineage": {
        "generation": 38,
        "parents": ["factor_seed_017", "factor_seed_142"]
      }
    }
  ]
}
```

### 6.3 Versioning & Promotion

Three tiers:
1. **`runs/{run_id}/`** — every GP run, retained for reproducibility
2. **`production/{version}/`** — manually promoted factors after OOS validation
3. **Training config** — references `production/{version}/factor_pool.parquet`

Promotion criteria (default, override-able):
- OOS IR > 0.3
- OOS RankIC mean > 0.02
- Max correlation with existing prod factors < 0.6
- Stability: rolling 6-month IR > 0.2 in at least 70% of windows

---

## 7. Implementation Tasks (Phased)

> Each task has acceptance criteria for the AI agent. Tests must pass before moving to next task.

### Phase 1: Foundations (Week 1)

**T1.1: Operator library (CPU only)**
- Implement all operators in Section 4.1 with `compute_cpu`
- Each operator has unit tests covering normal/edge/NaN cases  
- Acceptance: `pytest tests/factor_mining/test_operators.py` passes

**T1.2: Expression tree & serialization**
- AST node classes, tree construction, deep copy, hashing
- JSON serialization round-trip
- Pretty-print to qlib-style expression string
- Acceptance: serialization round-trips identically, hash is stable

**T1.3: Grammar & type checker**
- Type system from Section 3.2
- Random typed expression generator (used for initial population)
- Expression validator (catches type errors)
- Acceptance: 1000 random expressions, 100% type-valid

### Phase 2: Evaluation Core (Week 2)

**T2.1: Single-factor evaluator (CPU)**
- Compute IC, RankIC, IR, turnover for one expression
- Apply coverage/variance/sanity filters from Section 5.2
- Acceptance: given a known good factor (e.g., 20-day momentum), produces IR > 0.3 on test data

**T2.2: Fitness function**
- Implement composite fitness from Section 5.1
- Configurable weights via dataclass
- Acceptance: unit tests with synthetic factors verify each component

**T2.3: Factor pool (basic)**
- `FactorPool` class with add/dedup-by-hash/save/load
- Correlation-based novelty check (CPU)
- Acceptance: pool persists and reloads identically; novelty correctly rejects duplicates

### Phase 3: GP Engine (Week 3)

**T3.1: GP core loop**
- Population init (random typed expressions)
- Tournament selection
- Crossover (subtree exchange) preserving type validity
- Three mutation strategies
- Elitism
- Acceptance: on a toy problem (find `mean(x, 10) - mean(x, 30)`), GP converges in < 20 generations

**T3.2: Generation logging & checkpointing**
- Log every generation: best fitness, mean fitness, diversity
- Checkpoint population each N generations
- Resume from checkpoint
- Acceptance: kill mid-run, resume, results match within tolerance

**T3.3: Orchestrator (`miner.py`)**
- High-level entry point: `python -m src.factor_mining.miner config.yaml`
- Loads data, runs GP, saves pool
- Reproducibility via seed
- Acceptance: same config + seed -> identical output

### Phase 4: GPU Acceleration (Week 4)

**T4.1: GPU operators**
- Reimplement hot operators (ts_mean, ts_std, ts_rank, ts_corr, cs_rank, cs_zscore) in CuPy
- Numerical equivalence check vs CPU (atol=1e-5)
- Acceptance: 1000 random expressions, CPU vs GPU max diff < 1e-5 for finite values

**T4.2: Batched evaluation**
- Evaluate K expressions per GPU batch (K configurable, default 64)
- Common-subtree caching within batch
- Acceptance: batch eval ≥ 10x faster than single-expression-loop on GPU

**T4.3: Memory management**
- Pin OHLCV to GPU
- Track GPU memory, evict cache when > 80% of 16GB
- Acceptance: 50-generation run on full universe (5000 stocks, 5 years) completes without OOM

### Phase 5: Integration (Week 5)

**T5.1: MinedFactorHandler**
- Qlib `DataHandlerLP` subclass that reads `factor_pool.parquet`
- Plugs into existing PipelineConfig
- Acceptance: pipeline runs end-to-end with mined factors, produces backtest

**T5.2: Validator (in-sample vs out-of-sample)**
- Re-evaluate top-K factors on OOS window
- Generate validation report (HTML)
- Acceptance: validator catches synthetic overfit factors (high IS, near-zero OOS IR)

**T5.3: Promotion workflow**
- CLI: `python -m src.factor_mining.promote --run {id} --to production/{version}`
- Validates promotion criteria
- Acceptance: bad runs are rejected with clear reasons

### Phase 6: Polish

**T6.1: Walk-forward integration**
- Hook into your existing `config_walk_n*.yaml` framework
- Re-mine factors per fold (or use the same pool, configurable)
- Acceptance: walk-forward with mined factors completes; reports comparable to hand-crafted baseline

**T6.2: Documentation**
- `docs/factor_mining.md` — user guide
- `docs/factor_mining_architecture.md` — internal design (this doc, polished)
- Acceptance: a new user can run a smoke test from docs alone

**T6.3: OpenSpec validation**
- `openspec validate --specs --strict` passes
- Archive change after merge

---

## 8. Configuration

### 8.1 Example: `config/factor_mining/default.yaml`

```yaml
# Data
data:
  provider_uri: "D:/qlib_data/my_cn_data"
  instruments: "all"
  features: ["$open", "$high", "$low", "$close", "$volume", "$vwap"]
  start_date: "2017-01-01"
  end_date: "2023-12-31"
  
# Time splits
splits:
  train_start: "2017-01-01"
  train_end: "2021-12-31"
  valid_start: "2022-01-01"  
  valid_end: "2022-12-31"
  test_start: "2023-01-01"
  test_end: "2023-12-31"

# Forward return definition
return:
  expression: "Ref($open, -2) / Ref($open, -1) - 1"
  
# Universe filtering
universe:
  min_price: 2.0
  min_volume_pct: 0.1     # bottom 10% by 60-day avg volume excluded
  exclude_st: true
  exclude_listed_days: 60

# GP parameters
gp:
  population_size: 500
  generations: 50
  tournament_size: 3
  crossover_prob: 0.7
  subtree_mutation_prob: 0.15
  point_mutation_prob: 0.10
  constant_mutation_prob: 0.05
  elitism_pct: 0.05
  max_tree_depth: 6
  min_tree_depth: 2
  random_seed: 42

# Fitness weights
fitness:
  w_ic: 1.0
  w_ir: 0.5
  w_rankic: 0.5
  w_turnover: 0.2
  w_correlation: 0.8
  w_complexity: 0.01

# Filters
filters:
  min_coverage: 0.8
  min_xs_std_days_pct: 0.7
  min_xs_std: 1.0e-6

# Pool management
pool:
  max_size: 100
  novelty_corr_threshold: 0.7
  save_path: "research/mined_factors/runs/${run_id}"

# Compute
compute:
  backend: "gpu"            # "cpu" | "gpu"
  batch_size: 64
  gpu_memory_limit_gb: 14   # leave headroom on 16GB card
  num_cpu_workers: 4        # for data loading

# Logging
logging:
  level: "INFO"
  generation_log_csv: true
  checkpoint_every_n_gen: 5
```

### 8.2 Smoke config: `config/factor_mining/smoke.yaml`
Same as default but: population=50, generations=5, instruments=csi300, ~5min run.

---

## 9. Risk & Mitigation

| Risk | Probability | Mitigation |
|------|-------------|------------|
| Overfitting to in-sample | High | Strict OOS validation; promotion gates; correlation penalties |
| GP gets stuck in local optima | Medium | Diverse mutation; periodic random injection (10% of pop every 10 gens) |
| GPU memory OOM with full universe | Medium | Configurable batch size, memory monitoring, graceful CPU fallback |
| Mined factors look great IS, terrible OOS | High | This is the #1 failure mode in factor mining. Mandatory walk-forward validation before promotion. Avoid "magic" constants in grammar |
| Slow iteration cycle | Medium | Cache subtree evaluations; smoke config for quick tests; GPU acceleration |
| Numerical instability (NaN/Inf propagation) | Medium | `_safe` variants of div/log/sqrt; hard coverage filters |
| Pool grows too correlated over time | Medium | Hard novelty threshold; periodic re-evaluation of all pool members |

---

## 10. Success Metrics

After Phase 5 (integration), the AFM should deliver:

**Quantitative:**
- Mined pool of 50-100 factors with OOS IR > 0.3 (each)
- Adding mined factors to model improves OOS Sharpe by ≥ 10% vs Alpha158-only baseline
- Average pairwise correlation among pool factors < 0.5
- Full GP run completes in < 90 minutes on target hardware

**Qualitative:**
- Mined expressions are human-readable (parsimony enforced)
- At least 30% of top-20 factors are "novel" (not common knowledge equivalents)
- Walk-forward stability: factors don't degrade dramatically in newer windows

---

## 11. Open Questions for User

1. **Survivorship bias data**: does your qlib data include delisted stocks? If not, mined factors will overestimate returns. Worth checking before Phase 5.

2. **Transaction cost model in fitness?**: Currently fitness uses `turnover * w_turn`. Should we use a more accurate cost model (e.g., 30bps round-trip)?

3. **Industry / size neutralization?**: Should `cs_*` operators have variants that operate within industry/size buckets? (Adds complexity but reduces style-factor leakage.)

4. **External primitives**: Allow injection of fundamental data ($pe, $pb) when available? Default: OHLCV only for v1.

5. **Promotion frequency**: Manual gate, or auto-promote when criteria met?

---

## 12. Next Steps for AI Agent

When ready to implement, give the agent this document + the OpenSpec change scaffold, then:

1. Start with **Phase 1 tasks** (T1.1 → T1.3) — pure Python, no GPU dependencies yet. This validates the design works on toy data.
2. Run smoke config end-to-end before Phase 4 (GPU). Verify correctness on small universe.
3. GPU port should be a pure performance optimization — CPU outputs must remain the reference.
4. Don't promote any factors to production until Phase 5 validator passes on real OOS data.

Each phase should be one OpenSpec proposal (or sub-change). Don't try to land all 6 phases in one PR.
