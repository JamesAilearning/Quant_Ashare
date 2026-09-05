## Context

The registered ensemble gate covers 2026-05-06..2026-07-31. The committed m3
preset and member gate both give valid=2026-04-07..2026-07-07. These intervals
overlap at 2026-05-06..2026-07-07. The original re-anchor decision referred to
being outside the three **training** windows; the runbook dropped that qualifier.
The existing daily-recommendation baseline permits a behavior dry run to overlap
training and explicitly excludes net returns from the per-retrain gate.

## Goals / Non-Goals

**Goals:** correct the first-launch operator card where the incorrect claim is
made; disclose validation/early-stopping exposure and the evidence boundary;
protect that disclosure with committed-evidence-only governance tests.

**Non-Goals:** changing dates, thresholds, models, PASS artifacts, certification
or runtime; editing archived decisions; declaring any shorter period independently
unseen; calculating returns; establishing a new forward-test business policy.

## Decisions

1. State that the dry run follows the three train windows but is not independent
   unseen-data performance validation. Show m3's valid interval and its overlap.
   Merely adding the word "training" would still leave the material validation
   exposure implicit, so include the reason: early stopping/model selection.
2. Keep the pinned CLI window and gate outputs unchanged. The correction is about
   interpretation, not a retroactive rejection or redefinition of the protocol.
3. Explicitly distinguish m3's registered 2026-07-10..2026-07-31 embedded daily
   diagnostic from ensemble certification. A date after valid_end alone does not
   establish an untouched evaluation period or independently frozen decisions.
4. Read committed YAML and gate JSON in governance tests, derive the overlap, and
   require the matching dates and evidence links in the specific operator step.
   Do not import a training engine or access live artifacts. Existing preset pins
   remain the authority for the dates; no duplicate runtime constant is added.

## Risks / Trade-offs

- A wording test cannot prove all future prose is scientifically correct → bind
  the key disclosure to the local step and actual evidence, plus human review.
- Historical PASS could be mistaken for newly certified performance → preserve
  its bytes and explicitly retain the canonical campaign/annual-recertification
  authority; this PR neither certifies nor revokes production.
- Readers may infer that trimming the overlap creates clean OOS → explicitly
  reject that inference; no replacement OOS period is selected here.
