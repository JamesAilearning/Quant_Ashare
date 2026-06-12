# Tasks: fetch-error-classification

## 1. Implementation
- [x] `client.py`: `KIND_*` constants; `TushareClientError(message, *,
      kind=None)` (legacy constructions keep working, `kind=None`).
- [x] `client.py`: `classify_tushare_failure` with documented precedence
      (specific quota phrases → auth → param → broad rate tokens → network →
      5xx → unknown), operating on the RAW `type(exc).__name__: exc` text.
- [x] `client.py`: `call()` drops the "Common causes…" suffix, preserves the
      vendor text verbatim, stamps `kind` on every failure path (SDK
      exception / None return / unknown API / SDK-missing /
      `from_environment` no-token).
- [x] `fetcher.py`: `_is_retryable_error` keys on `kind` first
      (`_RETRYABLE_KINDS = {rate_limit, network, server_error}`; `unknown`
      and all operator-action kinds are non-retryable); substring sets kept
      verbatim as the `kind=None` fallback.

## 2. Tests
- [x] `tests/data_pipeline/test_client.py` (new): table-driven
      `classify_tushare_failure` over REAL-form Tushare bodies (Chinese quota
      message containing "权限" → `rate_limit`; permission / token / 积分 →
      `auth`; param; transport type-names → `network`; 5xx; unknown), plus
      precedence traps; `call()` wrap fidelity (vendor text verbatim, NO
      "Common causes", correct kind per failure path); legacy construction
      carries `kind=None`.
- [x] `tests/data_pipeline/test_fetcher.py`: kind beats retryable-looking
      message; retryable kinds retry regardless of message; non-retryable +
      `unknown` kinds do not retry; `kind=None` falls back to substrings
      (both directions); REAL wrapped quota message → retryable; REAL wrapped
      permission message → non-retryable.
- [x] Fast-abort acceptance: token-invalid and permission failures abort the
      WHOLE multi-endpoint run on the FIRST call — exactly one client call,
      zero holes, zero backoff sleeps. Contrast pair: same run shape with a
      quota error retries to exhaustion and records a hole instead.
- [x] Blind-spot closure: the new tests construct errors in the WRAPPED
      production shape (via `classify_tushare_failure` / the real
      `client.call`), not only bare vendor strings.

## 3. Verification
- [x] `python -m unittest tests.data_pipeline.test_client` — 10 tests green.
- [x] `python -m unittest tests.data_pipeline.test_fetcher` — 54 tests green
      (zero regressions in the pre-existing 45).
- [x] `tests.data_pipeline.test_fetch_manifest` + `test_daily_update` — 50
      tests green (adjacent consumers unaffected).
