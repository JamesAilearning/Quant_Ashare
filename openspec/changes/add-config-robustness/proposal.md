# Make the config layer robust to machine-local paths and stale bundle dates

## Why

The 10-PR audit fix-up plan flagged two separate config-layer
foot-guns that have bitten operators repeatedly:

1. **Hardcoded machine-local `provider_uri`.** `config.yaml:5` and
   every `config_walk*.yaml` ship with
   `provider_uri: "D:/qlib_data/my_cn_data"` baked in. The only way
   to run the pipeline on a different machine (CI runner, a teammate's
   laptop, a colocated Linux box, anywhere) is to edit the YAML
   in-place — which then dirties the working tree, breaks
   side-by-side `config_walk*.yaml` comparisons, and makes
   `git diff` noisy with one-machine-only edits. Operators have
   resorted to keeping local-only YAML branches; the audit caught
   one such branch silently overriding `output_dir` along the way.

2. **"Last verified bundle tail: 2026-03-06" lives in prose
   comments.** Three YAML files document the bundle tail this way
   (`config.yaml:16`, `config_walk.yaml:12`, plus the per-N sweep
   configs that inherit it). The strings drift unsynced — there is
   no programmatic check that `test_end <= bundle.tail_date`. A run
   whose dates fall after the bundle's coverage fails deep inside
   `FeatureDatasetBuilder` with an opaque "empty dataset" message
   instead of a clean upfront "your bundle ends 2026-03-06 but you
   asked for test_end=2026-04-30" rejection. Two of the audit's
   confused-operator incidents were this exact failure mode.

This change ships both fixes with the smallest possible surface area.

## What Changes

### Part A — env-var expansion in YAML loader

`src/core/_yaml_loader.py` gains an `expand_env_vars(value: str) -> str`
helper that resolves `${VAR_NAME}` and `${VAR_NAME:-default}` syntax
(the same surface as POSIX shell parameter expansion). The loader
walks the parsed YAML tree and rewrites string scalars in place;
keys and non-string types pass through unchanged. Unresolved
references (env var truly missing AND no default supplied) raise
`YamlEnvVarError` with both the variable name and the YAML file
that referenced it, so the operator immediately knows which
config and which `${VAR}` failed. Literal paths still load
identically — the existing `config.yaml` does not change.

### Part B — bundle manifest validation

New module `src/data/bundle_manifest.py`:

- `BundleManifest` dataclass: `{provider_uri, tail_date,
  instrument_count, built_at}`.
- `load_manifest(provider_uri)` reads
  `Path(provider_uri) / "bundle_manifest.json"`; returns `None` when
  the file is absent (no manifest = no validation possible, the
  ingest script that built this bundle predates the manifest
  contract).
- `validate_test_end_against_bundle(provider_uri, test_end, *,
  soft=False)` raises `BundleStaleError` when `test_end >
  manifest.tail_date`; logs WARNING when `soft=True`; logs INFO
  when no manifest exists.
- Opt-out via env var `QLIB_SKIP_BUNDLE_VALIDATION=1` for tests
  that need to bypass the check (e.g. when a fixture bundle has no
  manifest and the test verifies a downstream component, not the
  validator itself).

The walk-forward CLI (`scripts/run_walk_forward.py`) calls the
validator with `WalkForwardConfig.overall_end` immediately after
loading the config and before `init_qlib_canonical` — so a stale
config is rejected before qlib loads anything.

## Non-Goals

- **No change to the existing YAML files' content.** Operators
  who want env-var indirection set the environment variable AND
  edit the YAML (e.g. `provider_uri: "${QLIB_PROVIDER_URI:-D:/qlib_data/my_cn_data}"`).
  Shipping that as the default for the canonical configs is a
  separate operator follow-up — this PR only adds the *capability*.
- **No re-issue of existing bundles.** Bundles that predate this
  PR have no `bundle_manifest.json`; they continue to load with
  an INFO log saying "no manifest, no validation possible". A
  separate operator follow-up will hand-write manifests for the
  one machine that runs walk-forward today, and the
  data-ingestion PR (separate, future) will start emitting the
  manifest as a side-effect of bundle build.
- **No change to `main.py` (single-fold pipeline).** The walk-
  forward CLI is the entry point that processes long date ranges
  across rolling windows; that is where stale-bundle failures
  bite hardest. Adding the validator to `main.py` is a trivial
  follow-up but conceptually separate (single-fold `test_end` is
  always a fixed string in the YAML; the failure mode is
  identical to walk-forward but the blast radius is one window
  not eight).
- **No change to `_load_config`'s strict-unknown-key rejection
  contract in `scripts/run_walk_forward.py`.** Env-var expansion
  is purely a string-scalar rewrite on the post-load dict; the
  set of valid keys is unchanged.
- **No change to `provider_uri` normalization in
  `QlibRuntimeConfig`.** The env-var expansion happens at YAML
  load time, before `provider_uri` reaches `QlibRuntimeConfig`;
  the normalization pipeline (`strip → expanduser → abspath →
  realpath → normcase`) is untouched.
