# v2-ashare-survivorship-correction Specification (delta)

## ADDED Requirements

### Requirement: Fetch failures SHALL carry a structured classification that retry policy keys on

`TushareClient` SHALL classify every failure it wraps into a structured kind
(`rate_limit` / `network` / `server_error` / `auth` / `param` /
`environment` / `unknown`) derived from the RAW underlying failure — the
exception type name plus the vendor's own message — BEFORE any wrapper prose
is added, and SHALL preserve the original vendor error text verbatim in the
wrapped message (no generic "common causes" suffix). Classification
precedence SHALL rank Tushare's specific quota phrases (e.g.
"每分钟最多访问") above auth/permission tokens, because the genuine
rate-limit body also contains "权限" and misclassifying it as `auth` would
abort multi-hour runs on a routine transient.

`TushareFetcher` retryability SHALL key on the structured kind when present:
`rate_limit` / `network` / `server_error` are retryable; `auth` / `param` /
`environment` are non-retryable (operator action, not time); `unknown` is
non-retryable (an unrecognized failure aborts fast and loud rather than
burning the retry budget on every unit). Message-substring matching SHALL be
used ONLY for a `TushareClientError` that carries no kind (legacy / direct
constructions), with the pre-existing substring semantics unchanged.

A non-retryable failure SHALL abort the whole fetch run on the first failing
call — no retry attempts, no backoff sleeps, no hole recorded — preserving
the P3-4a fast-abort contract that the wrapper-prose substring matching had
made unreachable.

#### Scenario: invalid token aborts the run on the first call
- **WHEN** the client wraps a vendor failure whose raw text is a token /
  permission error (e.g. "token无效", "抱歉，您没有访问该接口的权限")
- **THEN** the wrapped error carries `kind=auth`, `fetch()` re-raises it on
  the FIRST call with zero retries, zero backoff sleeps, and zero holes

#### Scenario: the real quota message stays retryable despite containing 权限
- **WHEN** the raw vendor text is Tushare's rate-limit body
  ("抱歉，您每分钟最多访问该接口500次，权限的具体详情访问：…")
- **THEN** the wrapped error carries `kind=rate_limit` and the fetcher
  retries with backoff, recording a hole on exhaustion (P3-4a behavior)

#### Scenario: a legacy unclassified error falls back to substring matching
- **WHEN** `_is_retryable_error` receives a `TushareClientError` constructed
  without a kind
- **THEN** the original substring sets decide retryability, unchanged in both
  directions (bare "rate limit…" retries; bare "token无效" does not)
