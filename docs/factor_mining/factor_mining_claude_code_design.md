# Factor Mining — Claude Code Implementation Design (v2)

> **What this is**: A re-planned design for the Automated Factor Mining (AFM) subsystem, specifically structured for implementation by **Claude Code** against the **current** Quant_Ashare repo (where PIT universe Phase A–D is already wired).
>
> **What changed from the original `factor_mining_design.md`**:
> 1. **Data layer is now `PITDataProvider`, not raw qlib.** This is the single biggest change. The original design called `qlib.init` + `D.features` directly. That path is contaminated (ticker reuse, survivorship). All factor evaluation MUST go through the PIT layer you just built, or the entire PIT investment is wasted.
> 2. **Restructured for Claude Code's strengths.** Claude Code can read your whole repo. So this design starts with a mandatory inventory step (read the real interfaces) instead of me guessing them.
> 3. **Maps onto your OpenSpec workflow** (`/opsx:propose` → `/opsx:apply` → `/opsx:archive`) and **AGENTS.md conventions** (e.g. #10 whole-file diffs).
> 4. **Explicit stopping points.** Claude Code tends to run an entire task to completion. This design inserts hard checkpoints so you stay in control.
>
> **Hardware**: RTX 4060 Ti 16GB + i7 CPU.
> **Effort**: ~5–6 weeks if done carefully, one phase at a time.

---

## 0. How to Use This Document with Claude Code

### 0.1 The Core Workflow Loop

For **each** phase below, the loop is:

```
1. You: paste the phase's "Claude Code Prompt" (Section 7) into a fresh Claude Code session
2. Claude Code: runs Phase 0 inventory if not done, then /opsx:propose for this phase
3. You: review the OpenSpec proposal/design/tasks/spec — confirm scope
4. Claude Code: /opsx:apply (implements minimal version)
5. Claude Code: runs tests, reports results
6. STOP — you review against the phase acceptance criteria
7. You: commit (Claude Code does NOT auto-commit unless you say so)
8. Claude Code: /opsx:archive after validation
```

**One phase = one OpenSpec change = one PR/commit cluster.** Never let Claude Code start Phase 2 before Phase 1 is archived.

### 0.2 Why a Fresh Session Per Phase

Claude Code accumulates context. After a long phase, its context is full of dead ends, corrected code, and stale assumptions. A fresh session re-reads the repo cleanly. **Start each phase in a new session.** Phase 0's inventory output (`docs/factor_mining/inventory.md`) is what carries state between sessions — not the chat history.

### 0.3 Guardrails (paste these into every session)

```
GUARDRAILS for this work:
- Follow AGENTS.md strictly, especially the whole-file-diff rule (#10).
- One OpenSpec change per phase. Do not bundle phases.
- Do NOT commit or push unless I explicitly say "commit now".
- Do NOT modify src/pit/ — the PIT layer is done and frozen for this work.
- Do NOT call qlib.init or qlib.data.D directly anywhere in factor_mining.
  All data access goes through PITDataProvider.
- CPU correctness first. No GPU code until Phase 4, and only if Phase 1-3 are green.
- When you finish a task, STOP and report. Do not roll into the next task.
- If you're tempted to expand scope, add a TODO and ask me first.
```

---

## 1. The One Rule That Matters Most

> **Every factor value, every forward return, every IC computation MUST be sourced through `PITDataProvider`. Factor mining never touches `qlib.data.D` directly.**

Why this is non-negotiable:

- You spent weeks building PIT to fix ticker-reuse contamination and survivorship bias.
- If `factor_mining/evaluator.py` calls `D.features(...)` directly, it reads the **contaminated** data, and every mined factor's IC is inflated by exactly the bias you tried to remove.
- The mined factors would look great and fail live — the #1 failure mode, now self-inflicted.

So the data boundary is:

```
factor_mining/*  ──►  PITDataProvider  ──►  (qlib internals, NaN gaps, entity registry)
                      ^^^^^^^^^^^^^^^^
                      the ONLY data door
```

Phase 0 confirms the exact PITDataProvider signature. Phase 2 wires the evaluator to it. Every later phase inherits this.

---

## 2. Phase 0 — Repo Inventory (MANDATORY FIRST STEP)

Claude Code must produce `docs/factor_mining/inventory.md` before writing any factor mining code. This is the document that makes everything else accurate, because it records the **real** interfaces instead of assumed ones.

### 2.1 What Claude Code Must Discover

The inventory must answer these questions by reading actual code (not guessing):

**A. PIT data layer**
- What is the exact signature of `PITDataProvider.__init__`? (provider_uri? registry path? cache config?)
- What does `get_features(...)` actually return — column layout, index type (MultiIndex (datetime, instrument)?), `align` modes?
- Does `get_features` accept qlib expression strings (e.g. `"Ref($close, -1)"`) or only raw fields?
- How do you construct a forward-return series through the PIT layer?
- Where does the entity registry live on disk? What's the PIT provider_uri?
- Is there a factory/singleton for PITDataProvider, or do you instantiate per-run?

**B. Existing analysis utilities (to reuse, not reinvent)**
- Locate `SignalAnalyzer` / `FactorAnalyzer` (or equivalent). Exact import path?
- What's the constructor signature? What methods compute IC / RankIC / IR?
- What's the input format they expect — long DataFrame? wide? MultiIndex?
- Can they be driven from arbitrary factor values, or are they tied to model outputs?

**C. Pipeline integration points (for Phase 5)**
- How does `PipelineConfig` specify the feature handler today?
- How does the existing feature handler (Alpha158 etc.) plug in?
- Where would a `MinedFactorHandler` need to slot in?
- How does `FeatureDatasetBuilder` (or equivalent) consume features?

**D. Conventions**
- Read AGENTS.md fully. Summarize every rule that affects how code/tests/commits are written. Quote #10 (whole-file diff) verbatim.
- What's the test layout convention? (tests/logic/, tests/governance/, etc.)
- What's the OpenSpec change directory convention?
- What linter/formatter? (ruff? mypy? — check pyproject.toml)
- Python version, key dependency versions (pandas, qlib, numpy).

**E. Data reality**
- What features exist in the PIT provider? (OHLCV? vwap? amount? turn?)
- What's the date coverage of the PIT data?
- How many instruments in `all`? In `csi300`?

### 2.2 Inventory Output Format

`docs/factor_mining/inventory.md` should be concrete, with real code snippets pasted from the repo:

```markdown
# Factor Mining — Repo Inventory (generated YYYY-MM-DD)

## PIT Data Layer
- Module: src/pit/query.py
- Class: PITDataProvider
- Constructor: `def __init__(self, provider_uri: str, entity_registry_path: str, ...)`
  [paste actual signature]
- get_features returns: [describe actual return shape, paste example]
- Forward return construction: [how to do it through PIT]
- PIT provider_uri: D:/qlib_data/my_cn_data_pit (confirm actual path)
- Entity registry: [actual path]

## Analysis Utilities
- IC/IR computation: [class, path, signature]
- Reusable? [yes/no, how]

## Pipeline Integration
- Feature handler mechanism: [how it works today]
- MinedFactorHandler slot: [where it goes]

## Conventions (from AGENTS.md)
- #10 whole-file diff: "[verbatim quote]"
- Test layout: [...]
- Linter: [...]
- OpenSpec changes dir: [...]

## Data Reality
- Features available: [...]
- Date coverage: [...]
- Instrument counts: all=[...], csi300=[...]

## Open Questions / Surprises
- [anything that contradicts the design doc's assumptions]
```

### 2.3 Phase 0 Stopping Point

After Claude Code generates the inventory, **STOP**. You read it. If anything contradicts this design (e.g. PITDataProvider doesn't return what we assumed), we adjust the design before writing code. **Do not proceed to Phase 1 until the inventory is reviewed.**

This single step prevents the most expensive failure: building Phase 1–3 against an imagined interface, then discovering at Phase 5 that the real interface is different.

---

## 3. Architecture: Where Factor Mining Lives

### 3.1 Module Layout

```
src/
├── core/                          (existing, frozen for this work)
├── pit/                           (existing, FROZEN — do not modify)
│   ├── query.py                   #   PITDataProvider — the data door
│   ├── cache.py
│   └── ...
├── factor_mining/                 (NEW)
│   ├── __init__.py
│   ├── operators.py               # Phase 1: operator library (CPU)
│   ├── expression.py              # Phase 1: AST, serialization, hash
│   ├── grammar.py                 # Phase 1: type system, random generator
│   ├── pit_adapter.py             # Phase 2: thin bridge to PITDataProvider
│   ├── evaluator.py               # Phase 2: IC/IR/turnover via PIT data
│   ├── fitness.py                 # Phase 2: composite fitness
│   ├── factor_pool.py             # Phase 2: dedup, novelty, persistence
│   ├── gp_engine.py               # Phase 3: GP loop
│   ├── miner.py                   # Phase 3: orchestrator / CLI entry
│   ├── gpu_compute.py             # Phase 4: GPU kernels (optional)
│   ├── validator.py               # Phase 6: IS/OOS validation
│   └── promote.py                 # Phase 6: promotion CLI
├── handlers/                      (NEW, Phase 5)
│   └── mined_factor_handler.py    # consumes pool into training pipeline
└── ...

tests/factor_mining/              (NEW, mirrors src structure)

research/mined_factors/           (NEW, gitignored except README)
├── runs/{run_id}/
└── production/{version}/

config/factor_mining/             (NEW)
├── default.yaml
├── smoke.yaml
└── production.yaml

docs/factor_mining/               (NEW)
├── inventory.md                  # Phase 0 output
├── data_quality_caveats.md       # carries over PIT bias notes
└── user_guide.md                 # Phase 6
```

### 3.2 The `pit_adapter.py` Insight (New in v2)

The original design had `evaluator.py` talk to data directly. In v2, insert a thin adapter:

```python
# src/factor_mining/pit_adapter.py
class FactorMiningDataView:
    """
    The ONLY bridge between factor_mining and PITDataProvider.

    Responsibilities:
    - Load the OHLCV panel once, through PIT, for a given universe+date range
    - Provide the panel to the operator engine in a fixed, documented layout
    - Construct the forward-return label through PIT (entity-aware)
    - Guarantee entity boundaries are respected (NaN gaps preserved)

    Everything else in factor_mining sees ONLY this view, never qlib.
    """
    def __init__(self, pit: PITDataProvider, config: DataViewConfig): ...

    def load_panel(self) -> FeaturePanel:
        """Return the OHLCV (+derived) panel for the configured universe/dates."""

    def forward_return(self) -> pd.DataFrame:
        """Return the entity-aware forward-return label panel."""

    def universe_mask(self) -> pd.DataFrame:
        """Boolean (date, stock) mask of tradable membership per day."""
```

Why this matters for Claude Code: it creates **one file to get right**. If PIT integration is wrong, it's wrong in exactly one place, easy to test and fix. The GP engine, operators, fitness — none of them know PIT exists.

### 3.3 Decoupling Contract (unchanged from v1)

Factor mining and the training pipeline stay **decoupled by file contract**:
- Mining writes `factor_pool.parquet` + `factor_expressions.json`
- `MinedFactorHandler` (Phase 5) reads the pool into the training pipeline
- Mining can run independently, on its own long cadence

---

## 4. Core Concepts (carried from v1, with PIT constraints)

### 4.1 Expression Tree

A factor is a typed expression tree producing a `panel<date, stock> → float` cross-sectional factor.

- **Leaves**: primitive features confirmed to exist in the PIT provider (Phase 0 §E tells us which). Default v1: `$open $high $low $close $volume $vwap $amount $turn`.
- **Internal nodes**: operators (Section 5).
- **Constants**: windows from `{5, 10, 20, 40, 60}`, scalars from `{0.5, 1, 2}` — restricted to prevent magic-number overfitting.

### 4.2 Type System

```
T_FEATURE     — leaf features
T_FLOAT       — computed panel field
T_INT_WINDOW  — integer window, only as ts_* 2nd arg ∈ {5,10,20,40,60}
T_SCALAR      — scalar constant ∈ {0.5, 1, 2}
T_CSF         — cross-sectional factor; the REQUIRED root output type
```

The root of every expression MUST be `T_CSF`. This guarantees output is a cross-sectional factor, not a raw price.

### 4.3 PIT-Specific Constraint (New)

Because PIT data has **NaN gaps at entity boundaries**, all `ts_*` operators must use pandas rolling with `min_periods = window` (no partial windows that would silently span a gap). The NaN gap physically prevents cross-entity contamination — but only if operators don't backfill or use `min_periods=1`. Phase 1 operators must respect this. Phase 0 inventory confirms gap structure.

### 4.4 Genetic Operations (unchanged)

- Selection: tournament (k=3) + elitism (top 5%)
- Crossover: subtree exchange (p=0.7), type-preserving
- Mutation: subtree (0.15), point (0.10), constant (0.05)
- Population: 500 × 50 generations (~25k evals/run)
- Diversity: hash dedup per generation + correlation niche penalty

---

## 5. Operator Library (Phase 1)

### 5.1 Operator Set (CPU reference implementation)

```python
# Arithmetic (T_FLOAT × T_FLOAT → T_FLOAT)
add, sub, mul, div_safe          # div_safe: 0/near-0 denom → NaN (never Inf)

# Unary (T_FLOAT → T_FLOAT)
neg, abs, log_safe, sqrt_safe, sign   # log/sqrt of <=0 → NaN

# Cross-sectional (T_FLOAT → T_CSF), per-date
cs_rank        # rank within day, normalized [-0.5, 0.5]
cs_zscore      # (x - mean_day) / std_day; std≈0 → 0 (not NaN)
cs_demean      # x - mean_day
cs_winsorize   # clip to [q05, q95] per day

# Time-series (T_FLOAT × T_INT_WINDOW → T_FLOAT), per-stock, min_periods=window
ts_mean, ts_std, ts_max, ts_min, ts_sum
ts_rank        # rolling rank, normalized; constant window → 0.5
ts_delta       # x - ref(x, n)
ts_pctchange   # x / ref(x, n) - 1
ts_argmax, ts_argmin
ts_corr, ts_cov            # ±Inf → NaN
ts_skew, ts_kurt
ts_decay_linear            # linear-weighted mean

# Conditional (T_FLOAT × T_FLOAT × T_FLOAT → T_FLOAT)
where          # cond > 0 ? a : b
```

### 5.2 Numerical Stability (every operator, mandatory)

This is the most common place AI-written operators break on real data. Each operator's CPU implementation must defensively handle:

| Operator | Required behavior |
|----------|-------------------|
| `div_safe` | zero / near-zero denominator → NaN, never Inf |
| `log_safe`, `sqrt_safe` | input ≤ 0 → NaN (don't raise) |
| `ts_rank` | constant window → 0.5 (mid rank), not NaN |
| `cs_zscore` | per-day std ≈ 0 → 0, not NaN (avoids contagion) |
| `ts_corr`, `ts_cov` | replace ±Inf with NaN |
| all `ts_*` | `min_periods = window` so partial windows are NaN (respects PIT gaps) |

### 5.3 Operator Test Requirement

Each operator test covers: normal input, NaN input, zero input, negative input (for log/sqrt), constant input (zero variance), single row, empty input, **and a PIT-gap input** (a series with a NaN gap in the middle — verify the operator doesn't bridge it).

The PIT-gap test is new in v2 and critical: it's the unit-level guarantee that mined factors won't cross entity boundaries.

---

## 6. Phase Breakdown (mapped to OpenSpec changes)

Each phase = one OpenSpec change. Names suggested below; adjust to your `/opsx:` conventions.

### Phase 1 — Foundations: `add-factor-mining-operators`
Pure Python, no data, no PIT. Operators + expression tree + grammar.

| Task | Deliverable | Acceptance |
|------|-------------|------------|
| 1.1 Operators (CPU) | `operators.py` + tests | All operators pass normal/edge/NaN/PIT-gap tests |
| 1.2 Expression tree | `expression.py` + tests | Serialize round-trips; hash stable; commutative ops hash-equal |
| 1.3 Grammar + generator | `grammar.py` + tests | 1000 random exprs, 100% type-valid, root=T_CSF, min_depth respected |
| 1.4 Integration smoke | `test_integration_smoke.py` | Hand-built 20-day reversal parses, round-trips, pretty-prints |

**Stopping point**: tests green, OpenSpec validated. No data touched yet.

### Phase 2 — Evaluation + PIT wiring: `add-factor-mining-evaluator`
The phase where PIT integration happens. Highest-risk phase for correctness.

| Task | Deliverable | Acceptance |
|------|-------------|------------|
| 2.1 PIT adapter | `pit_adapter.py` + tests | Loads panel + forward return through PITDataProvider ONLY; entity gaps preserved |
| 2.2 Single-factor evaluator | `evaluator.py` + tests | Known 20-day reversal yields plausible IC (0.01–0.05) on PIT data |
| 2.3 Fitness | `fitness.py` + tests | Composite fitness from §5.1 of v1 doc; configurable weights |
| 2.4 Factor pool | `factor_pool.py` + tests | add/dedup-by-hash/save/load; novelty by correlation; round-trips |

**Stopping point**: Run the evaluator on a real known factor through PIT. Compare its IC to the pre-PIT contaminated number — it should be **lower**. Document the delta. This is proof PIT integration works.

### Phase 3 — GP Engine: `add-factor-mining-gp-engine`
The search loop. CPU only.

| Task | Deliverable | Acceptance |
|------|-------------|------------|
| 3.1 GP core | `gp_engine.py` + tests | On toy target `mean(x,10)-mean(x,30)`, converges < 20 gens |
| 3.2 Logging + checkpoint | (in gp_engine) | Kill mid-run, resume, results match within tolerance |
| 3.3 Orchestrator/CLI | `miner.py` | `python -m src.factor_mining.miner config/factor_mining/smoke.yaml` runs end-to-end on CPU; same seed → identical output |

**Stopping point**: Run smoke config (csi300, pop=50, gen=5) end-to-end on CPU. Inspect 10 mined expressions manually — they should look reasonable, not `cs_rank(cs_rank(cs_rank($close)))`.

### Phase 4 — GPU Acceleration (OPTIONAL): `add-factor-mining-gpu`
Only if Phase 1–3 are green and CPU speed is the bottleneck. Pure performance optimization; CPU stays the correctness reference.

| Task | Deliverable | Acceptance |
|------|-------------|------------|
| 4.1 GPU operators | `gpu_compute.py` | 1000 random exprs, CPU vs GPU max diff < 1e-5 for finite values |
| 4.2 Batched eval | (in gpu_compute) | Batch ≥ 10× faster than single-expr GPU loop |
| 4.3 Memory mgmt | (in gpu_compute) | 50-gen run on full universe (5000×5yr) completes without OOM on 16GB |

**Stopping point**: GPU and CPU produce numerically equivalent factors. If not, GPU is wrong — CPU wins.

### Phase 5 — Pipeline Integration: `add-mined-factor-handler`
Plug mined factors into the existing training pipeline. **Requires PIT Phase D done (it is).**

| Task | Deliverable | Acceptance |
|------|-------------|------------|
| 5.1 MinedFactorHandler | `handlers/mined_factor_handler.py` + tests | Reads pool; plugs into PipelineConfig; pipeline runs end-to-end producing a backtest |
| 5.2 Config wiring | `config/factor_mining/*.yaml` + pipeline config example | A training run can select mined factors instead of / alongside Alpha158 |

**Stopping point**: Full pipeline run with mined factors completes and produces a backtest. Sharpe is whatever it is — don't tune yet, just confirm the wiring.

### Phase 6 — Validation + Promotion: `add-factor-mining-validation`
Make mined factors trustworthy before they reach production.

| Task | Deliverable | Acceptance |
|------|-------------|------------|
| 6.1 Validator (IS/OOS) | `validator.py` + tests | Catches synthetic overfit factor (high IS IR, ~0 OOS IR) |
| 6.2 Promotion CLI | `promote.py` + tests | `python -m src.factor_mining.promote --run {id} --to production/{ver}`; rejects bad runs with reasons |
| 6.3 Walk-forward hook | integration with `config_walk_n*.yaml` | Walk-forward with mined factors completes; report comparable to Alpha158 baseline |
| 6.4 User guide | `docs/factor_mining/user_guide.md` | New user runs a smoke test from docs alone |

**Stopping point**: Validator demonstrably rejects a known-overfit factor. Promotion gate works. Only now are mined factors allowed near production.

---

## 7. Claude Code Prompts (one per phase)

Paste these into a **fresh** Claude Code session. Each assumes `docs/factor_mining/inventory.md` exists (Phase 0). Always prepend the Guardrails block from §0.3.

### 7.0 Phase 0 Prompt (run this first, once)

```
Read AGENTS.md and the three design docs in docs/factor_mining/ (if present)
or that I'll paste. Then produce docs/factor_mining/inventory.md per Section 2
of the factor mining design doc.

Your job in this session is ONLY to read the repo and write the inventory.
Do NOT write any factor mining code. Do NOT create OpenSpec changes yet.

Specifically discover and document, with real code snippets pasted from the repo:
- PITDataProvider exact signature, get_features return shape, how to build a
  forward-return series through it, the PIT provider_uri and registry path
- The existing IC/IR/RankIC computation utility (SignalAnalyzer/FactorAnalyzer
  or whatever exists) — import path, constructor, methods, input format
- How PipelineConfig selects a feature handler today, and where a
  MinedFactorHandler would slot in
- AGENTS.md rules affecting code/tests/commits — quote rule #10 verbatim
- Test layout, linter/formatter, OpenSpec change dir convention
- Features available in the PIT provider, date coverage, instrument counts

End by listing any surprises that contradict the design doc's assumptions.
Then STOP. I will review the inventory before we proceed.
```

### 7.1 Phase 1 Prompt

```
[Guardrails block from §0.3]

Read docs/factor_mining/inventory.md for current repo facts.

Task: Implement Phase 1 (Foundations) of factor mining — operators, expression
tree, grammar. Pure Python, NO data access, NO PIT, NO GPU.

Workflow:
1. /opsx:propose add-factor-mining-operators
   Scope: src/factor_mining/{operators,expression,grammar}.py + tests.
   Reference Section 5 and 4.2 of the design doc for operator set and types.
2. STOP and show me the proposal/design/tasks/spec. Wait for my OK.
3. /opsx:apply — implement minimal version.
4. Critical requirements:
   - Every operator handles NaN/zero/negative/constant/empty AND a PIT-gap input
     (a series with a NaN hole that must NOT be bridged). min_periods=window on
     all ts_* ops.
   - Commutative operators (add, mul, min, max) must hash identically regardless
     of argument order.
   - Type system per §4.2; expression root MUST be T_CSF.
   - Random generator: 1000 samples must be 100% type-valid, min_depth=2.
5. Run: pytest tests/factor_mining/ -v ; plus the repo's linter.
6. STOP and report results against the Phase 1 acceptance criteria.

Do NOT compute IC, do NOT touch data, do NOT write the GP engine.
Do NOT commit until I say so.
```

### 7.2 Phase 2 Prompt

```
[Guardrails block from §0.3]

Read docs/factor_mining/inventory.md. Phase 1 is archived.

Task: Implement Phase 2 (Evaluation + PIT wiring). This is the phase where factor
mining first touches data — and it must touch data ONLY through PITDataProvider.

Workflow:
1. /opsx:propose add-factor-mining-evaluator
   Scope: pit_adapter.py, evaluator.py, fitness.py, factor_pool.py + tests.
2. STOP, show me the proposal. Wait for OK.
3. /opsx:apply.
4. Critical requirements:
   - pit_adapter.py is the ONLY file that imports/uses PITDataProvider.
     evaluator/fitness/pool must not import qlib or PIT directly.
   - Use the EXACT PITDataProvider interface from inventory.md, not assumptions.
   - Forward return constructed entity-aware through PIT.
   - Evaluator reuses the existing IC/IR utility from inventory.md if compatible;
     if not, document why and implement minimally.
5. Validation:
   - Build a known 20-day reversal factor by hand.
   - Evaluate it through the PIT adapter.
   - Report its IC. It should be plausible (0.01-0.05) and LOWER than the
     pre-PIT contaminated value if you can find that number.
6. STOP and report. Include the IC delta (PIT vs contaminated) if available.

Do NOT write the GP engine. Do NOT commit until I say so.
```

### 7.3 Phases 3–6 Prompt Template

```
[Guardrails block from §0.3]

Read docs/factor_mining/inventory.md. Phase {N-1} is archived.

Task: Implement Phase {N} ({name}) per the design doc Section 6.

Workflow:
1. /opsx:propose {change-name}
   Scope: {files for this phase}
2. STOP, show me the proposal. Wait for OK.
3. /opsx:apply.
4. Critical requirements: {phase-specific, from the Section 6 table}
5. Run tests + linter.
6. STOP and report against this phase's acceptance criteria + stopping point.

Constraints specific to this phase:
{e.g. Phase 4: CPU stays the reference, GPU must match within 1e-5}
{e.g. Phase 5: requires PIT Phase D; MinedFactorHandler reads pool file contract}
{e.g. Phase 6: validator must reject a synthetic overfit factor}

Do NOT proceed to Phase {N+1}. Do NOT commit until I say so.
```

---

## 8. Configuration

`config/factor_mining/default.yaml` — same as v1 §8.1 but with **two changes**:

```yaml
data:
  # CHANGED: point at the PIT provider, not the raw one
  provider_uri: "D:/qlib_data/my_cn_data_pit"
  entity_registry_path: "..."     # NEW: from inventory.md
  instruments: "all"
  features: ["$open", "$high", "$low", "$close", "$volume", "$vwap", "$amount", "$turn"]
  start_date: "2017-01-01"
  end_date: "2023-12-31"

# everything else (splits, return, gp, fitness, filters, pool, compute, logging)
# carries over from factor_mining_design.md §8.1 unchanged

compute:
  backend: "cpu"        # CHANGED default: cpu until Phase 4 proves GPU equivalence
  batch_size: 64
  gpu_memory_limit_gb: 14
```

`smoke.yaml`: instruments=csi300, population=50, generations=5, ~5 min CPU run.

---

## 9. Risks & Mitigations (v2-specific)

| Risk | Mitigation |
|------|------------|
| Claude Code calls qlib.D directly, bypassing PIT | pit_adapter.py is the only data door; grep for `qlib.data` in factor_mining must return nothing outside pit_adapter; add a CI check |
| Claude Code runs all 6 phases in one session | Hard stopping points; one OpenSpec change per phase; fresh session per phase |
| inventory.md assumptions wrong → Phase 5 rework | Phase 0 reviewed before any code; Phase 2 validates PIT interface early |
| ts_* bridges entity NaN gap → contamination returns | min_periods=window; PIT-gap unit test on every ts operator |
| GPU diverges from CPU silently | CPU is reference; 1e-5 equivalence test gate |
| Mined factors overfit | Phase 6 validator + promotion gate; OOS on PIT data is now trustworthy |
| Scope creep ("while I'm here") | Guardrails block; one change per phase; TODO-and-ask rule |

---

## 10. Success Criteria

**After Phase 5** (integration):
- Pipeline runs end-to-end with mined factors, produces a backtest
- All factor data flows through PITDataProvider (verified by grep + CI check)
- A grep for `qlib.data.D` or `qlib.init` in src/factor_mining/ returns matches ONLY in pit_adapter.py (ideally zero — even the adapter goes through PITDataProvider)

**After Phase 6** (validation):
- Mined pool of 50–100 factors, each with OOS IR > 0.3 **on PIT data**
- Adding mined factors improves OOS Sharpe ≥ 10% vs Alpha158-only baseline
- Validator rejects synthetic overfit factors
- Avg pairwise correlation among pool < 0.5
- Full GP run < 90 min on target hardware (CPU acceptable; GPU faster)

**Reality check**: Because data is now PIT-correct, expect mined-factor metrics to be **lower but trustworthy** compared to what contaminated data would have shown. A real OOS IR of 0.3 on clean data beats a fake 0.6 on contaminated data.

---

## 11. What Carries Over Unchanged from the Original Docs

To avoid duplication, these sections of the original `factor_mining_design.md` still apply verbatim:
- §5.1 fitness formula
- §5.2 validity filters (coverage, variance, sanity)
- §6.2 persistence format (factor_pool.parquet + factor_expressions.json)
- §6.3 versioning & promotion tiers

And from `factor_mining_phase1_preflight.md`:
- §1 decision records (the 5 open questions — answer them before Phase 2)
- §4 implementation notes (numerical stability, hash commutativity, type strictness)

The PIT-specific decision from those open questions is already made: **survivorship/ticker-reuse is handled by the PIT layer**, so the old "universe_stability_penalty" workaround is no longer needed. The fitness function operates on clean PIT data.

---

## 12. The Single Most Important Check

Before Phase 5 is considered done, run this and confirm it returns nothing (or only pit_adapter.py):

```bash
grep -rn "qlib.init\|qlib.data\|from qlib" src/factor_mining/ | grep -v pit_adapter.py
```

If this returns matches, factor mining is reading contaminated data somewhere, and the mined factors are not trustworthy. This one grep is the difference between a real factor mining system and an expensive way to overfit to survivorship bias.
