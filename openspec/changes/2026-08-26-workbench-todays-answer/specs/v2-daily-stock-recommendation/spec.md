# Delta for v2-daily-stock-recommendation

## MODIFIED Requirements

### Requirement: The recommendation artifact SHALL carry its own generation context (schema v2)

`write_outputs` SHALL serialize a top-level `meta` block into
`daily_recommendation_{as_of}.json` together with `artifact_schema_version: 2`.
The block SHALL carry: `generated_at` (Asia/Shanghai ISO8601 with explicit
offset), `model_path`, the mode-specific identity fields below,
`fit_start_for_inference` / `fit_end_for_inference` (the RESOLVED window the run
actually used), `provider_uri`, `bundle_tag` (the `_fetch_integrity` identity
compact tag, or `null` when the bundle carries no identity stamp),
`bundle_built_at` (the `_fetch_integrity` stamp's `built_at` — the rebuild
nonce refreshed by every bundle build, or `null` when no stamp was read;
NEVER a fabricated timestamp), `instruments` and `topk`.

`bundle_built_at` exists because `bundle_tag` alone is NOT exact bundle
binding: the tag hashes only the calendar, so an in-place rebuild that
changes instruments or bins while leaving `calendars/day.txt` byte-identical
does not change it. Readers that bind an artifact to the current bundle
(the workbench synthesis card) compare this nonce against the current
stamp's `built_at` when both sides carry one; a producer omitting the field
would silently disable that in-place-rebuild protection, so it is part of
the meta contract, not an optional extra.

**Identity shape is mode-exclusive (XOR)**: in single-model mode the meta
SHALL carry `model_pkl_sha256` (SHA-256 of the loaded model pickle) and no
`ensemble` block; in ensemble mode the meta SHALL carry
`meta.ensemble.manifest_sha256` (plus the member triple listing) and SHALL
NOT carry `model_pkl_sha256` — that field's semantics stay reserved for the
single-model pickle digest, which the decision page cross-checks against the
trainer sidecar (see "ensemble 工件身份字段语义"). An artifact carrying both
or neither identity is malformed. The meta SHALL be assembled inside `recommend()` (the single place
that knows the resolved window, model and bundle) and carried on
`DailyRecommendationResult.run_meta` as a REQUIRED field — no default value, so
every constructor (including tests) is forced to supply it rather than silently
omitting context. The buy-list CSV and the scored-audit CSV are unchanged.

#### Scenario: a run_meta missing the nonce is refused at serialization

- **GIVEN** a `DailyRecommendationResult` whose `run_meta` lacks the
  `bundle_built_at` key (or carries a non-str, non-null value)
- **WHEN** `write_outputs` runs
- **THEN** it raises rather than emitting a schema-v2 artifact readers would
  mistake for a legitimate pre-nonce file

#### Scenario: the rebuild nonce rides the meta block

- **GIVEN** a bundle whose `_fetch_integrity.json` was read during
  `recommend()`
- **WHEN** `write_outputs` serializes the artifact
- **THEN** `meta.bundle_built_at` equals that stamp's `built_at`

#### Scenario: an unstamped bundle records a null nonce

- **GIVEN** a bundle with no readable integrity stamp (accepted via the
  explicit holey override)
- **WHEN** the artifact is serialized
- **THEN** `meta.bundle_built_at` is `null` — never a fabricated timestamp
