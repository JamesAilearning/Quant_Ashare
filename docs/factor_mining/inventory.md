# Factor Mining — Repo Inventory (generated 2026-05-22)

> Phase 0 output per `factor_mining_claude_code_design.md` §2. Read-only
> survey of the repo as it exists today (branch
> `worktree-factor-mining+scaffold`, base `f5e62fb` on `main`). No
> factor-mining code has been written; this document is the contract
> later phases implement against.

---

## A. PIT Data Layer

### A.1 Two PIT modules — read carefully

The repo has **two distinct `pit/` packages** with different roles:

- **`src/pit/`** — the **query layer** consumers use. Contains
  `query.py` (PITDataProvider) and `cache.py` (LRUCache). This is the
  "data door" the design doc refers to.
- **`src/data/pit/`** — the **builders/validators** that write the
  artifacts the query layer reads (delisted registry, qlib bins, index
  membership, universe files, PIT validator).

Factor mining imports from `src.pit` only.

### A.2 `PITDataProvider` — exact signature

Located at [src/pit/query.py:76](src/pit/query.py:76):

```python
class PITDataProvider:
    """Read-only PIT-correct query layer over a qlib provider directory."""

    def __init__(
        self,
        provider_uri: str | Path,
        delisted_registry_path: str | Path,
        cache_max_entries: int = 256,
    ) -> None:
```

Construction internally calls `init_qlib_canonical(...)` via
`src.core.qlib_runtime`, pinning `data_adjust_mode=ADJUST_MODE_POST`
(close × adj_factor — see §A.6 "Adjusted-price caveat").

### A.3 Public API

```python
def get_universe(
    self,
    date: str | pd.Timestamp,
    universe_name: str = "all",
) -> list[str]: ...

def get_universe_range(
    self,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    universe_name: str = "all",
) -> dict[pd.Timestamp, list[str]]: ...

def get_features(
    self,
    fields: list[str],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    universe_name: str = "all",
    align: str = "universe",
    instruments: list[str] | None = None,
) -> pd.DataFrame:
```

**Key facts confirmed from source:**

- `fields` accepts **qlib expression strings** —
  `["$close", "Ref($close, -1)", "Mean($close, 20)"]` is valid.
  Factor mining can pass arbitrary qlib expressions through PIT,
  including forward-return expressions.
- Return shape: **`DataFrame` indexed by `(instrument, datetime)`
  MultiIndex** (NOT `(datetime, instrument)` — see §B.1 surprise),
  one column per field.
- `align` modes: `"universe"` (default) and `"tradable_only"` —
  currently equivalent; Phase D reserved.
- `instruments=...` takes precedence over `universe_name` when
  supplied. Phase D wiring uses this form so factor-resolved ticker
  lists are routed through the PIT mask.
- **Post-delist mask invariant**: every `(ticker, date)` with
  `date > delist_date` is forced to NaN, even when qlib window
  operators (`Mean($close, 20)`, `Ref(...)`) would otherwise leak
  across the boundary because qlib's default `min_periods < N`. This
  is the §4.3.2 mitigation. Factor-mining operators SHALL NOT undo
  this mask.
- **No `resolve_entity`** — A-share has no ticker reuse (PR #95);
  ticker is the stable identifier.
- **Read-only API**, no write methods, no singleton — instantiate per
  run (the LRU cache lives on the instance).

### A.4 Forward-return construction through PIT

The cleanest path is to pass a qlib expression directly:

```python
panel = pit.get_features(
    fields=["Ref($close, -2) / Ref($close, -1) - 1"],  # T+1 buy / T+2 sell
    start="2018-01-01", end="2025-12-31",
    universe_name="csi300",
)
```

This is what `decisions.md` D1 expects ("forward_return = (Ref(open,
-2) / Ref(open, -1)) - 1"). The post-delist mask correctly NaNs the
result on / past the last trading day.

For multi-horizon labels (e.g. 5-day return), use
`"Ref($close, -5) / $close - 1"` — same pattern.

Phase 2's `pit_adapter.py` should expose this as a single method,
e.g. `FactorMiningDataView.forward_return(horizon: int) -> DataFrame`,
so callers never assemble expression strings themselves.

### A.5 Provider URI and registry path

There is **no module-level default**. The two paths are constructor
arguments. Today's defaults in repo:

- Legacy qlib provider (non-PIT): `D:/qlib_data/my_cn_data` — declared
  in [config.yaml:5](config.yaml:5). This is the **non-PIT** bundle the
  pipeline currently uses by default.
- PIT-corrected provider: **operator-chosen output_dir of**
  [src/data/pit/qlib_bin_builder.py](src/data/pit/qlib_bin_builder.py).
  The design doc names `D:/qlib_data/my_cn_data_pit` but this is not
  hardcoded anywhere — **needs operator confirmation** before Phase 2.
- Delisted registry: parquet at an operator-chosen path; produced by
  [src/data/pit/delisted_registry.py](src/data/pit/delisted_registry.py).

### A.6 Adjusted-price caveat (load-bearing for factor mining)

Per the qlib_bin_builder docstring
([src/data/pit/qlib_bin_builder.py:39-44](src/data/pit/qlib_bin_builder.py:39)):

> The bin stores PRE-ADJUSTED prices (close × adj_factor and same for
> open/high/low). adj_factor is Tushare's as-of-today snapshot per
> §4.3.1, so **absolute adjusted prices are NOT PIT-correct features**.
> Downstream consumers MUST use **within-ticker ratios / returns
> only** (the contract is enforced at the Phase C query layer).

**Impact on factor mining**: the grammar SHALL NOT permit terminals
like `$close` to appear at the root of an expression in a way that
treats absolute level as the feature. Cross-sectional rank
(`cs_rank($close)`) is also suspect because the absolute level
already has the as-of-today adj_factor baked in. **Acceptable forms**
are within-ticker ratios (`$close / Ref($close, 20) - 1`), differences
(`ts_delta($close, 20)`), and any expression whose output is invariant
to a single ticker's static rescaling.

This is a stronger constraint than the design doc's
"T_CSF root" rule alone. The Phase 1 grammar must enforce it.

---

## B. Existing Analysis Utilities

Two analyzers exist; both are reusable but with different costs.

### B.1 `SignalAnalyzer` — aggregate prediction IC/IR

Located at
[src/core/signal_analyzer.py:58](src/core/signal_analyzer.py:58):

```python
class SignalAnalyzer:
    @classmethod
    def analyze(
        cls,
        predictions: Any,                       # pd.Series with (datetime, instrument) MultiIndex
        config: SignalAnalysisConfig | None = None,
    ) -> SignalAnalysisResult: ...
```

`SignalAnalysisConfig` (frozen dataclass):
- `forward_periods: tuple[int, ...] = (1, 5, 10, 20)`
- `ic_method: str = "rank"`  ("rank" / "normal")
- `compute_turnover: bool = True`
- `topk: int = 50`

**Input contract**: `predictions` MUST be a `pd.Series` with
`MultiIndex` of names `("datetime", "instrument")` — **order matters**.
The boundary explicitly rejects swapped orders
([signal_analyzer.py:127-137](src/core/signal_analyzer.py:127)).

Note that this is the **opposite** of `PITDataProvider.get_features`'s
return shape (`(instrument, datetime)`). Phase 2's adapter must
`swaplevel()` between the two.

**Forward-return source**: SignalAnalyzer currently fetches via
`qlib.data.D.features(instruments, ["$close"], ...)` directly
([signal_analyzer.py:223-229](src/core/signal_analyzer.py:223)).
**This bypasses PIT.** Unlike `FactorAnalyzer`, it has no
`pit_provider` opt-in yet.

**Reusable for factor mining?** Yes — if we feed it factor values as
the "predictions" Series. But the bypassing means returns would be
contaminated. Two options for Phase 2:

1. Submit a small `SignalAnalyzer` patch adding a `pit_provider`
   parameter mirroring the FactorAnalyzer Phase D.1 wiring (would be
   its own OpenSpec change).
2. Do not use SignalAnalyzer; let Phase 2's `evaluator.py` compute IC
   itself using `src.core._ic_utils.compute_ic_for_group` — the same
   primitive both analyzers already share.

Recommendation: option 2 for Phase 2. Factor mining's API needs are
simpler than SignalAnalyzer's (no model-level decay curve, no
turnover at the analyzer level — turnover is part of fitness). The
shared primitive is small enough to call directly.

### B.2 `FactorAnalyzer` — per-factor IC against Alpha158

Located at
[src/core/factor_analyzer.py:79](src/core/factor_analyzer.py:79):

```python
class FactorAnalyzer:
    @classmethod
    def analyze(
        cls,
        config: FactorAnalysisConfig | None = None,
        *,
        dataset: Any | None = None,
        pit_provider: Any | None = None,
    ) -> FactorAnalysisResult: ...
```

`FactorAnalysisConfig`:
- `instruments: str = "csi300"`
- `feature_handler: str = "Alpha158"`
- `test_start: str = "2025-07-01"`; `test_end: str = "2025-12-31"`
- `forward_period: int = 5`; `ic_method: str = "rank"`
- `max_decay_lag: int = 20`; `top_n_factors: int = 20`

**Has `pit_provider` opt-in** ([factor_analyzer.py:88-110](src/core/factor_analyzer.py:88)) —
Phase D.1 wired it in 3a52610. When supplied, close-price fetch
routes through `pit_provider.get_features` instead of direct qlib.

**Reusable for factor mining?** Marginally. The class is bound to the
`DatasetH + col_set="feature"` contract — it expects a qlib dataset
whose feature columns are already materialised. For GP-mined factors,
each candidate's values are produced on the fly by the operator
engine, not via a registered handler. Wedging GP outputs into a
`DatasetH` is more friction than just sharing the underlying IC math.

### B.3 Shared IC primitive

Both analyzers depend on
[src/core/_ic_utils.py](src/core/_ic_utils.py):

- `MIN_IC_OBSERVATIONS_PER_LAG` — minimum (factor, fwd_return)
  observations per lag before the IC is trusted.
- `compute_ic_for_group(group, method)` — per-day cross-sectional IC.

**Phase 2 `evaluator.py` should import `compute_ic_for_group` directly**
from `src.core._ic_utils` rather than going through either analyzer.
This is the minimal, lowest-friction path that still respects the
"reuse existing IC math" rule from `factor_mining_claude_code_design.md`
§3.2.

### B.4 IR convention (carry-over for fitness)

Both analyzers use the same IR rule, which factor mining MUST match
to keep fitness numbers comparable across the codebase:

- IR = `mean_IC / std_IC` only when `std_IC > 0` (or > 1e-9).
- Otherwise IR is **NaN**, not 0.0 — a zero would tell Optuna /
  walk-forward "flat zero-IR model" which is structurally
  indistinguishable from a genuinely mediocre one. See
  [signal_analyzer.py:164-170](src/core/signal_analyzer.py:164) and
  [factor_analyzer.py:441-450](src/core/factor_analyzer.py:441).

---

## C. Pipeline Integration Points

### C.1 `PipelineConfig` feature-handler seam

Located at [src/core/pipeline.py:69](src/core/pipeline.py:69):

```python
@dataclass(frozen=True)
class PipelineConfig:
    provider_uri: str
    region: str = "cn"
    instruments: str = "csi300"
    feature_handler: str = "Alpha158"     # ← the seam
    train_start: str = "2022-01-01"
    train_end:   str = "2024-12-31"
    valid_start: str = "2025-01-01"
    valid_end:   str = "2025-06-30"
    test_start:  str = "2025-07-01"
    test_end:    str = "2025-12-31"
    # ... model, backtest, etc.
```

A `MinedFactor` handler is selected by setting `feature_handler:
"MinedFactor"` in the training config. The string flows into
`FeatureDatasetConfig.feature_handler` and from there into
`FeatureDatasetBuilder.build(...)`.

### C.2 Feature-handler registry

Located at
[src/data/feature_dataset_builder.py:28-30](src/data/feature_dataset_builder.py:28):

```python
FeatureHandlerFactory = Callable[["FeatureDatasetConfig"], Any]
_FEATURE_HANDLER_REGISTRY: dict[str, FeatureHandlerFactory] = {}

def register_feature_handler(
    name: str,
    factory: FeatureHandlerFactory,
    *,
    replace: bool = False,
) -> None: ...
```

Default registration (module-load time):

```python
register_feature_handler("Alpha158", _alpha158_factory)
```

where `_alpha158_factory(config)` returns a `qlib.contrib.data.handler.Alpha158`
instance.

**MinedFactorHandler slot** (Phase 5): provide a factory function
matching the `FeatureHandlerFactory` callable, registered as
`"MinedFactor"` (or similar). The factory takes a `FeatureDatasetConfig`
and returns a qlib-compatible handler whose features come from the
mined factor library parquet rather than from qlib's
`Alpha158`. The training pipeline never imports `research.factor_lab`
directly — only the handler does, when it loads the pool.

### C.3 PIT alignment guard (already wired)

`FeatureDatasetBuilder.build(..., pit_provider=...)` is the **Phase
D.2 PIT guard**: when a PIT provider is passed, the builder asserts
that the canonical qlib `provider_uri` matches `pit_provider._provider_uri`
and raises if they differ. This catches the footgun of calling
`init_qlib_canonical(provider_uri=legacy_dir)` while passing a
PIT-corrected provider — which would silently train on legacy
survivorship-biased bins.

This is **assertion-only**, not a swap; the dataset is still built
via qlib's `DatasetH`. The mined-factor handler will inherit this
guard for free.

### C.4 Spec governance — `v2-feature-handler-registry`

[openspec/specs/v2-feature-handler-registry/spec.md](openspec/specs/v2-feature-handler-registry/spec.md)
already governs this seam. Two relevant requirements:

> **Feature handler registration SHALL remain explicit.** The system
> SHALL NOT import arbitrary handler classes from user config strings.
> Only factories registered through the registry boundary SHALL be
> accepted.

> **Unknown handler is requested → validation raises `FeatureDatasetError`**
> with a list of registered handler names.

Phase 5's OpenSpec change can either be additive (no spec edit
needed beyond MinedFactor's own registration) or MODIFY this
capability if the registration flow needs a new contract. The design
suggests the former.

---

## D. Conventions (from AGENTS.md and pyproject.toml)

### D.1 Whole-file diff verification (verbatim)

AGENTS.md does not use numbered rules; the design doc's "rule #10
whole-file diff" maps to AGENTS.md's
**"Mechanical-move PRs require pre/post diff verification"** block
([AGENTS.md:93-99](AGENTS.md:93)):

> Mechanical-move PRs require pre/post diff verification
> A "split this file into a sub-package", "rename this module", or
> "extract this helper into its own file" task has the explicit goal
> of zero behavior change. For these PRs a green test suite is
> necessary but not sufficient — tests cover the properties tests
> assert on, not all behavior. Lost WARNING logs, dropped keyword-
> only markers, swapped parameter order, compressed or rewritten
> docstrings, lost class decorators (e.g. @dataclass(frozen=True)),
> and quietly-added except Exception catch-alls all pass an
> unchanged test suite while violating "no behavior change". Before
> opening the PR, run a whole-file content diff that filters out only
> trivial lines (imports, blanks, pure docstring rows) so every
> functional line — including @decorator lines that sit *above*
> class headers — is compared:
>     git show <pre-move-sha>:<old-path> > /tmp/pre.py
>     diff <(grep -vE '^(\s*$|\s*#|\s*"""|^import |^from )' /tmp/pre.py | sort) \
>          <(cat <new-path-1> <new-path-2> ... | \
>             grep -vE '^(\s*$|\s*#|\s*"""|^import |^from )' | sort)

Factor mining is **net-new code**, not a mechanical move, so this
rule is unlikely to bite Phase 1–3. It will matter in any future PR
that reorganises `research/factor_lab/`.

### D.2 Other always-on rules (from AGENTS.md)

- **No silent fallback** — failures must raise, not return `{}` or
  `None`. Factor mining's evaluator must raise on missing data, not
  silently produce an empty IC.
- **One logically coherent change per PR.** Phases 1–6 each become
  their own PR.
- **Before claiming done**: run `pytest tests/logic/ tests/governance/`
  and `python -c "import <module>"` for every source module touched.
- **Refactors must achieve their stated goal** — adding a second
  source of truth without removing the first is rejected.
- **Two engines, one schema** — `pipeline_report.json` and
  `walk_forward_report.json` share field names. Any factor-mining
  artifact that piggybacks on these must keep schemas symmetric.

### D.3 Layer boundaries

- `src/core/` — canonical runtime contracts and approved runtime
  logic only.
- `src/data/` — data access; no hidden selection semantics.
- `src/contracts/` — schemas, source-of-truth rules, provenance,
  validation boundaries.
- `research/` and `research/factor_lab/` — research-only,
  non-production, non-canonical.
- `tests/logic/` — runtime and placeholder behavior tests.
- `tests/governance/` — contract, boundary, and regression tests.

**Implication for factor mining**: per the design doc, code lives
under `src/factor_mining/` not `research/factor_lab/`. This is a
**deliberate divergence from the V1 v2-project-skeleton spec** —
`research/factor_lab/` remains the placeholder it was, and factor
mining lives in `src/factor_mining/` because the design wants the
operator/expression/grammar code to be importable from production
training paths via the feature-handler registry. The Phase 1 spec
will need to acknowledge this in writing.

### D.4 Test layout convention

- `tests/logic/<module-mirror-path>/` — runtime/behavior tests.
- `tests/governance/` — boundary/contract regressions.
- E2E tests guarded by `RUN_E2E=1` env var (per CI policy and author
  memory).

Phase 1 tests live at `tests/logic/factor_mining/` (mirroring `src/factor_mining/`).
Boundary tests (e.g. "src does not import research.factor_lab")
live at `tests/governance/`.

### D.5 Linter / formatter / Python

[pyproject.toml](pyproject.toml):

- Python: `>=3.10,<3.13`
- Linter: `ruff` (line-length 120, target `py310`, lint selectors
  `E F W I B UP`, ignores `E501`).
- Type-checker: `mypy` (`strict = false`,
  `warn_unused_ignores = true`, `ignore_missing_imports = true`,
  `python_version = "3.10"`).
- Tests: `pytest` with `testpaths = ["tests"]`, `pythonpath = ["."]`.
- Key pinned deps: `numpy>=1.24,<2.0`, `pandas>=2.0,<2.3`,
  `pyarrow>=14`.
- `qlib` is **not in pyproject** — installed manually from a local
  source checkout (`pip install -e D:/Qlib/qlib`) per
  [docs/qlib-pin.md](docs/qlib-pin.md). CI uses a pinned git commit.

### D.6 OpenSpec convention

- Active changes: `openspec/changes/<change-id>/` containing
  `proposal.md`, `design.md`, `tasks.md`,
  `specs/<capability>/spec.md`, and optionally `.openspec.yaml`
  (`schema: spec-driven\ncreated: YYYY-MM-DD`).
- Archived specs: `openspec/specs/<capability>/spec.md`.
- Spec deltas use `## ADDED Requirements` / `## MODIFIED Requirements` /
  `## REMOVED Requirements` headers, with `### Requirement: <Title>`
  bodies whose **first line MUST contain `SHALL` or `MUST`** (the
  validator parses only the first line for the keyword — empirically
  confirmed during a prior failed `openspec validate --strict`).
- Each requirement needs ≥1 `#### Scenario:` block with WHEN/THEN bullets.
- Workflow guide: [docs/codex/openspec-loop.md](docs/codex/openspec-loop.md)
  (the `/opsx:propose` / `/opsx:apply` / `/opsx:archive` commands the
  design doc references). The `/opsx:` slash commands are not
  Claude Code skills in this repo — they are a Codex / OpenSpec
  workflow described in that doc.
- Validation: `openspec validate <change-id> --strict` (CLI v1.2.0
  installed at `npm i -g openspec`).

---

## E. Data Reality

### E.1 Features available in the PIT provider

From [src/data/pit/qlib_bin_builder.py:80-82](src/data/pit/qlib_bin_builder.py:80):

```python
BIN_FEATURE_FIELDS: tuple[str, ...] = (
    "open", "high", "low", "close", "volume", "money",
)
```

**Six fields**, not eight. Available as qlib expressions:

- `$open`, `$high`, `$low`, `$close`
- `$volume` (in **shares** — Tushare's `vol` × 100 from 手/lots)
- `$money` (in **yuan** — Tushare's `amount` × 1000 from 千元)

**NOT present**:
- `$vwap` — not in bins. Computable as `$money / $volume` per day in
  qlib expression land if needed.
- `$turn` — turnover rate, not in bins. Would need either a separate
  Tushare ingest (`daily_basic.turnover_rate`) or in-bin extension.
- `$amount` (as a distinct field name) — `$money` is the same
  quantity under a different name. The grammar should expose
  whichever name matches the bin.

### E.2 Date coverage and instrument counts

**Probed against the legacy bundle on 2026-05-22** (PIT bundle not
yet on this machine — see §F.3). The PIT bundle, when built, will
have the same `all` instrument set but with NaN-after-delist masking
applied to delisted entries; date range will match unless the operator
chose a different ingest window.

| Property | Value | Notes |
|----------|-------|-------|
| Calendar | **1990-12-19 → 2026-03-06** | 9574 trading days |
| `all` universe (union over time) | **5492 tickers** | full A-share universe |
| `all` universe (active 2026-03-06) | **5478 tickers** | snapshot of currently-tradable |
| `csi300` (union over time) | **591 tickers** | membership rotated ~2× over the calendar |
| `csi500` | **NOT AVAILABLE** | `instruments/csi500.txt` is missing from this bundle. Phase 2 universe choices are `all` or `csi300` only unless the operator generates csi500 membership separately (see [src/data/pit/index_membership.py](src/data/pit/index_membership.py)). |
| Field availability (probed: `$open $high $low $close $volume $money`) | all 6 present | `$money` is recognised by qlib against this bundle too — the field name is consistent between legacy and PIT bins per the qlib_bin_builder contract |

**Memory budget guidance for Phase 2 / Phase 3 config defaults**:
- `csi300` × ~2000 days × 6 fields × float32 ≈ **~30 MB per field-fetch panel** — comfortable for the 256-entry LRU cache (~7.5 GB worst case but in practice much less because field-sets are frozenset-keyed).
- `all` × ~2000 days × 6 fields × float32 ≈ **~250 MB per panel** — `csi300` is the safer default for the GP-search smoke config; `all` should be reserved for production mining runs.

**Reproducing the probe** (the actual script that produced these
numbers; uses `qlib.init` directly because this is a one-off
diagnostic, not production code):

```python
import qlib, contextlib, os
with contextlib.redirect_stdout(open(os.devnull, "w")):
    qlib.init(provider_uri="D:/qlib_data/my_cn_data", region="cn")
from qlib.data import D
cal = D.calendar(freq="day")
print(cal.min(), cal.max(), len(cal))
print(len(D.list_instruments(D.instruments("all"), as_list=True)))
print(len(D.list_instruments(D.instruments("csi300"), as_list=True)))
```

When the PIT bundle is on disk, **re-run the probe against the PIT
`provider_uri`** and update this table. The expected change is:
later `cal.min()` (PIT may not extend as deep into the 1990s), and
`all` count unchanged but with delisted tickers carrying NaN past
their `delist_date`.

### E.3 Universe semantics

Per [src/pit/query.py:16-19](src/pit/query.py:16):

> `get_universe(date)` never returns a ticker whose `list_date > date`
> or whose `delist_date < date`. The `delist_date` itself IS included
> — it is the last valid trading day per Phase B's bin contract.

Index-membership universes (`csi300`, `csi500`, etc.) are produced
by [src/data/pit/index_membership.py](src/data/pit/index_membership.py).
The latest commit on `main` (PR #109) fixed CSI300 membership against
Tushare. The exact universe-name list available depends on which
membership files have been built — `"all"` and `"csi300"` are the
documented defaults.

---

## F. Open Questions / Surprises (contradictions with the design docs)

The four design docs in `docs/factor_mining/` are largely consistent
with the repo, but the following deviations need decisions before
Phase 1 / Phase 2.

### F.1 Feature universe is 6, not 8 (D3 must update)

`decisions.md` D3 commits to 8 fields:
`$open $high $low $close $volume $vwap $amount $turn`. The PIT bins
contain only 6. D3 already flagged this as Phase-0-dependent; the
finalized list is:

| Field | In PIT? | Notes |
|-------|---------|-------|
| `$open`, `$high`, `$low`, `$close` | ✅ | Pre-adjusted by Tushare snapshot adj_factor |
| `$volume` | ✅ | In shares |
| `$money` | ✅ | In yuan; this is the "amount" field under a different name |
| `$vwap` | ❌ | Derivable as `$money / $volume` |
| `$amount` | ❌ name | Same quantity as `$money`; can alias or just rename |
| `$turn` | ❌ | Not in bins; would need extra ingest |

**Recommendation**: v1 feature universe = `$open $high $low $close
$volume $money`, with `$vwap` available as a *derived* expression if
fitness gains warrant. `$turn` is deferred to v2 pending separate
ingest. Update `decisions.md` D3 to lock this in before Phase 2.

### F.2 Adjusted-price contract is stronger than the design assumed

The bin stores PRE-ADJUSTED prices using an **as-of-today** adj_factor
snapshot. Absolute adjusted prices are explicitly **not PIT-correct
features**. The Phase 1 grammar MUST reject expressions whose value
is sensitive to a single ticker's static rescaling (any expression
that consumes `$close` other than via within-ticker ratios /
returns / differences). This is a stronger constraint than
"root must be T_CSF" alone.

**Recommendation**: add a "scale-invariance check" to the grammar
type system in Phase 1, OR enumerate the safe combinator patterns and
reject everything else. The simplest enforcement is: terminals
`$open/$high/$low/$close` may only appear in subtrees that produce a
ratio (`a / b`), a delta (`a - b`), or a ts-relative ratio
(`a / Ref(a, n)`). The Phase 1 spec needs to formalise this.

### F.3 PIT provider URI not pinned anywhere in code (and PIT bundle is not yet on disk on this machine)

The design doc and `decisions.md` action items reference
`D:/qlib_data/my_cn_data_pit`, but this path is not declared in any
config file. `config.yaml` still points at the legacy
`D:/qlib_data/my_cn_data`. The Phase 0 probe (§E.2) confirmed:

```
D:/qlib_data/
  AstockCSV/  cn_data/  import_000300/  my_cn_data/
  my_cn_data_bak_000300/  qlib_csv/
```

**There is no `my_cn_data_pit/` directory on this machine.** The PIT
bundle has not yet been built locally. Before Phase 2 can run, the
operator must:

1. Run [src/data/pit/qlib_bin_builder.py](src/data/pit/qlib_bin_builder.py)
   end-to-end (Tushare daily + adj_factor + delisted registry →
   PIT-corrected qlib bin output dir).
2. Run the related delisted-registry build script if not already done.
3. Note the output dir path; this becomes `pit_provider_uri` in the
   Phase 2 default config.

Three Phase-2 implications:

- Phase 2's `default.yaml` introduces `pit_provider_uri` and
  `delisted_registry_path` fields pointing at the operator's chosen
  PIT output.
- The PIT alignment guard at
  [feature_dataset_builder.py:215](src/data/feature_dataset_builder.py:215)
  catches any mismatch between qlib's canonical `provider_uri` and
  the PITDataProvider's — fail-loud, not fail-silent.
- Phase 2 cannot be **smoke-tested end-to-end** until the PIT bundle
  is built. Phase 1 (operators / expression / grammar — pure Python,
  no data) is unaffected.

**Phase 0 status**: documented. Phase 1 can proceed without the PIT
bundle. Phase 2 must wait for it.

### F.4 Index shape mismatch between PIT and SignalAnalyzer

`PITDataProvider.get_features` returns `(instrument, datetime)`
MultiIndex order; `SignalAnalyzer.analyze` requires
`(datetime, instrument)` order at the boundary. Phase 2's
`pit_adapter.py` must `swaplevel()` on the way out — this is a
one-liner but easy to forget. **Recommendation**: bake this into the
adapter so no downstream factor-mining code touches the index order
itself.

### F.5 SignalAnalyzer has no `pit_provider` opt-in (yet)

FactorAnalyzer was wired in Phase D.1 (3a52610). SignalAnalyzer's
`_fetch_returns` still calls `qlib.data.D.features(...)` directly.
Factor mining doesn't need SignalAnalyzer (per §B.1 recommendation),
but if a future phase wants to reuse it, the same Phase D.1 wiring
should be applied — that would be its own OpenSpec change in
`v2-...` land, **not** part of factor mining.

### F.6 Factor mining lives in `src/factor_mining/`, not `research/factor_lab/`

The four design docs split. `factor_mining_claude_code_design.md`
§3.1 places factor mining under **`src/factor_mining/`** (so the
operator and expression code is importable from production training
paths via the feature-handler registry). The old `factor_mining_design.md`
§2.2 also says `src/factor_mining/`. But the existing OpenSpec
capability `v2-project-skeleton-boundaries` and the
`research/factor_lab/README.md` placeholder were written assuming
factor research code would live under `research/factor_lab/`.

The design's chosen path is `src/factor_mining/`. **Phase 1's
OpenSpec change should MODIFY `v2-project-skeleton-boundaries`** to
acknowledge that:
- `research/factor_lab/` remains a research-only, non-production
  placeholder (no behavior change to that directory).
- `src/factor_mining/` is a **new production-layer module** governed
  by `v2-feature-handler-registry` + a new
  `v2-factor-mining-foundations` capability (or similar).
- Mined-factor library OUTPUT (parquet + manifest) lives under
  `research/mined_factors/` per `decisions.md` D4. That output is
  research artifact — only the handler at registration time imports
  it, and only when a training config opts in via `feature_handler:
  "MinedFactor"`.

This is the single biggest spec drift introduced by adopting the
v2 design doc, and Phase 1's proposal must state it explicitly.

### F.7 The four design docs disagree on operator counts

- `factor_mining_design.md` §4.1 lists ~22 operators.
- `factor_mining_phase1_preflight.md` §6 acceptance criteria also says
  "All 22 operators".
- `factor_mining_claude_code_design.md` §5.1 lists a slightly
  different set with the PIT-gap NaN behaviour required on `ts_*`.

The Phase 1 OpenSpec proposal must enumerate **the exact final set**
in `proposal.md`, taking the union scoped down by:
- only operators whose qlib_template is `pit_safe=True`,
- only operators whose CPU implementation respects `min_periods =
  window` (the PIT-gap test from claude_code_design §5.3),
- only operators with `_safe` variants for div / log / sqrt.

### F.8 OpenSpec validator first-line gotcha

Empirically confirmed during a prior throwaway draft in this session:
`openspec validate --strict` checks only the **first line** of a
requirement body for the `SHALL` / `MUST` keyword. A multi-line
requirement whose first wrapped line lacks the keyword (e.g. "The
factor-mining code (operator registry, terminal registry, expression
/ tree, GP engine) SHALL live under ...") fails validation with the
header echoed in the error.

**Mitigation**: every requirement body's first line must contain
`SHALL` or `MUST`. Re-flow long sentences so the keyword sits in the
first line — not a content change, just a wrapping discipline.

### F.9 AGENTS.md has no numbered rules

The design doc's "rule #10 whole-file diff" doesn't map to a numbered
rule. AGENTS.md uses topical headings instead. The closest match is
the "Mechanical-move PRs require pre/post diff verification" block;
quoted verbatim in §D.1 above. The design doc's wording can be
updated in Phase 0 follow-up if you want exact citation alignment.

---

## G. Phase 0 stopping point

This is the inventory. No factor-mining code has been written. No
OpenSpec change has been created. The next user-driven step per the
design doc:

1. Read this document.
2. Decide on the open questions in §F (especially F.1, F.2, F.6).
3. Update `decisions.md` to lock D2/D3 against Phase 0 findings.
4. Optionally extend §E.2 with the date / instrument output of the
   probe script.
5. Sign off and start Phase 1 in a fresh session, per the design's
   §7.1 prompt.

Phase 1's first action will be `/opsx:propose
add-factor-mining-operators` (or the equivalent), not direct code.
