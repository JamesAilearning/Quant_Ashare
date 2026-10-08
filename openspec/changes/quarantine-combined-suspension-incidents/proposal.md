## Why

The real 2026-10-08 Tushare probe simultaneously omitted the eight known 688766 history keys and returned both R and S for 688005 on 20260116. Neither existing alternative policy represents and isolates both identified problems; the legacy omission rule does not adjudicate added rows outside its own incident. The operator explicitly approved temporary exclusion of both securities and isolation of only these identified records.

## What Changes

- Add one closed, opt-in policy for exactly this combined payload; keep both existing policies unchanged.
- Preserve original and current bytes, strict retention elsewhere, durable publication/recovery and incomplete-data reporting.
- Exclude exactly SH688005 and SH688766 before daily Top-K without changing scores, holdings, model, universe or trading cadence.
- Introduce plural instrument disclosure only for the new policy; preserve old single-instrument artifacts and refuse cross-policy/shape mismatches.
- Keep historical certification and implicit/UI authorization prohibited.

## Capabilities

### New Capabilities

- `combined-suspension-isolation`: Exact two-security acquisition evidence and daily-serving exclusion under explicit authorization.

### Modified Capabilities

None. The two earlier policy IDs and their accepted payloads remain unchanged.

## Impact

Suspension contract, acquisition publication checks, policy-derived serving/export/read-only UI disclosure, CLI choice lists and synthetic tests. No new dependency, broad waiver, future payload reconciliation, production switch or scheduler enablement in this PR. Live production acceptance remains a separate required operation after clean review and merge.
