## Why

A supervised production catch-up requested a wider suspend-history range but
replaced all 5,000 retained records with a different 5,000-record slice. Request
coverage and atomic file replacement did not detect a partial vendor response.

## What Changes

- Fetch `suspend_d` in bounded, sequential date partitions; split potentially
  saturated responses down to a day and refuse unresolved saturation. Reject
  a potentially saturated `namechange` response without an unverified fallback.
- Validate response schema and the endpoint's actual filtering date before
  publication. Name changes use announcement dates, not effective dates; retain
  legitimate missing announcement values without claiming all history was fetched.
- **BREAKING**: a refresh may no longer erase old aggregate business records,
  including by replacing nonempty history with an empty response. Permit only
  explicit name-change end-date corrections for otherwise identical records.
- Publish once, only after every partition and preservation check succeeds.
  Keep the stable `file` hole, manifest schema, request bounds, and existing
  provenance/skip/dry-run protections.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `v2-ashare-survivorship-correction`: aggregate acquisition must reject
  observable truncation and lost history before publication.

## Impact

Scoped to the two aggregate fetch producers, their synthetic tests and data
acquisition documentation. No new dependency, CLI option, persisted field or
Pipeline/WalkForward schema change. Local and production data remain untouched
by implementation/tests; production rebuild and acceptance are separate gates.

Non-goals: changing other endpoint policies, selecting a stock universe,
unverified pagination fallback, silently merging retained data, automatic raw
repair or deployment, models, recommendation rules, trading and UI. Successful
partition checks are not proof that the vendor has disclosed every real event.
Reconstructing complete name history (including never-observed records without
announcement dates) requires separately verified acquisition semantics.
