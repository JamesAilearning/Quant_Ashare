# Tasks: persist-fetch-manifest

## 1. Implementation
- [x] `src/data/tushare/fetch_manifest.py`: `FetchManifest` / `EndpointCoverage`
      + the `fetch_manifest.json` schema (`schema_version` / `fetched_at` /
      per-endpoint `status` / `coverage_end_date` / `units_written` / `holes[]`).
- [x] `build_manifest(results, holes, start_date, end_date, *, now=None)` —
      injectable timestamp (Phase 2 value-injection pattern; default
      `datetime.now(tz=utc)`); records `coverage_start_date` + `coverage_end_date`.
- [x] `read_manifest` (missing → None / unknown schema_version → fail-loud / codex
      P2: missing required field → fail-loud) + `write_manifest` (atomic temp +
      `os.replace`) + `clear_manifest`.
- [x] `merge_manifest(prev, current)` — self-heal: re-resolve each ran endpoint
      from `current` (healed holes dropped, recurring holes kept with attempts
      accumulated, coverage spans widest); preserve non-run endpoints untouched.
- [x] codex P1: REFUSE a narrower-scope merge of a date-scoped endpoint that still
      has unresolved holes — fail-loud, never drop out-of-range holes; `stock_basic`
      exempt (date-agnostic); a hole-free narrower run is allowed.
- [x] codex P1-B: coverage reflects what was ACTUALLY fetched — a run that wrote
      nothing for an endpoint (all files skipped by resume, e.g. a wider run that
      skips a prior narrow aggregate file) does NOT advance its coverage; only a
      run that wrote data advances it.
- [x] `01_fetch_tushare.main`: on the completed-run path (not `--dry-run`), read
      prev → build current (start+end) → merge → write atomically.
- [x] codex P2: the whole read/build/merge/write is inside the CLI's
      `FetchManifestError` catch, so a refused narrower-scope merge surfaces as a
      clean non-zero exit, not a traceback; `read_manifest` rejects a non-object
      JSON document (e.g. `[]`) as `FetchManifestError`.

## 2. Tests (mock + synthetic manifests, no real fetch)
- [x] WRITE: fields correct + injected timestamp; write→read roundtrip.
- [x] ATOMIC: a failed `os.replace` leaves the prior manifest intact + valid
      (no half-written file); no `.tmp` left after success.
- [x] READ: missing → fresh (None, no error); unknown / missing `schema_version`
      and malformed JSON → `FetchManifestError`; (codex P2) missing `endpoints`
      member or per-endpoint key → `FetchManifestError`.
- [x] MERGE: self-healed hole dropped; un-healed hole kept with attempts
      accumulated; coverage advances on a wider run.
- [x] MERGE RED LINE — two INDEPENDENT counter-examples: (误删) an endpoint that
      did NOT run keeps its holes; (赖着) a healed hole does not linger while a
      still-failing sibling unit is kept.
- [x] MERGE SCOPE (codex P1): a narrower-scope date-scoped merge with holes is
      refused (incl. `namechange`, P1-A); a same-or-wider one self-heals;
      `stock_basic` narrower and a hole-free narrower run are NOT refused.
- [x] COVERAGE (codex P1-B): a wider run that SKIPS a prior narrow aggregate file
      keeps the actually-fetched coverage (no over-claim); a run that wrote data
      advances coverage.
- [x] CLI FAIL-LOUD (codex P2): a non-object manifest fails loud; `main` returns
      `1` (not a traceback) when a narrower-scope rerun makes the merge refuse.
- [x] CLEAR: removes the manifest (→ fresh); no-op when absent.
- [x] INTEGRATION: through `01.main` twice — run 1 records the hole (exit 3),
      run 2 (unit now succeeds) self-heals it (exit 0), asserting the hole left
      no file/`.tmp` and run 2 re-fetches ONLY the holed unit (real resume).

## 3. Verification
- [x] `pytest tests/data_pipeline/test_fetch_manifest.py` green (29 tests).
- [x] Full fast suite green; `ruff` + `mypy --strict` clean.
