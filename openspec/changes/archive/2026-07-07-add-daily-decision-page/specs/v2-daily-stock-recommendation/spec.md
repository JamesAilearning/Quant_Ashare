## ADDED Requirements

### Requirement: The recommendation artifact SHALL carry its own generation context (schema v2)

`write_outputs` SHALL serialize a top-level `meta` block into
`daily_recommendation_{as_of}.json` together with `artifact_schema_version: 2`.
The block SHALL carry: `generated_at` (Asia/Shanghai ISO8601 with explicit
offset), `model_path`, `model_pkl_sha256` (SHA-256 of the loaded model pickle),
`fit_start_for_inference` / `fit_end_for_inference` (the RESOLVED window the run
actually used), `provider_uri`, `bundle_tag` (the `_fetch_integrity` identity
compact tag, or `null` when the bundle carries no identity stamp), `instruments`
and `topk`. The meta SHALL be assembled inside `recommend()` (the single place
that knows the resolved window, model and bundle) and carried on
`DailyRecommendationResult.run_meta` as a REQUIRED field — no default value, so
every constructor (including tests) is forced to supply it rather than silently
omitting context. The buy-list CSV and the scored-audit CSV are unchanged.

#### Scenario: a fresh run writes a self-describing artifact
- **WHEN** `recommend()` completes and `write_outputs` persists the JSON
- **THEN** the JSON contains `artifact_schema_version: 2` and a `meta` block
  whose `fit_end_for_inference` equals the window the run resolved (CLI flag or
  model meta), and whose `model_pkl_sha256` equals the SHA-256 of the pickle
  that produced the scores

#### Scenario: a bundle without an integrity stamp does not fake an identity
- **WHEN** the provider bundle carries no `_fetch_integrity` identity
- **THEN** `meta.bundle_tag` is `null` — never a fabricated or defaulted tag

#### Scenario: legacy artifacts remain readable, distinguishably
- **WHEN** a reader loads a pre-v2 JSON (no `meta`, no `artifact_schema_version`)
- **THEN** parsing succeeds and the absence is DETECTABLE (readers can branch on
  the missing block); readers surfacing generation context SHALL warn rather
  than substitute defaults
