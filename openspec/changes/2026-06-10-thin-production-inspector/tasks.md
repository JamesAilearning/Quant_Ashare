# Tasks: thin-production-inspector

## 1. Implementation
- [x] `web/operator_ui/pages/data_inspect.py` (数据检视): read-only — integrity
      stamp (clean / holey + holes table / missing / corrupt, each with the
      operator consequence), bundle-health summary, on-demand thin 06 validator
      run rendered as a checks table + per-check expanders. Registered in the
      app navigation under 分析.
- [x] `docs/operations-env-vars.md`: the five operational env vars
      (QUANT_PROVIDER_URI / QUANT_MODEL_PATH / QUANT_DELISTED_REGISTRY /
      QUANT_NAME_SOURCE / TUSHARE_TOKEN) — consumers, defaults, precedence,
      PowerShell `$env:` spelling, the `${…}` loader-vs-PowerShell trap.
- [x] CLAUDE.md: stale `${QLIB_PROVIDER_URI}` → real `${QUANT_PROVIDER_URI}`
      (+ `:-default` form) + pointer to the doc.
- [x] 01–06 path residue audit: P3-6a Step 0 verified all six scripts are
      pure-argparse with explicit paths — nothing to change.

- [x] codex P2: a qlib singleton re-init for a DIFFERENT provider_uri in the
      same UI process (QlibRuntimeInitError) renders a controlled error with a
      restart instruction instead of crashing the read-only page.

## 2. Tests
- [x] GOVERNANCE (red line): page source contains NO write-side filesystem API;
      NO import of builder / fetcher / orchestrator machinery (import-line
      scan); 检视生产 + 只读 copy present; page registered in navigation.

## 3. Verification
- [x] Governance test green; full fast suite + pit green; CI-scope ruff +
      mypy clean; openspec validate --strict.
