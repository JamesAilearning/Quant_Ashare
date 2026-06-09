# Tasks: gate-downstream-on-holey-fetch

## 1. Implementation
- [x] `src/data/pit/bundle_integrity.py`: `BundleIntegrity` + `write_bundle_integrity`
      (atomic temp + `os.replace`, injectable `now`) + `read_bundle_integrity`
      (missing → None; malformed / non-object / non-UTF-8 / unknown-schema /
      missing-field → `BundleIntegrityError`). Stamp = `{bundle}/_fetch_integrity.json`.
- [x] `src/data/tushare/fetch_manifest.py`: `all_holes(m)` + `is_complete(m)`.
- [x] Layer 1: `QlibBinBuilder(__init__ ..., allow_holey_fetch=False)`; `build()`
      reads `{tushare_dir}/fetch_manifest.json`, refuses (QlibBinBuilderError) a
      holey OR missing manifest unless allowed, and stamps the bundle's integrity
      (clean / holey + holes) inside staging (promoted atomically).
      `05_build_qlib_bins.py`: `--allow-holey-fetch`.
- [x] Layer 2: `RecommendationConfig.allow_holey_recommend=False`;
      `recommend()` calls `_assert_bundle_fetch_complete` after the staleness guard
      — refuses a holey or missing stamp unless allowed. `daily_recommend`
      CLI: `--allow-holey-recommend`.
- [x] The two overrides are INDEPENDENT — the stamp carries the fact, not the
      authorization.

## 2. Tests (mock + synthetic stamps / manifests, no real fetch / qlib bundle)
- [x] STAMP CONTRACT: write→read round-trip (clean + holey, fields match);
      injected timestamp; atomic (no `.tmp`); missing → None; non-object /
      non-UTF-8 / unknown-schema / missing-field → `BundleIntegrityError`.
- [x] LAYER 1: holey manifest → build raises; missing manifest → build raises;
      complete → builds + stamp clean; holey + `allow_holey_fetch` → builds +
      stamp holey + holes. (Existing builder-logic tests seed a complete manifest.)
- [x] LAYER 2: holey stamp → recommend gate raises; `allow_holey_recommend` →
      passes; missing stamp → raises; `allow` → passes; clean → passes silently.
- [x] RED LINE (non-transitive): a bundle stamped built-from-holey-fetch (the
      build override) is STILL refused at the recommend gate without
      `allow_holey_recommend`.

## 3. Verification
- [x] `pytest` for the three touched test files green (bundle_integrity 9,
      qlib_bin_builder incl. FetchGate, daily_recommend incl. HoleyGate).
- [x] Full fast suite green (2372 passed); `ruff` + `mypy --strict` clean.
