## Context

`FeatureDatasetBuilder` validates `feature_handler` against a one-item tuple
and constructs `qlib.contrib.data.handler.Alpha158` through an if branch. The
runtime behavior is explicit, but extension requires editing builder internals.

## Goals / Non-Goals

**Goals:**

- Replace the if branch with a registry of named handler factories.
- Keep `Alpha158` as the only default registered handler unless qlib exposes
  additional handlers and tests cover them.
- Let tests and future runtime code register handlers without touching the
  builder.

**Non-Goals:**

- No automatic import of arbitrary dotted paths from config.
- No default switch to Alpha360.
- No change to dataset segment semantics.

## Decisions

1. **Use callable factories instead of config dotted imports.**

   Factories keep construction explicit and testable. Arbitrary dotted imports
   from user config would blur runtime dependency and security boundaries.

2. **Expose registration helpers from the data layer.**

   `register_feature_handler`, `list_supported_feature_handlers`, and
   `reset_feature_handler_registry_for_tests` provide a narrow extension and
   test boundary without requiring core runtime modules to know qlib internals.

3. **Resolve qlib handler imports lazily.**

   The default Alpha158 factory imports qlib only at build time, preserving
   import-time behavior for modules that only inspect config.

## Risks / Trade-offs

- **Risk: global registry leaks test registrations** -> Mitigation: provide a
  test reset helper and use it in tests.
- **Risk: config names imply official endorsement** -> Mitigation: only
  registered names are accepted and unsupported names fail loudly.
- **Risk: Alpha360 requires different defaults** -> Mitigation: leave it to a
  future explicit registration/config change.

## Migration Plan

1. Add registry helpers and default Alpha158 registration.
2. Update `FeatureDatasetBuilder` to construct handlers through the registry.
3. Add tests for defaults, custom factories, and unknown handler errors.
