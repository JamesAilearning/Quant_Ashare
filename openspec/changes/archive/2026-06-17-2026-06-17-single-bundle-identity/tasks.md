# Tasks: single-bundle-identity (PR-G+I)

## 0. Step 0 — diagnosis (read-only) — DONE
- [x] Mapped the 3 disjoint identity surfaces on current main; confirmed
      `save_manifest` zero prod callers, freshness no-op, resume has no identity
      input, UI reads never-written files. `_fetch_integrity.json` carries only
      holey-fetch provenance today (corrected the audit's assumption).

## 1. Implementation
- [x] `BundleIdentity` optional within schema v1 (writer + reader + dataclass).
- [x] `qlib_bin_builder` stamps identity from staging bytes pre-swap.
- [x] `read_bundle_tag` integrity branch FIRST, content_hash RECOMPUTED.
- [x] `_resolve_bundle_freshness` (prefer integrity, fall back to manifest);
      `validate_test_end_against_bundle` + `verify_content_hash` re-pointed;
      SKIP bypass preserved; malformed tail_date degrades (no raise).
- [x] resume fingerprint: `compute_config_fingerprint(bundle_identity=...)` +
      `FoldManifest.from_fold` forward + engine `_resolve_bundle_identity`
      (same tag as cache key; "unknown"/None not folded in).
- [x] UI banner prefers `_fetch_integrity` identity (tail_date + count).

## 2. Tests
- [x] writer/reader roundtrip + legacy-v1-no-identity reads None + malformed
      fails loud + gate back-compat (clean+identity reads not-holey).
- [x] read_bundle_tag prefers identity (recomputed) / no-identity → unknown.
- [x] resume: real identity changes fingerprint; "unknown"/None unchanged;
      sentinel-matches-`_LEGACY_BUNDLE_TAG` coupling pin.
- [x] WF freshness lights up from identity (stale → raises, in-window → passes).
- [x] `_resolve_bundle_freshness` precedence (integrity wins / manifest-only /
      malformed-degrades / none → None).
- [x] BUILD-PATH end-to-end: `QlibBinBuilder.build()` stamps identity
      (tail/start/end/count/content_hash) — extended the builder test.
- [x] Lockstep governance pin: same-window different calendar bytes → cache tag
      AND resume fingerprint both change.

## 3. Verification
- [x] ruff + mypy --strict + full fast suite (2555 passed).
- [x] Pre-push 3-skeptic adversarial self-review; P2/P3 findings fixed
      (recompute hash, UI precedence, fallback tests, docstring, date-in-try).

## Follow-ups (not this PR)
- Retire the dead `save_manifest` writer + `bundle_manifest.json` reader path.
- Add the WF-freshness check to UI-launched WF jobs (call-site coverage).
