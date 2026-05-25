## ADDED Requirements

### Requirement: YAML config loader SHALL expand `${VAR}` and `${VAR:-default}` environment-variable references in string values

`src/core/_yaml_loader.py::load_yaml_with_inheritance` SHALL, after merging any `extends` chain into a single dict, walk the resulting tree and rewrite every string scalar value by substituting environment-variable references of the form `${VAR_NAME}` (variable name only) and `${VAR_NAME:-default_text}` (POSIX-shell default syntax). The substitution SHALL ONLY apply to string-typed values; dict keys, integers, floats, booleans, `None`, and nested mappings/lists pass through their non-string structure unchanged (the walker recurses into them, but does not rewrite the container itself). Multiple references in one string SHALL each be expanded (`"${A}/${B}"` substitutes both). Literal substrings around the reference SHALL be preserved (`"prefix-${VAR}-suffix"` keeps the bracketing text). An unresolved reference (the environment variable is unset AND the YAML used the bare `${VAR}` form, not `${VAR:-default}`) SHALL raise a typed `YamlEnvVarError` whose message names BOTH the unresolved variable name AND the YAML file path that referenced it, so the operator immediately knows which config and which placeholder caused the failure. Existing YAML files that contain no `${...}` references SHALL load with byte-for-byte identical post-merge dicts before and after this change (the new expansion is purely additive on top of the existing parse).

#### Scenario: bare `${VAR}` reference resolves from the environment

- **WHEN** a YAML file declares `provider_uri: "${QLIB_PROVIDER_URI}"` and `QLIB_PROVIDER_URI=/data/bundle` is set in the process environment
- **THEN** `load_yaml_with_inheritance` returns a dict whose `provider_uri` key has value `"/data/bundle"`

#### Scenario: bare `${VAR}` reference with the variable unset raises a typed error

- **WHEN** a YAML file at path `config_walk.yaml` declares `provider_uri: "${QLIB_PROVIDER_URI}"` and `QLIB_PROVIDER_URI` is NOT set in the process environment
- **THEN** `load_yaml_with_inheritance` raises `YamlEnvVarError`
- **AND** the exception message mentions the variable name `"QLIB_PROVIDER_URI"`
- **AND** the exception message mentions the source file path `"config_walk.yaml"`

#### Scenario: `${VAR:-default}` falls back when the variable is unset

- **WHEN** a YAML file declares `provider_uri: "${QLIB_PROVIDER_URI:-D:/qlib_data/my_cn_data}"` and `QLIB_PROVIDER_URI` is NOT set
- **THEN** `load_yaml_with_inheritance` returns a dict whose `provider_uri` key has value `"D:/qlib_data/my_cn_data"`

#### Scenario: `${VAR:-default}` prefers the environment value when set

- **WHEN** a YAML file declares `provider_uri: "${QLIB_PROVIDER_URI:-D:/qlib_data/my_cn_data}"` and `QLIB_PROVIDER_URI=/override` is set
- **THEN** the returned `provider_uri` is `"/override"` (the default is not used)

#### Scenario: env vars inside a longer string are expanded in place

- **WHEN** a YAML value is `"${BUNDLE_ROOT}/csi300/${VINTAGE}"` with `BUNDLE_ROOT=/data` and `VINTAGE=2026-03-06`
- **THEN** the substituted value is `"/data/csi300/2026-03-06"`

#### Scenario: non-string YAML scalars and dict keys are NOT touched

- **WHEN** a YAML file contains an integer value (`num_boost_round: 1000`), a boolean (`run_attribution: true`), and a string key (the key is a Python `str`, but it's a key, not a value)
- **THEN** none of these are passed through the env-var expander
- **AND** their post-load Python types and values match a plain `yaml.safe_load` parse

#### Scenario: literal-path YAML still loads byte-for-byte identically

- **WHEN** a YAML file (e.g. the existing `config.yaml`) contains only literal string values with NO `${...}` references
- **THEN** `load_yaml_with_inheritance` returns the same dict it would have returned before this requirement was implemented (regression for "we did not silently change parsing of existing configs")

### Requirement: Walk-forward CLI SHALL validate the configured `overall_end` against the bundle manifest before initializing qlib

`scripts/run_walk_forward.py` SHALL, after loading the YAML config and before invoking `init_qlib_canonical`, call `validate_test_end_against_bundle(provider_uri, wf_config.overall_end)` on the resolved qlib provider URI. The bundle manifest is a JSON file at `Path(provider_uri) / "bundle_manifest.json"` with the schema `{"provider_uri": str, "tail_date": str (ISO YYYY-MM-DD), "instrument_count": int, "built_at": str (ISO datetime)}`. The validator behaviour SHALL be:

- If `QLIB_SKIP_BUNDLE_VALIDATION=1` is set in the environment, log INFO and return immediately without reading the manifest (operator opt-out for tests and one-off bypass).
- If `bundle_manifest.json` is absent, log INFO `"no manifest at <path>, skipping bundle freshness validation"` and return (no manifest = no validation possible; predates the manifest contract).
- If the manifest exists but the JSON is malformed (parse error) or missing a required field, raise `BundleManifestError` naming the malformed file and the problem.
- If the manifest exists and is well-formed:
  - If `overall_end` (parsed as ISO date) `<=` `tail_date`, return without raising (the bundle covers the requested window, possibly with extra trailing data which is fine).
  - If `overall_end > tail_date`, raise `BundleStaleError` whose message names BOTH the requested `overall_end` AND the bundle's `tail_date`, so the operator immediately knows the gap.

The validator's `soft=True` mode SHALL log a WARNING instead of raising on a stale bundle (intended for non-canonical scripts that prefer a warning to an abort); the walk-forward CLI uses the default HARD mode. The validator SHALL run BEFORE `init_qlib_canonical` so a stale-bundle abort happens before qlib reads any data file.

#### Scenario: validator passes when overall_end is before bundle tail_date

- **GIVEN** a bundle directory whose `bundle_manifest.json` declares `"tail_date": "2026-03-06"`
- **WHEN** `validate_test_end_against_bundle(provider_uri, "2026-02-28")` is called
- **THEN** the call returns without raising and without logging a WARNING

#### Scenario: validator hard-fails when overall_end is after bundle tail_date

- **GIVEN** a bundle whose `tail_date` is `"2026-03-06"`
- **WHEN** `validate_test_end_against_bundle(provider_uri, "2026-04-30")` is called with the default `soft=False`
- **THEN** `BundleStaleError` is raised
- **AND** the message names both `"2026-04-30"` (the requested `overall_end`) and `"2026-03-06"` (the bundle tail)

#### Scenario: validator soft-warns when overall_end is after bundle tail_date

- **GIVEN** a bundle whose `tail_date` is `"2026-03-06"`
- **WHEN** `validate_test_end_against_bundle(provider_uri, "2026-04-30", soft=True)` is called
- **THEN** the call returns without raising
- **AND** a WARNING is logged that names both dates

#### Scenario: validator logs INFO and returns when no manifest exists

- **GIVEN** a provider directory that does NOT contain `bundle_manifest.json`
- **WHEN** `validate_test_end_against_bundle(provider_uri, any_date)` is called
- **THEN** the call returns without raising
- **AND** an INFO log records the missing manifest path

#### Scenario: validator raises on malformed manifest JSON

- **GIVEN** a `bundle_manifest.json` whose contents are not parseable JSON
- **WHEN** `validate_test_end_against_bundle(provider_uri, any_date)` is called
- **THEN** `BundleManifestError` is raised naming the malformed file

#### Scenario: `QLIB_SKIP_BUNDLE_VALIDATION=1` bypasses validation entirely

- **GIVEN** a bundle whose `tail_date` is `"2026-03-06"` and `overall_end=2026-04-30` (would normally hard-fail)
- **WHEN** `QLIB_SKIP_BUNDLE_VALIDATION=1` is set and `validate_test_end_against_bundle(provider_uri, "2026-04-30")` is called
- **THEN** the call returns without raising
- **AND** an INFO log records the bypass

#### Scenario: walk-forward CLI runs the validator before qlib init

- **WHEN** `scripts/run_walk_forward.py` processes a YAML whose `overall_end` is after the bundle's `tail_date` and `QLIB_SKIP_BUNDLE_VALIDATION` is unset
- **THEN** `BundleStaleError` is raised before `init_qlib_canonical` is called
- **AND** no qlib data files have been opened (the abort happens at config-validation time, not deep in FeatureDatasetBuilder)
