# Factor Mining — Design Decisions

> **Status**: All five decisions locked 2026-05-22 (D1–D4 from the
> original draft + D5 added after Phase 0 inventory). Locking happened
> in two passes:
>
> - 2026-05-22 (initial): D1 and D4 locked; D2 and D3 declared as
>   intent pending Phase 0 inventory confirmation.
> - 2026-05-22 (post-Phase-0): D2 and D3 finalized against the
>   Phase 0 findings in `inventory.md`; D5 added.
>
> **Owner**: James
> **Context**: The 5th open question from `factor_mining_phase1_preflight.md`
> (survivorship / ticker-reuse) is already resolved by the PIT universe
> layer and is therefore not listed here.

---

## Decision Summary

| # | Question | Decision | Finalized? |
|---|----------|----------|-----------|
| D1 | Transaction cost model | **B — annualized round-trip, cost_rate = 0.003** | ✅ Final |
| D2 | Industry / size neutralization | **v1: none; grammar keeps `group_by` hook for v2** | ✅ Final (locked post-Phase-0) |
| D3 | Feature universe | **superseded → 12 fields (6 OHLCV/money + 6 `daily_basic`); see D3 amendment** | ⚠️ → moot: the 6 fundamentals' GP-validation is shelved with GP (see D6) |
| D4 | Promotion workflow | **Manual gated** | ✅ Final |
| D5 | PIT data gate strictness | **Zero `qlib.data`/`qlib.init` matches under `src/factor_mining/`** | ✅ Final |
| D6 | GP factor mining in production | **Shelved — not on the recommendation path** | ✅ Decided (C2-b; revisit-gated) |

---

## D1: Transaction Cost Model — DECIDED (Final)

**Decision**: Option B — annualized round-trip cost.

```
transaction_cost(turnover_daily) = turnover_daily × 252 × cost_rate
cost_rate = 0.003   # 0.3% round-trip, configurable
```

**Rationale**:
- A-share round-trip cost (post-2023-08 stamp tax halving) is ~0.2–0.3%: stamp tax 0.05% (sell) + commission ~0.05% (both sides) + slippage/impact ~0.1–0.2%.
- 0.3% gives a small conservative buffer so GP doesn't favor high-turnover factors whose theoretical edge is eaten by costs.
- Annualized form aligns dimensionally with annualized IC, so the penalty is comparable to the signal term in fitness.
- PIT data makes turnover computation accurate — ticker-reuse no longer creates phantom position changes.

**Implementation**:
```yaml
# config/factor_mining/default.yaml
fitness:
  cost_rate: 0.003        # round-trip cost, tune here
  w_turnover: 0.2
```

Fitness turnover term:
```
- w_turnover * (turnover_daily * 252 * cost_rate)
```

---

## D2: Industry / Size Neutralization — DECIDED (Final, post-Phase 0)

**Decision**: v1 does NOT implement neutralization. Grammar keeps an
extension hook (`group_by` parameter on `cs_*` operators) for v2, but
the Phase 1 random generator samples ONLY `group_by=None`.

**Phase 0 confirmation**: The inventory survey of `src/data/pit/`,
`src/data/`, `src/contracts/`, and `src/core/` turned up **no
structured industry classification source** (only the coarse
`stock_basic.industry` Tushare field, referenced indirectly via
`src/core/attribution_industry_loader.py`). With no reliable
PIT-aware industry table available, v1 neutralization is impossible
to implement correctly regardless of design intent. Decision locked.

**Rationale (carry-over)**:
- Adds ~2× search space and runtime.
- Industry classification data quality is uncertain — Tushare `stock_basic.industry` is coarse; proper SW (申万) classification needs a separate, maintained source.
- The PIT entity registry makes future neutralization natural: an industry label can hang off each entity, cleaner than tagging raw tickers.

**Implementation** (hook only, not sampled in v1):
```python
class CSOperator:
    def __init__(self, group_by: Optional[str] = None):
        self.group_by = group_by    # None = full universe; 'industry' = within-industry (v2)
# v1 grammar samples ONLY group_by=None
```

---

## D3: Feature Universe — DECIDED (Final, post-Phase 0)

> **⚠️ AMENDMENT (C2 — supersedes the "6 fields Final" text below).** The
> archived OpenSpec change `extend-feature-universe-with-daily-basic`
> (#187, 2026-05-27) extended the terminal universe from 6 to **12
> fields**: the 6 OHLCV/money fields below **plus 6 `daily_basic`
> fundamentals** `$pe $pb $ps $turnover_rate $circ_mv $total_mv`. The live
> source of truth is `FeatureRegistry.V1` in
> `src/factor_mining/grammar.py` (12 fields) — NOT this section and NOT
> any config key (the dead `features:` key in
> `config/factor_mining/default.yaml` was removed in C2).
>
> **This is NOT a confirmation that 12 fields is correct.** Whether the 6
> `daily_basic` fundamentals earn their place is **PENDING C2 validation**
> — a fair GP-vs-Alpha158 comparison on the C1 leak-free (embargo-gapped)
> walk-forward folds against the clean Alpha158 baseline (mean IR ≈ 0.30,
> see `docs/phase_c1_result.md`). The 6 fundamentals stay or go based on
> that result. The original 6-field rationale below is kept for history.

**Decision**: v1 feature universe is the **6 fields the PIT qlib bins
actually contain**:

```
$open $high $low $close $volume $money
```

`$vwap` is available as a **derived expression** (`$money / $volume`)
when grammar wants it. `$turn` (daily turnover rate) is deferred to v2
pending a separate Tushare `daily_basic` ingest (out of scope for
factor mining itself).

**Phase 0 confirmation**: Per [src/data/pit/qlib_bin_builder.py:80](src/data/pit/qlib_bin_builder.py:80),
the PIT bins write exactly:

```python
BIN_FEATURE_FIELDS: tuple[str, ...] = (
    "open", "high", "low", "close", "volume", "money",
)
```

The original D3 draft (`$open $high $low $close $volume $vwap $amount $turn`)
assumed 8 fields. **The PIT provider has 6, with `$money` standing in
for the `$amount` concept** (Tushare `amount` × 1000 from 千元 to
yuan). `$vwap` and `$turn` are not in the bins.

**Naming convention**: the grammar exposes `$money` (matching the bin
name), not `$amount` — this avoids an aliasing layer and keeps the
inventory's source-of-truth alignment.

**Rationale (carry-over)**:
- Price-volume factors are the core of A-share daily-frequency alpha.
- Fundamentals (pe/pb/ps/mktcap) are quarterly — frequency mismatch with daily strategy, need forward-fill handling; not worth it for v1.

**Critical caveat (NEW from Phase 0)**: per the qlib_bin_builder
adjusted-price contract (see also `inventory.md` §F.2 and the
companion document `scale_invariance.md`), the bins store **pre-adjusted**
prices using an **as-of-today** snapshot of `adj_factor`. Absolute
adjusted prices are NOT PIT-correct features. **The grammar SHALL
reject expressions whose value carries an unbounded per-ticker
`adj_factor` constant** — see `scale_invariance.md` for the type
rules. This is a stronger constraint than "root must be T_CSF" alone.

**Implementation** (extension point for v2):
```python
class FeatureRegistry:
    V1_RAW_PRICE  = {"$open", "$high", "$low", "$close"}   # ADJ_TAINTED leaves
    V1_SCALE_FREE = {"$volume", "$money"}                  # PURE leaves
    V1 = V1_RAW_PRICE | V1_SCALE_FREE                      # 6 total
    V2 = V1 | {"$turn"} | {"$pe", "$pb", "$ps", "$mktcap"} # reserved, NOT enabled in v1

    @classmethod
    def current(cls) -> set:
        return cls.V1
```

`$vwap = $money / $volume` is computed as part of the operator graph
at expression evaluation time, not registered as a separate terminal.

---

## D4: Promotion Workflow — DECIDED (Final)

**Decision**: Manual gated. No auto-promotion in v1.

**Rationale**:
- A mined factor going to production unreviewed is the highest-risk path to silent overfitting.
- Manual gate forces a human to inspect each factor, building judgment.
- PIT makes OOS validation trustworthy (clean data), so the metrics a human reviews at the gate are real. Manual gate + trustworthy OOS = the safest combination.

**Directory structure**:
```
research/mined_factors/
├── runs/{run_id}/          # every GP run, auto-saved
│   └── factor_pool.parquet
├── candidates/{date}/      # researcher manually copies promising factors here
│   └── {factor_id}.json
└── production/{version}/    # researcher manually promotes after OOS review
    └── factor_pool.parquet  # training pipeline reads from here
```

**Promotion criteria** (configurable, but a human presses the button):
- OOS IR > 0.3
- OOS RankIC mean > 0.02
- Max correlation with existing production factors < 0.6
- Stability: rolling 6-month IR > 0.2 in ≥ 70% of windows

**Implementation**: `promote.py` CLI (Phase 6), validates criteria, rejects bad runs with explicit reasons. Never auto-runs.

---

## D5: PIT Data Gate Strictness — DECIDED (Final, NEW post-Phase 0)

**Decision**: Strict mode — **zero `qlib.data` / `qlib.init` matches
under `src/factor_mining/`**, including the PIT adapter.

```bash
grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/
# MUST return zero matches.
```

**What this means concretely**:

- `src/factor_mining/pit_adapter.py` (Phase 2) is **not allowed** to
  `from qlib.data import D` or to call `qlib.init`. Instead, it
  delegates 100% to `src.pit.query.PITDataProvider.get_features`,
  which already encapsulates the qlib import + post-delist mask + LRU
  cache.
- The adapter's job is purely **shape adaptation**: `swaplevel()` to
  convert PIT's `(instrument, datetime)` MultiIndex to the
  `(datetime, instrument)` order the downstream evaluator wants, plus
  helper methods like `forward_return(horizon: int)` that internally
  call `pit.get_features(["Ref($close, -<horizon>) / $close - 1"], ...)`.
- A CI check enforces the rule. The repo's pre-commit hook
  (`.githooks/pre-commit`) is the natural home.

**Rationale**:
- The original `factor_mining_claude_code_design.md` §10 wrote the
  rule as "should return matches ONLY in pit_adapter.py (ideally
  zero — even the adapter goes through PITDataProvider)". The
  parenthetical IS the right rule; the looser "only in pit_adapter"
  version invites the next maintainer to inline a "small qlib call,
  just this once" into the adapter, which is exactly how data-layer
  bypasses creep back in.
- `PITDataProvider.get_features` already accepts qlib expression
  strings (e.g. `"Ref($close, -1)"`) and applies the post-delist
  mask. Anything `pit_adapter` would call `D.features` for, it can
  call `pit.get_features` for instead with identical semantics —
  except cleaner (cache + mask are free).
- A black-and-white rule survives drift better than a "one
  exception" rule.

**Phase 2 enforcement**: the grep above runs as part of the Phase 2
spec's governance test. Failure to satisfy it blocks merge.

---

## D6: GP Factor Mining in Production — DECIDED (Shelved, C2-b, 2026-06-05)

**Decision**: the GP factor-mining line is **shelved** — it does NOT go on
the production stock-recommendation path. Alpha158 stays the sole feature
source for the canonical pipeline. (The GP subsystem and its coverage fix
remain in the tree, correct and tested — see #217 — just unused in
production.)

**Why — a fair OOS comparison shows no edge.** A leak-free, same-window
dry-run (C2-b): the same 11 walk-forward OOS folds (2023-Q1 .. 2025-Q3),
same pipeline (#212 embargo gap, #179 risk constraints, #181 microstructure
mask, same LGB config, same PIT bundle) — the only variable is the feature
handler. GP top-50 (frozen by in-sample fitness, mined 2018-2021) vs
Alpha158:

- mean IR: **GP −0.10** vs **Alpha158 +0.19**
- mean IC(1d): **GP ≈0.0004** (no predictive power) vs **Alpha158 ≈0.035**
- annualized return: **GP −0.85%** vs **Alpha158 +3.4%**

The GP factors were already **weak in-sample** (top-50 IS `|rank_ic|` ≤ 0.011,
fitness all negative) and OOS IC collapsed to ≈0 — i.e. the current GP
grammar/fitness does not find signal even in-sample, so a bigger eval budget
alone is unlikely to fix it.

**Revisit conditions** (what would reopen this): only after (a) the Alpha158
signal is demonstrably exhausted on this universe/window, AND (b) the GP
grammar / fitness is redesigned (not merely a larger eval budget). Any
re-evaluation MUST use the same fair protocol — leak-free embargo-gapped
folds, frozen IS-only factor selection, same-window Alpha158 baseline.

**This also makes D3's "PENDING C2 GP validation" moot**: whether the 6
`daily_basic` fundamentals earn their place was to be settled by GP; with GP
shelved, that question is parked alongside it (the fields remain in
`FeatureRegistry.V1`, simply unexercised by a production GP line).

**Evidence**: `docs/phase_c2b_dryrun_result.md` (full comparison table +
caveats). Caveat: this is a single-frozen-mining dry-run on a small smoke
pool (200×20), single OOS window — a directional verdict, strong enough to
shelve, not a permanent close.

**Supersedes**: the "GP loses" conclusion in `empirical_results_b_std.md`
was based on a contaminated comparison (label look-ahead pre-#212 + IS/OOS
selection bias) and is voided; this clean verdict replaces it (see that
file's top note).

---

## Phase 0 outcomes

The Phase 0 inventory pass (see `docs/factor_mining/inventory.md`)
produced three findings that materially change Phase 1's scope. All
three are recorded as decisions or design-doc updates here, so Phase
1 can start without re-deriving them.

### O1 — Module location: `src/factor_mining/`, not `research/factor_lab/`

`factor_mining_claude_code_design.md` §3.1 places factor mining under
`src/factor_mining/` so the operator / expression / grammar code is
importable from production training paths via the feature-handler
registry. This **diverges from** the existing
`v2-project-skeleton-boundaries` spec, which expects research-only
code under `research/factor_lab/`.

**Resolution**:
- `research/factor_lab/` remains a placeholder (no behavior change).
- `src/factor_mining/` is a **new production-layer module** governed
  by `v2-feature-handler-registry` (registration seam) plus a new
  `v2-factor-mining-foundations` capability (Phase 1's OpenSpec
  change introduces this).
- Mined factor **output** (parquet manifest + per-factor parquet
  files) lives at `research/mined_factors/` per D4. That output is
  research artifact; only the `MinedFactor` handler at registration
  time imports it, and only when a training config opts in via
  `feature_handler: "MinedFactor"`.

Phase 1's OpenSpec proposal must MODIFY
`v2-project-skeleton-boundaries` to acknowledge `src/factor_mining/`
as a production-layer addition (not a relaxation of the
`research/factor_lab/` non-production contract).

### O2 — Adjusted-price contract drives a stricter grammar

`inventory.md` §F.2 surfaces a constraint stronger than "root must be
T_CSF": the qlib bins store pre-adjusted prices with an as-of-today
adj_factor snapshot, so any expression whose value depends on a
per-ticker `adj_factor` constant is NOT PIT-correct as a cross-
sectional factor.

**Resolution**: introduce a two-tier scale-invariance type system
into the Phase 1 grammar. The full type rules are formalized in
`docs/factor_mining/scale_invariance.md` (companion to this file).
Phase 1's grammar test must include the scale-invariance type
checker; Phase 1's spec must list `scale_invariance.md` as a
normative reference.

### O3 — Field-set is 6, not 8

D3 above; included here for the consolidated outcome list.

---

## Carry-over Note

These decisions supersede the 5 open questions in `factor_mining_phase1_preflight.md` §1. Specifically:
- Q1 (survivorship): resolved by PIT layer — no `universe_stability_penalty` needed.
- Q2 (cost): → D1.
- Q3 (neutralization): → D2.
- Q4 (features): → D3.
- Q5 (promotion): → D4.

The fitness function now operates on clean PIT data, so the original survivorship workaround is dropped entirely.

---

## Action Items Before Phase 2

- [x] Phase 0 inventory confirms industry/size data availability → finalize D2 (no neutralization, decided)
- [x] Phase 0 inventory confirms which of the 8 price-volume fields exist → finalize D3 (6 fields, decided)
- [x] D5 strict data gate locked
- [x] O1 module-location resolution documented
- [x] O2 scale-invariance rules formalized in `scale_invariance.md`
- [ ] D1 `cost_rate = 0.003` written into `config/factor_mining/default.yaml` (Phase 2 / Phase 3 task)
- [ ] D4 directory structure `research/mined_factors/{runs,candidates,production}/` created (Phase 5 task)
- [ ] D5 grep guard wired into `.githooks/pre-commit` (Phase 2 task)
- [x] `inventory.md` §E.2 date / instrument numbers backfilled from the probe (legacy bundle; 5492 / 591 / 9574 days). PIT-bundle re-probe pending after operator builds the PIT bins.
