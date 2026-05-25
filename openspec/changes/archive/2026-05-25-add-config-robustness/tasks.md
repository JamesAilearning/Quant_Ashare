# Tasks: Config-layer robustness (env-var paths + bundle manifest)

## OpenSpec (propose stage)

- [x] Draft proposal.md / tasks.md
- [x] Draft `specs/v2-config-robustness/spec.md` (2 ADDED Requirements)
- [ ] `openspec validate add-config-robustness --strict` — green

## Implementation

### Part A — env-var expansion in YAML loader

- [x] `src/core/_yaml_loader.py::expand_env_vars(value: str) -> str`
      — resolves `${VAR}` and `${VAR:-default}`; raises
      `YamlEnvVarError` on unresolved references
- [x] `src/core/_yaml_loader.py::_expand_env_vars_in_tree(obj, source_path)`
      — walks the parsed dict/list tree; rewrites string scalars
      in place; passes keys and non-string types through
- [x] `load_yaml_with_inheritance` calls the tree walker on the
      merged dict before returning
- [x] `YamlEnvVarError` exposes both the unresolved variable name
      and the source YAML path

### Part B — bundle manifest validation

- [x] `src/data/bundle_manifest.py::BundleManifest` dataclass:
      `{provider_uri, tail_date, instrument_count, built_at}`
- [x] `load_manifest(provider_uri) -> BundleManifest | None`
      — reads `Path(provider_uri) / "bundle_manifest.json"`;
      returns None when missing; raises on malformed JSON
- [x] `validate_test_end_against_bundle(provider_uri, test_end,
      *, soft=False)` — raises `BundleStaleError` when
      `test_end > tail_date`; logs WARNING when `soft=True`;
      logs INFO when no manifest exists
- [x] `BundleStaleError` carries both the requested `test_end`
      and the bundle's `tail_date` for the message
- [x] `QLIB_SKIP_BUNDLE_VALIDATION=1` env var bypasses the
      validator entirely (logs INFO)

### Walk-forward CLI integration

- [x] `scripts/run_walk_forward.py` calls
      `validate_test_end_against_bundle(provider_uri,
      wf_config.overall_end)` after `_load_config` and before
      `init_qlib_canonical`
- [x] The validator runs in HARD mode (soft=False) by default;
      operators that want to override use the env-var opt-out
- [x] Strict-unknown-key check in `_load_config` is unchanged

## Tests

### `tests/logic/test_env_var_expansion.py` — env-var expansion dimensional matrix

- [x] `test_env_var_set_returns_value` — `${VAR}` with VAR set
- [x] `test_env_var_unset_no_default_raises` — `${VAR}` with
      VAR unset, no default → `YamlEnvVarError` mentions VAR
      and source path
- [x] `test_env_var_default_syntax_with_value_set` — `${VAR:-fallback}`
      with VAR set → returns VAR's value (not the default)
- [x] `test_env_var_default_syntax_with_value_unset` — `${VAR:-fallback}`
      with VAR unset → returns `"fallback"`
- [x] `test_env_var_default_syntax_empty_default` — `${VAR:-}`
      with VAR unset → returns `""`
- [x] `test_env_var_nested_in_larger_string` — prefix-${VAR}-suffix
      pattern expands the inner reference and keeps the literal
      bracketing
- [x] `test_env_var_multiple_references_in_one_string` —
      `${A}/${B}` expands both
- [x] `test_env_var_no_substitution_for_keys` — env var in a YAML
      key passes through unchanged (we only rewrite values)
- [x] `test_env_var_no_substitution_for_non_string_scalars` —
      ints, bools, floats, None pass through unchanged
- [x] `test_env_var_literal_path_still_loads` — a YAML with a
      literal `D:/qlib_data/my_cn_data` (no `${...}`) loads
      identically before and after the patch (regression for
      "don't break existing configs")

### `tests/logic/test_bundle_manifest.py` — manifest validation dimensional matrix

- [x] `test_load_manifest_missing_file_returns_none` — no
      `bundle_manifest.json` → `load_manifest` returns None
- [x] `test_load_manifest_well_formed_returns_dataclass` —
      a real JSON file deserialises into `BundleManifest`
- [x] `test_load_manifest_malformed_json_raises` — invalid JSON
      raises `BundleManifestError`
- [x] `test_load_manifest_missing_required_field_raises` — JSON
      missing `tail_date` → `BundleManifestError` names the
      missing field
- [x] `test_validate_test_end_before_tail_passes` — `test_end
      < tail_date` returns without raising
- [x] `test_validate_test_end_equal_to_tail_passes` — boundary
      case: `test_end == tail_date` is OK (inclusive)
- [x] `test_validate_test_end_after_tail_hard_raises` — strict
      mode raises `BundleStaleError` whose message names both
      dates
- [x] `test_validate_test_end_after_tail_soft_logs_warning` —
      soft mode logs WARNING but does NOT raise
- [x] `test_validate_no_manifest_logs_info_and_passes` — no
      manifest → INFO log, no exception
- [x] `test_validate_skip_env_var_bypasses_check` —
      `QLIB_SKIP_BUNDLE_VALIDATION=1` returns immediately with
      INFO log even when manifest says test_end > tail_date

## Validation

- [x] `pytest tests/logic/test_env_var_expansion.py -q` — all green
- [x] `pytest tests/logic/test_bundle_manifest.py -q` — all green
- [x] `pytest tests/logic/ -q` — full suite green
- [x] Manual smoke: literal-path `config.yaml` and
      `config_walk.yaml` still parse identically (no env vars
      involved; only verifies the new path is purely additive)
- [ ] CI green on push (no `--admin` merge)

## Operator follow-up (after this PR merges)

- [ ] Hand-write `bundle_manifest.json` for
      `D:/qlib_data/my_cn_data` so the existing operator machine
      gets validation today instead of INFO-no-manifest
- [ ] Update bundle build / ingest scripts (separate PR) to emit
      `bundle_manifest.json` as a side-effect of any new bundle
      build, so the manifest is never out of sync with the data
- [ ] (Optional) Replace `provider_uri: "D:/qlib_data/my_cn_data"`
      with `provider_uri: "${QLIB_PROVIDER_URI:-D:/qlib_data/my_cn_data}"`
      in the canonical YAMLs once the team agrees on the env
      var name

## Deferred (NOT this proposal)

- Single-fold `main.py` validator wiring (trivial follow-up;
  conceptually separate)
- Bundle-build emitter for `bundle_manifest.json` (separate PR)
- Replacing the literal `provider_uri` in the shipped YAML
  defaults (separate trivial PR; only after the team picks the
  canonical env var name)
- Env-var expansion for *keys*, not just values (no use case;
  expansion in keys breaks the strict-unknown-key rejection
  semantics that `_load_config` relies on)
