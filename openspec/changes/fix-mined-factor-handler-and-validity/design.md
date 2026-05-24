# Design: Fix MinedFactor handler integration bugs and validity sanity-check overlap

## Why this is a hot-fix change rather than part of Phase 6.3

The wiring PR (`add-factor-mining-walk-forward`, archived) shipped
the synthetic-test contract correctly per its spec, but its spec was
itself wrong on the MultiIndex level order, and the synthetic test
fixtures couldn't exercise the real qlib `StaticDataLoader` /
universe-name path. The bugs only show up on real PIT data; that
gives us an "expand test coverage to the things we couldn't cover
before" PR rather than a feature change.

## Root cause analysis

### Bug 1 — `_extreme_outlier_frac` double-counts NaN as outlier

**File:** `src/factor_mining/fitness.py`

**Original code (pre-fix):**

```python
def _extreme_outlier_frac(result, magnitude):
    arr = result.factor_values.to_numpy()
    if arr.size == 0:
        return 0.0
    extreme = ~np.isfinite(arr) | (np.abs(arr) > magnitude)
    return float(extreme.sum()) / float(arr.size)
```

The `~np.isfinite(arr)` term flagged every NaN cell as an "outlier"
and used `arr.size` as the denominator. On a factor with coverage
0.81 (19% NaN), this returns ~0.19, well above the
`extreme_outlier_frac_max = 0.05` default. The sanity check then
fails — even though the magnitudes of the *finite* cells are all
bounded.

The intent of v1 §5.2 was that the sanity check is a *magnitude*
filter (anti-blow-up), distinct from the *coverage* filter
(anti-sparsity). Mixing them double-penalised NaN-heavy factors and
made the binding constraint effectively `coverage ≥ 0.95`, not
`coverage ≥ 0.80`.

**Fix:** denominator is the count of finite cells; numerator counts
only finite cells whose magnitude exceeds the bound. All-NaN factor
returns 0.0 (coverage check binds in that case).

### Bug 2 — `_make_qlib_handler` passes raw "csi300" to DataHandlerLP

**File:** `src/data/mined_factor_handler.py`

**Original code (pre-fix):**

```python
def _make_qlib_handler(features, label, config):
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.data.dataset.loader import StaticDataLoader

    data_dict = {"feature": features}
    if label is not None:
        data_dict["label"] = label
    loader = StaticDataLoader(config=data_dict)
    return DataHandlerLP(
        instruments=config.instruments,  # <-- "csi300" string passes
        start_time=config.train_start,
        end_time=config.test_end,
        data_loader=loader,
    )
```

qlib's `StaticDataLoader.load(instruments, start_time, end_time)` is
implemented (roughly) as:

```python
def load(self, instruments, start_time, end_time):
    df = self._data  # the dict we built
    return df.loc(axis=0)[start_time:end_time, instruments]  # !!
```

It treats `instruments` as a level-1 filter on the MultiIndex, NOT as
a qlib universe name. Passing `"csi300"` (a string) makes pandas try
to find a row whose level-1 value is exactly `"csi300"`; the
DataFrame has no such row, so pandas raises
`KeyError: 'csi300'`.

The Alpha158 handler dodges this because qlib's built-in handler
calls `D.list_instruments(D.instruments(name), ...)` to resolve the
universe internally before the `StaticDataLoader` ever sees the
instrument list. We need to do the same resolution at our handler's
construction site.

**Fix:** add an `isinstance(instruments, str)` check; if true, resolve
via `D.list_instruments(D.instruments(name), start_time=...,
end_time=..., as_list=True)`. List inputs pass through unchanged.

### Bug 3 — `make_mined_factor_features` produces `(instrument, datetime)` MultiIndex

**File:** `src/data/mined_factor_handler.py`

**Original code (pre-fix):**

```python
for entry in sorted_entries:
    result = evaluate_expression(entry.expr, resolved_panel)
    stacked = result.stack(future_stack=True)
    stacked.index = stacked.index.set_names(["instrument", "datetime"])
    columns.append(stacked)
    column_names.append(_column_name_for(entry))

features = pd.concat(columns, axis=1, keys=column_names)
features.columns = column_names
features.index = features.index.set_names(["instrument", "datetime"])
return features
```

`result` is a date × ticker DataFrame; `result.stack()` produces a
Series with MultiIndex `(date_level, ticker_level)`. Setting the
names to `["instrument", "datetime"]` is a *labelling* operation that
doesn't reorder the levels — it just renames level 0 "instrument"
(it's actually the date level) and level 1 "datetime" (it's actually
the ticker level). Downstream qlib code that introspects level names
gets misled.

But the harder failure is the `StaticDataLoader.load` path described
in bug #2: even with correct names but `(instrument, datetime)`
order, `df.loc(axis=0)[:, instruments]` looks up ticker codes in
level 1; if level 1 is the datetime axis, the lookup fails on
`SH600000` as a datetime.

**Fix:** `reorder_levels(["datetime", "instrument"])` on each
per-entry stacked series and on the concatenated `features` DataFrame
before returning. Sort the index after reorder so
`is_monotonic_increasing` holds — `StaticDataLoader` assumes a
sorted index.

## Module surface

```
src/factor_mining/
└── fitness.py                          # MODIFIED — _extreme_outlier_frac

src/data/
└── mined_factor_handler.py             # MODIFIED — make_mined_factor_features + _make_qlib_handler

tests/logic/
├── test_mined_factor_handler.py        # MODIFIED — assert + 3 new tests
└── factor_mining/
    └── test_fitness.py                 # MODIFIED — 3 new sanity tests
```

No edits to `src/factor_mining/evaluator.py`, `src/data/feature_dataset_builder.py`,
`src/factor_mining/factor_pool.py`, `scripts/run_walk_forward.py`,
or any spec other than the two named below.

## Spec deltas

### `v2-mined-factor-handler`

**MODIFIED — "make_mined_factor_features SHALL produce one column per pool entry":**
flip the MultiIndex level order requirement from `(instrument,
datetime)` to `(datetime, instrument)`. The scenario assertion
changes accordingly.

**ADDED — "Handler factory SHALL resolve universe-name strings to
ticker lists":** the factory's `_make_qlib_handler` (or equivalent
construction site) SHALL invoke `D.list_instruments(D.instruments(name),
start_time, end_time, as_list=True)` when `config.instruments` is a
`str`. List inputs are passed through unchanged.

### `v2-factor-mining-foundations`

**MODIFIED — "Validity filters SHALL enforce coverage, variance, and
sanity constraints":** clarify that the sanity check's denominator is
the count of finite cells in `result.factor_values`, not the total
cell count, and that non-finite cells SHALL NOT count as outliers.
The coverage check is the sole arbiter of NaN density; the sanity
check is the magnitude filter on the finite fraction.

## Test plan

### `tests/logic/factor_mining/test_fitness.py` — 3 new tests

```python
def test_sanity_does_not_count_nan_as_outlier():
    """30% NaN + no magnitude outliers must pass the sanity check
    alone (coverage check is separate)."""
    rng = np.random.default_rng(11)
    arr = rng.normal(0, 1, size=(100, 50))
    nan_mask = rng.random(arr.shape) < 0.30
    arr[nan_mask] = np.nan
    df = pd.DataFrame(arr, ...)
    r = _make_result(factor_values=df, coverage=0.70)
    cfg_no_coverage_check = FitnessConfig(
        coverage_min=0.0, variance_days_frac_min=0.0,
    )
    assert passes_validity(r, cfg_no_coverage_check)


def test_sanity_extreme_outliers_in_finite_cells_still_fail():
    """30% NaN + 10% of finite cells set to 1e10 must STILL fail."""
    ...


def test_sanity_all_nan_factor_returns_zero_outlier_frac():
    """All-NaN factor: sanity check returns 0 (coverage binds)."""
    ...
```

### `tests/logic/test_mined_factor_handler.py` — 1 modified + 3 new tests

- **MODIFIED `test_make_features_returns_multiindex_dataframe`:** the
  index-names assertion changes from `["instrument", "datetime"]` to
  `["datetime", "instrument"]`.
- **NEW `test_make_features_index_order_is_datetime_then_instrument`:**
  explicit regression for bug #3 — asserts level-0 values are
  Timestamps, level-1 are strings, and the index is
  monotonically increasing.
- **NEW `test_factory_resolves_universe_name_to_ticker_list`:** stubs
  qlib via `monkeypatch.setitem(sys.modules, "qlib.data", ...)` etc.,
  invokes `_make_qlib_handler` with `instruments="csi300"`, and
  asserts the handler ended up with the resolved ticker list (not the
  raw "csi300" string).
- **NEW `test_factory_passes_through_explicit_ticker_list`:** invokes
  `_make_qlib_handler` with `instruments=["SH600519", "SH600036"]`
  (list, not str) and asserts `D.list_instruments` was NOT called.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Changing the MultiIndex order is a breaking change for any downstream consumer of `make_mined_factor_features`'s output | The only consumer is `_make_qlib_handler` (same file) and the synthetic-test fixtures. No external consumer ships against the old contract — Phase 5/6 explicitly documented the handler as "not for direct external consumption" |
| The `D.list_instruments` resolution adds a qlib runtime dependency at handler-build time | The qlib import was already inside `_make_qlib_handler` (lazy); this PR adds a `D.list_instruments` call alongside the existing `StaticDataLoader` / `DataHandlerLP` calls — no new top-level imports, no new module-load-time qlib pull |
| The sanity-check change could let through a factor that the old check would have rejected | Yes — that's the point. The old check was over-rejecting due to the NaN double-count. The variance check + the coverage check still bound; the test `test_sanity_extreme_outliers_in_finite_cells_still_fail` proves the magnitude check still works on the finite fraction |
| Mocking qlib in the universe-name test could mask a real qlib API drift | The mock asserts the exact API surface used (`D.instruments(name)` → spec object, `D.list_instruments(spec, start_time, end_time, as_list=True)` → list). If qlib changes the signature, this test would need updating alongside any real call site — same maintenance cost as the real call site |

## Why no proposal for re-tuning GP defaults

The bake-off result (`mean_information_ratio = -2.41`, threshold-not-met)
was on `pop=30 gen=5` — a smoke-test config picked to keep the loop
under 5 minutes for the first end-to-end run. Real production GP
parameters (pop≥200, gen≥30, novelty pressure≥0.5) are a separate
config-tuning PR that depends on this hot-fix landing first
(otherwise the larger run will hit the same three bugs). That PR
is gated on operator-controlled walltime budget and is out of scope
here.

## Backward compatibility

- Existing `make_mined_factor_features` callers in production code:
  none (the function is internal to `src/data/mined_factor_handler.py`
  per Phase 5).
- Existing `_make_qlib_handler` callers in production code: none (also
  internal).
- Existing synthetic-test fixtures: one assertion changes
  (`["instrument", "datetime"]` → `["datetime", "instrument"]`) which
  is done in the same PR.
- Existing `passes_validity` / `compute_fitness` callers: same
  signature, same return type; the only behaviour change is that a
  NaN-heavy-but-magnitude-OK factor now passes sanity (it may still
  fail coverage — and most such factors do, per the v1 §5.2 default).
