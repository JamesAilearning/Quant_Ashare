# Proposal: fetch-error-classification

## Why

P3-4a's design splits Tushare failures into retryable (rate limit / network /
5xx → backoff, then hole) and non-retryable (token / permission / param →
abort the whole run fast, because every remaining unit would fail
identically). The audit (docs/audit_rebase_20260611.md, B1) found the
non-retryable path is UNREACHABLE in production: `TushareClient.call` appends
the generic suffix "Common causes: rate limit (account tier too low), missing
parameter, or transient network error." to EVERY wrapped exception, and
`TushareFetcher._is_retryable_error` is a substring match over the message —
so "rate" / "limit" / "network" always match and every failure classifies as
retryable. A wrong token or a missing endpoint permission therefore grinds
through 5 × 60-300 s of backoff per unit; across the per-`(ticker, year)`
loops (~5800 tickers × 26 years × 3 endpoints) that is a multi-day stall
producing tens of thousands of holes instead of a first-call abort. The
existing tests construct `TushareClientError` with BARE vendor-style messages
— precisely the shape production never sees — so the suite cannot catch it.

The root cause is in the WRAPPER layer, so that is where the fix goes.

## What Changes

- `src/data/tushare/client.py`:
  - `TushareClientError` gains a structured `kind` attribute (`rate_limit` /
    `network` / `server_error` / `auth` / `param` / `environment` /
    `unknown`; `None` = unclassified legacy construction).
  - New `classify_tushare_failure(error_text)` classifies the RAW underlying
    failure (`f"{type(exc).__name__}: {exc}"`) BEFORE wrapping, with explicit
    precedence: specific quota phrases first (Tushare's real rate-limit body
    also contains "权限"), then auth/permission/tier, then param, then broad
    legacy rate tokens, then network, then 5xx; anything else is `unknown`.
  - `call()` stops appending the "Common causes…" prose — the original vendor
    error text is preserved verbatim — and stamps `kind` on every failure
    path (SDK exception, `None` return → `rate_limit`, unknown API name →
    `param`, SDK missing / no token in env → `environment`).
- `src/data/tushare/fetcher.py`:
  - `_is_retryable_error` keys on `kind` when present: retryable =
    {`rate_limit`, `network`, `server_error`}; everything else — including
    `unknown` — is non-retryable (the 4a stance: an unrecognized failure
    aborts fast and loud rather than burning the retry budget per unit).
  - The original substring sets remain ONLY as a fallback for a
    `TushareClientError` carrying no kind (legacy / direct constructions).
  - The retry/backoff schedule and the abort wiring are unchanged — only the
    classification mechanism changes.

## Non-Goals

- No change to manifest handling on the hard-abort path (PR-B /
  manifest-truthfulness owns the `clear_manifest` red line).
- No retry-budget / backoff tuning.
- No new endpoint knowledge in the client; it stays a typed boundary that
  states the classified FACT — retry POLICY stays in the fetcher.
