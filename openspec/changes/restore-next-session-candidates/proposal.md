## Why

The current recommendation command requires already-recorded entry-day bars and therefore emits a retrospective confirmation list, not next-session candidates. The user explicitly approved changing this timing boundary on 2026-10-12 to restore genuine daily decision support without changing models, CSI800, Top50, N5, holdings, or automatic trading.

## What Changes

- **BREAKING**: use the latest fully closed price session T as the default decision date, and obtain the next session from an explicitly fetched, provenance-bound Tushare SSE schedule. Never extend the qlib price calendar or fabricate future prices.
- Keep one recommendation runtime: score only features dated at or before T, filter known T-day microstructure plus current ST and approved quarantine evidence, and emit candidates whose entry-day tradability is explicitly unconfirmed.
- **BREAKING**: publish recommendation schema v3 with explicit candidate mode, unconfirmed entry status, nullable entry tradability, and separately named as-of eligibility. Migrate CLI, CSV/JSON, operator readers, and tests together; legacy v2 remains historical read-only and cannot be promoted to current candidates or nominal holdings.
- Require explicit schedule availability, structural completeness, freshness, and price/schedule date consistency before model loading. Missing or stale evidence fails loudly, without a weekday approximation or fallback to retrospective behavior.
- Preserve the certified ensemble, training/normalization windows, ranking, Top50, N5 anchored on T, quarantine evidence, and manual-only recommendation boundary. No claim that a candidate is executable or that backtest returns certify live execution.

## Capabilities

### New Capabilities

- `recommendation-session-schedule`: explicit Tushare SSE schedule acquisition and read-only, provenance-bound session resolution, separate from price/provider data.

### Modified Capabilities

- `v2-daily-stock-recommendation`: latest-closed T to planned T+1 candidates, no entry-day bar reads, schema v3 and unknown execution conditions.
- `v2-daily-decision-page`: distinguish current unconfirmed candidates from legacy historical confirmations and prevent nominal-holding promotion.

## Impact

Affected: inference date resolution, the manual recommendation CLI and operator command/reader boundaries, new bounded schedule acquisition, persisted recommendation schemas, synthetic tests, and runbooks. Pipeline/WalkForward reports and official qlib-native metrics are unchanged. Existing raw fetch manifests, provider calendars, price files, model assets, production checkout, scheduler state, and successful fidelity receipts are not modified by implementation or tests. Work occurs in a separate Git worktree so frozen recovery evidence remains inspectable.
