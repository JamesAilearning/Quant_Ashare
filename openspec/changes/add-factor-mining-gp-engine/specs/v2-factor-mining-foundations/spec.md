## MODIFIED Requirements

### Requirement: Phase 1 SHALL NOT access qlib, PIT data, or any data source

Code under `src/factor_mining/` SHALL NOT import from `qlib`, SHALL NOT call `qlib.init`, and SHALL NOT reference `qlib.data.D`. A repository-wide grep for `qlib\.data`, `qlib\.init`, or `from qlib` under `src/factor_mining/` MUST return zero matches. The PIT layer SHALL be reached only through `src/factor_mining/pit_adapter.py`, which is the designated data door; only `pit_adapter.py` MAY import `PITDataProvider` from `src.pit.query`. Other modules under `src/factor_mining/` (including Phase 3's `gp_engine.py` and `miner.py`) SHALL NOT import `src.pit` directly. `miner.py` MAY consume PIT data only via `FactorMiningDataView` instances it constructs around `PITDataProvider` (i.e. the PIT entry point is still `pit_adapter.py`). This requirement extends the original Phase 1 strict gate D5 (from `docs/factor_mining/decisions.md`) to cover Phase 2-3 modules; the qlib direct-import ban remains absolute for the entire subpackage.

#### Scenario: a developer runs the strict data-gate grep
- **WHEN** a developer runs `grep -rn "qlib\.data\|qlib\.init\|from qlib" src/factor_mining/`
- **THEN** the output is empty (zero matches)
- **AND** any non-empty output is treated as a scope violation, not an acceptable exception

#### Scenario: gp_engine.py or miner.py attempts to import src.pit directly
- **WHEN** a module under `src/factor_mining/` other than `pit_adapter.py` adds `from src.pit.query import …` or `import src.pit`
- **THEN** the change is rejected at review
- **AND** the reviewer directs the contributor to route the call through `FactorMiningDataView` in `pit_adapter.py`

#### Scenario: miner.py wraps a PITDataProvider
- **WHEN** a reviewer inspects `src/factor_mining/miner.py`'s PIT-mode branch
- **THEN** the file constructs `FactorMiningDataView` and consumes its `load_panel()` / `forward_return()` outputs
- **AND** the file does NOT import or call `PITDataProvider.get_features` directly

## ADDED Requirements

### Requirement: `GPEngine` SHALL implement tournament selection, elitism, and per-generation hash dedup

`src/factor_mining/gp_engine.py` SHALL export a `GPEngine` class whose constructor accepts a `GPConfig` and a `FitnessConfig`. The engine SHALL perform tournament selection (default `tournament_size=3`) where the highest-fitness candidate among the sampled set is chosen as a parent, SHALL preserve the top `max(1, int(elite_frac * population_size))` individuals into the next generation unchanged ("elitism"), and SHALL deduplicate each generation by the Phase 1 structural hash. If post-dedup the new generation is smaller than `population_size`, the engine SHALL top up by generating fresh random expressions (via `grammar.random_expression`) to maintain a constant population size.

#### Scenario: tournament selection picks the best of the sampled set
- **WHEN** `select(evaluated)` is called with a fixed RNG seed and `tournament_size=3`
- **THEN** the returned expression has the highest fitness among the three sampled
- **AND** ties are broken by insertion order (deterministic)

#### Scenario: elitism preserves top-K
- **WHEN** `next_generation(evaluated)` is called on a sorted-by-fitness `evaluated` list
- **THEN** the top `max(1, int(elite_frac * population_size))` entries appear in the new generation unchanged

#### Scenario: per-generation dedup
- **WHEN** the new generation contains duplicate expression hashes
- **THEN** duplicates are dropped (one representative kept) before evaluation
- **AND** fresh `random_expression` calls top up the population back to `population_size`

### Requirement: Crossover SHALL be type-preserving via subtree exchange

`GPEngine.crossover(parent_a, parent_b)` SHALL perform a subtree exchange: enumerate `parent_a`'s subtree positions, pick one at random, find a subtree in `parent_b` with the same `ExprType` (kind + taint), and produce a new expression by replacing the picked subtree in `parent_a` with the matching subtree from `parent_b`. If `parent_b` contains no subtree of matching type, or if the resulting expression fails the Phase 1 type-check at construction (raising `GrammarError`), `parent_a` SHALL be returned unchanged. Crossover SHALL never produce an expression whose root fails the `(CSF, PURE)` constraint.

#### Scenario: matching-type subtree swap succeeds
- **WHEN** two parents share a `(FLOAT, PURE)` subtree shape and `crossover` is called
- **THEN** the offspring is structurally valid (passes Phase 1 type-check at construction)
- **AND** the offspring's root retains `(CSF, PURE)` type

#### Scenario: no matching-type subtree
- **WHEN** the chosen `parent_a` subtree's type is `(FLOAT, ADJ_TAINTED)` and `parent_b` has no `(FLOAT, ADJ_TAINTED)` subtree
- **THEN** `crossover` returns `parent_a` unchanged

### Requirement: Mutation SHALL provide subtree, point, and constant operators

`GPEngine` SHALL expose three mutation operators: `mutate_subtree(expr)` replaces a random subtree with a fresh `random_expression` of matching type; `mutate_point(expr)` replaces a random `Terminal` with a different `Terminal` of identical `(kind, taint)` (ADJ_TAINTED → another ADJ_TAINTED price feature; PURE → another PURE feature; INT_WINDOW → another window literal); `mutate_const(expr)` replaces a random INT_WINDOW literal with a different element of `WINDOW_LITERALS`. All three mutations SHALL preserve the Phase 1 type contract; any attempt that would produce an ill-typed expression is treated as a no-op (return the original).

#### Scenario: subtree mutation replaces a random node
- **WHEN** `mutate_subtree(expr)` is called and a feasible matching-type subtree is generated
- **THEN** the returned expression differs structurally from `expr` (different hash) OR is the original when the new subtree happens to be identical
- **AND** the returned expression's root type matches `expr`'s root type

#### Scenario: point mutation respects taint
- **WHEN** `mutate_point(expr)` is called on an expression containing `$close` (ADJ_TAINTED)
- **THEN** if `$close` is selected, the replacement is one of `{$open, $high, $low}` (other ADJ_TAINTED features), not `$volume` or `$money`

#### Scenario: const mutation swaps window literals
- **WHEN** `mutate_const(expr)` selects a window literal `Terminal("20")`
- **THEN** the replacement is another window literal from `{5, 10, 40, 60}`

### Requirement: `GPEngine.run` SHALL be deterministic given identical config and seed

For an identical `GPConfig` (including `seed`), `FitnessConfig`, panel, and forward-return inputs, two independent `GPEngine.run` invocations SHALL produce identical results: the same set of `(expr_hash, fitness)` entries across all generations and an identical final `FactorPool`. The engine SHALL flow all randomness through a single seeded `random.Random(config.seed)`; no `numpy.random` global state, no time-based seeds, no dict-iteration assumptions beyond Python 3.7+ insertion-order semantics.

#### Scenario: two identical runs produce identical pools
- **WHEN** `GPEngine(config, fitness).run(panel, fwd)` is called twice with the same inputs
- **THEN** the two final `FactorPool` objects have the same `len()`, the same set of expression hashes, and the same per-hash fitness values (within `1e-12` for floating-point)

### Requirement: Checkpoint save / load SHALL round-trip a GP run

`GPEngine.save_checkpoint(path)` SHALL write a JSON-serialisable representation of the engine state (config, current generation index, RNG state, fitness cache by hash, population, history). `GPEngine.load_checkpoint(path, *, fitness_config)` SHALL reconstruct an engine instance from that file. Running `engine.run(...)` after a load that occurred at generation `k` SHALL produce a final pool entry-identical to the pool produced by a single continuous run, modulo floating-point tolerance (`abs(continuous_fitness - resumed_fitness) < 1e-12` for every shared expression). The factor-values cache (used by the novelty term) does not need to survive serialisation; it is recomputed lazily on demand after resume.

#### Scenario: kill-resume produces the same pool as a continuous run
- **WHEN** an engine is run for `k` generations, checkpointed, reloaded, and run for the remaining `n - k` generations
- **THEN** the final pool is entry-identical (same hash set, same per-hash fitness within `1e-12`) to a single continuous run of `n` generations with the same config and seed

### Requirement: Miner CLI SHALL run end-to-end from a YAML config

`src/factor_mining/miner.py` SHALL be invokable as `python -m src.factor_mining.miner <config.yaml>` and SHALL produce, under `<output_dir>/runs/{run_id}/`: `factor_pool.parquet` and `factor_expressions.json` (the Phase 2 pool format), `gp_history.json` (the per-generation stats), and `config.yaml` (the parsed config dumped back). The `run_id` SHALL be either taken from the config or autogenerated. The CLI SHALL accept both `data.mode: synthetic` (in-process deterministic panel) and `data.mode: pit` (real PIT bundle via `FactorMiningDataView`); the PIT-mode SHALL raise an explicit error when `pit_provider_uri` or `delisted_registry_path` is an empty string per `config/factor_mining/default.yaml`'s placeholder convention.

#### Scenario: running the smoke config end-to-end
- **WHEN** a developer runs `python -m src.factor_mining.miner config/factor_mining/smoke.yaml`
- **THEN** the process exits 0
- **AND** the four output files exist under `<output_dir>/runs/{run_id}/`
- **AND** the same invocation with an unchanged config produces a pool with the same entries (determinism)

#### Scenario: pit-mode rejects empty URI
- **WHEN** the miner is invoked with `data.mode: pit` and `data.pit_provider_uri: ""`
- **THEN** an explicit error is raised naming `inventory.md` §F.3 (the "build the PIT bundle" follow-up task)
- **AND** no factor-pool files are written

### Requirement: Smoke config SHALL exist and complete quickly on CPU

`config/factor_mining/smoke.yaml` SHALL exist after Phase 3 and SHALL configure a small synthetic-data run (target wall-clock < 30 s on a modest CPU). Running `python -m src.factor_mining.miner config/factor_mining/smoke.yaml` is the canonical smoke test of the Phase 3 surface.

#### Scenario: a developer wants to verify the GP engine works
- **WHEN** a developer runs the smoke command
- **THEN** the run completes in under 30 s on a typical developer laptop
- **AND** the produced pool contains at least one entry (some expression passes validity)
