# Add PIT Universe Foundation

## Why

The current Tushare-backed qlib provider violates Point-in-Time correctness in
two ways:

- **Ticker reuse contamination** — the same market code (e.g. `SH600753`) maps
  to different company entities over time (庞大集团 → ST友谊). Today's bins
  splice the two together, so a `ts_mean(close, 20)` operator at the second
  entity's day 5 reads through the first entity's last 15 days.
- **Survivorship bias** — delisted entities without a subsequent ticker reuse
  drop out of the panel entirely.

Both produce false-positive alpha. The §2 survivorship verification (see
`docs/pit/pit_universe_design.md`) documented 4 ticker-reuse contaminations
plus 3 missing delistings on the current provider.

A prior Phase A.1 implementation attempt was committed and reverted twice
(commits `cc96e5d` → `3f9f132`, `058cd6b` → `6720a5e`) because it skipped the
governance / contract foundation step. This proposal restores the
foundation-first sequence required by `AGENTS.md`: define the capability
contract first, then implement against it.

## What Changes

- Add `v2-pit-universe-foundation` capability spec covering:
  - Entity-vs-ticker identity model and entity registry schema invariants.
  - NaN-gap separation rule between entity periods in qlib bin storage.
  - Adjusted-price PIT caveat: `adj_factor` is non-PIT; features SHALL be
    within-entity ratios / returns only.
  - qlib operator `min_periods` validation contract (Stage 6.D test).
  - PIT query layer behavior: `get_universe(date)`, `get_features(...)`,
    `resolve_entity(ticker, date)`.
  - Bounded LRU cache requirement on the PIT query layer.
  - Migration safety: legacy provider preserved untouched, new provider
    written to a separate directory; destructive scripts gated by explicit
    flag.
  - Out-of-scope PIT dimensions explicitly listed (Phase E+ backlog).
  - Reference cases YAML governance: user-curated seed; agent additions
    require per-row Tushare API citation.
- Copy the canonical design document into `docs/pit/pit_universe_design.md`
  so all follow-up PRs reference a single in-repo source.
- Add `tasks.md` checklist covering Phase 0 (this PR) and skeleton checkboxes
  for Phases A-D (each phase = one or more follow-up PRs).

## Non-Goals

- No ingestion script, entity-resolution algorithm, bin builder, or PIT query
  implementation in this PR — those are Phase A-C deliverables.
- No reference-cases YAML seed in this PR. Per the design doc §11 Phase 0.2
  and §14 point 7, the first ≥10 reference cases are user-curated; the agent
  does NOT auto-generate the seed.
- No changes to the existing `v2-universe-data-contract` spec. That
  capability governs artifact validation (provenance, snapshot_at, schema
  mismatch); the new capability governs entity-model / PIT-bin / PIT-query
  semantics. They are complementary.
- No PIT support for industry classification, fundamentals, share count, ST
  status, or risk-model exposures. Each is tracked as Phase E+ backlog in the
  design doc §4.5.
- No deletion or overwrite of the existing `D:/qlib_data/my_cn_data`
  provider.
