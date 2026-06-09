# v2-daily-stock-recommendation Specification (delta)

## ADDED Requirements

### Requirement: Recommendation SHALL refuse a bundle built from a holey fetch

`recommend` SHALL refuse to emit a buy list from a price/feature bundle that was
built from a HOLEY tushare fetch, or that lacks a fetch-integrity stamp, unless the
operator explicitly opts in. Right after the staleness guard, it SHALL read the
bundle's `_fetch_integrity.json` stamp (written by the qlib bin builder) from the
SAME normalized `provider_uri` qlib initialized against (so an `~`-prefixed or
whitespaced URI is not read from a non-existent literal path): a stamp marked
`built_from_holey_fetch`, OR a MISSING stamp (completeness cannot be confirmed —
e.g. a bundle built before this contract existed), SHALL raise
`DailyRecommendationError` rather than rank a list on survivorship-incomplete data,
unless `allow_holey_recommend` (`--allow-holey-recommend`) is set. This decision
SHALL be INDEPENDENT of the build-side `--allow-holey-fetch`: the stamp carries the
FACT that the fetch was holey, never the authorization to trade on it, so building
a partial bundle SHALL NOT by itself permit recommending from it. A clean stamp
SHALL pass silently.

#### Scenario: a holey-stamped bundle refuses recommendation
- **WHEN** the bundle's stamp is `built_from_holey_fetch = true` and
  `allow_holey_recommend` is not set
- **THEN** `recommend` raises rather than emitting a list

#### Scenario: an unstamped bundle refuses recommendation
- **WHEN** the bundle has no fetch-integrity stamp and `allow_holey_recommend` is
  not set
- **THEN** `recommend` raises (completeness cannot be confirmed)

#### Scenario: a clean bundle recommends normally
- **WHEN** the bundle's stamp is `built_from_holey_fetch = false`
- **THEN** the gate passes silently and recommendation proceeds

#### Scenario: the override permits an intentional holey run
- **WHEN** `allow_holey_recommend` is set
- **THEN** the gate passes regardless of a holey or missing stamp

#### Scenario: red line — the build override does not sanction recommendation
- **WHEN** a bundle was built under the build-side `--allow-holey-fetch` (so it is
  stamped `built_from_holey_fetch = true`) and recommendation runs WITHOUT
  `--allow-holey-recommend`
- **THEN** `recommend` still refuses — build-allow never cascades into
  recommend-allow; each boundary opts in on its own
